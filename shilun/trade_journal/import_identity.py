"""Exact source-row / confirmed broker aliases for immutable journal facts.

The input digest belongs to the original normalized request. This projection is
called both in read-only preview and again under the journal write lease. It
never merges on price, quantity, time proximity, or a stripped broker ID alone.
"""
from __future__ import annotations

import base64
from collections import defaultdict
from copy import deepcopy
import hashlib
import re


VERSION = "trade-journal-import-identity-v1"
NON_CORE = {"fill_id","financial_event_id","content_hash","source_ref","name","note","inventory_order","recorded_at","_id","occurrence"}


class ImportIdentityError(ValueError):
    pass


def normalize_identity_links(value):
    if value is None:
        return []
    if not isinstance(value,list) or len(value)>50000:
        raise ImportIdentityError("identity_links须为不超过50000项的明确原行确认数组")
    output,seen=[],set()
    for link in value:
        if not isinstance(link,dict):raise ImportIdentityError("成交身份确认须为对象")
        ref=link.get("incoming_source_ref")
        if not isinstance(ref,dict) or not isinstance(ref.get("source_id"),str) or not ref["source_id"] or type(ref.get("row")) is not int or ref["row"]<1 or not isinstance(ref.get("sheet"),(str,int)) or isinstance(ref.get("sheet"),bool):
            raise ImportIdentityError("成交身份确认须明确incoming_source_ref的source_id/sheet/正原行号")
        key=(ref["source_id"],ref["sheet"],ref["row"])
        if key in seen:raise ImportIdentityError("同一原行不能重复确认身份")
        seen.add(key)
        if not isinstance(link.get("existing_fact_id"),str) or not link["existing_fact_id"] or not re.fullmatch(r"[0-9a-f]{64}",str(link.get("expected_existing_hash") or "")):
            raise ImportIdentityError("成交身份确认须有既有fact ID及精确expected_existing_hash")
        if link.get("identity_confirmation") is not True or not isinstance(link.get("reason"),str) or not link["reason"].strip() or len(link["reason"])>4000:
            raise ImportIdentityError("成交身份必须由用户明确确认并填写不超过4000字的理由")
        output.append({"incoming_source_ref":{k:deepcopy(ref[k]) for k in ("source_id","sheet","row")},"existing_fact_id":link["existing_fact_id"],
            "expected_existing_hash":link["expected_existing_hash"],"reason":link["reason"].strip(),"identity_confirmation":True})
    return sorted(output,key=lambda r:(r["incoming_source_ref"]["source_id"],str(r["incoming_source_ref"]["sheet"]),r["incoming_source_ref"]["row"]))


def _location(ref):
    if not isinstance(ref,dict) or ref.get("sheet") is None or type(ref.get("row")) is not int:return None
    return ref.get("source_id"),ref["sheet"],ref["row"]


def _source_is_verified(source):
    try:
        content=source.get("content")
        raw=base64.b64decode(content,validate=True) if source.get("content_encoding")=="base64" else content.encode("utf-8") if source.get("content_encoding","utf-8")=="utf-8" else None
        return raw is not None and hashlib.sha256(raw).hexdigest()==source.get("sha256")
    except (ValueError,TypeError,AttributeError):return False


def _broker(row):
    return row.get("account_alias"),row.get("date"),row.get("ticker"),row.get("broker_execution_id")


def _zero_alias(left,right):
    return bool(isinstance(left,str) and isinstance(right,str) and left!=right and re.fullmatch(r"[0-9]+",left) and re.fullmatch(r"[0-9]+",right) and (left.lstrip("0") or "0")== (right.lstrip("0") or "0"))


def fact_differences(existing,incoming,*,broker_alias=False):
    differences={}
    for key in (set(existing)|set(incoming))-NON_CORE:
        before,after=existing.get(key),incoming.get(key)
        if before==after:continue
        if key=="broker_execution_id" and broker_alias and (before==after or _zero_alias(before,after)):continue
        # The old importer kept the statement clock in `time` only. Preserve
        # that canonical record; the second field is redundant source evidence.
        if key=="statement_event_time" and existing.get("time_precision")==incoming.get("time_precision")=="statement_event_time" and existing.get("time")==incoming.get("time") and (before is None and after==existing.get("time") or after is None and before==incoming.get("time")):
            continue
        # The raw IPO row does not undo a separately reviewed historical link.
        if key=="linked_ticker" and before and after is None:continue
        differences[key]={"existing":deepcopy(before),"incoming":deepcopy(after)}
    return differences


def reconcile_import(data,*,existing_sources,existing_fills,existing_financial,committed_batches):
    result=deepcopy(data)
    conflicts,candidates,proofs,source_imports=[],[],[],[]
    facts={"fills":existing_fills,"financial_events":existing_financial}
    keys={"fills":"fill_id","financial_events":"financial_event_id"}
    by_id={category:{r[keys[category]]:r for r in rows} for category,rows in facts.items()}
    old_sources={r["source_id"]:r for r in existing_sources}
    for source in data["sources"]:
        if source["source_id"] in old_sources and source["sha256"]!=old_sources[source["source_id"]].get("sha256"):
            conflicts.append({"kind":"sources","id":source["source_id"],"code":"source_id_content_conflict","message":"原来源ID被用于不同原件SHA，不能借身份映射覆盖来源"})
    source_map={**old_sources,**{r["source_id"]:r for r in data["sources"]}}
    source_shas={key:r["sha256"] for key,r in source_map.items()}
    referenced={r.get("source_ref",{}).get("source_id") for rows in facts.values() for r in rows}
    verified=defaultdict(list)
    requested_shas={r["sha256"] for r in data["sources"]}
    for source in existing_sources:
        if source.get("sha256") in requested_shas:
            if _source_is_verified(source):verified[source["sha256"]].append(source)
            else:conflicts.append({"kind":"sources","id":source["source_id"],"code":"source_archive_integrity_conflict","message":"既有同SHA原件无法从私有content复核，须审查来源完整性，不能静默替换"})
    aliases={}
    sources=[]
    for source in data["sources"]:
        matching=sorted(verified.get(source["sha256"],[]),key=lambda r:(r["source_id"] not in referenced,r["source_id"]))
        chosen=matching[0] if matching else source
        aliases[source["source_id"]]=chosen["source_id"]
        sources.append(deepcopy(chosen))
        source_imports.append({"incoming_source_id":source["source_id"],"canonical_source_id":chosen["source_id"],"sha256":source["sha256"],
            "filename":source.get("filename"),"source_type":source.get("source_type"),"evidence_class":source.get("evidence_class"),"metadata":deepcopy(source.get("metadata") or {})})
    result["sources"]=list({r["source_id"]:r for r in sources}.values())
    anchors={category:defaultdict(set) for category in facts}
    def anchor_key(ref):
        if not isinstance(ref,dict) or ref.get("source_id") not in source_shas:return None
        # JSON exports contain page-local row numbers. A record_id, when
        # supplied, is their actual stable locator and takes precedence.
        if ref.get("record_id") is not None:
            return "record_id",source_shas[ref["source_id"]],str(ref["record_id"])
        loc=_location(ref)
        return ("sheet_row",source_shas[loc[0]],loc[1],loc[2]) if loc else None
    def add_anchor(category,identifier,ref):
        key=anchor_key(ref)
        if key is not None and identifier in by_id[category]:anchors[category][key].add(identifier)
    for category,rows in facts.items():
        for row in rows:add_anchor(category,row[keys[category]],row.get("source_ref"))
    for batch in committed_batches:
        if batch.get("status")!="committed":continue
        for item in batch.get("fill_source_refs") or []:add_anchor("fills",item.get("fill_id"),item.get("source_ref"))
        for item in batch.get("financial_source_refs") or []:add_anchor("financial_events",item.get("financial_event_id"),item.get("source_ref"))
    broker_exact=defaultdict(set)
    broker_zero=defaultdict(set)
    for row in existing_fills:
        if row.get("broker_execution_id"):
            broker_exact[_broker(row)].add(row["fill_id"])
            number=row["broker_execution_id"]
            if re.fullmatch(r"[0-9]+",number):broker_zero[(*_broker(row)[:3],number.lstrip("0") or "0")].add(row["fill_id"])
    # An explicit confirmed alias is reusable only after a committed receipt.
    for batch in committed_batches:
        if batch.get("status")!="committed":continue
        for proof in batch.get("identity_reconciliations") or []:
            identifier=proof.get("canonical_fact_id");row=by_id["fills"].get(identifier)
            if row and proof.get("identity_confirmation") is True and proof.get("canonical_content_hash")==row.get("content_hash") and proof.get("incoming_broker_execution_id"):
                broker_exact[(*_broker(row)[:3],proof["incoming_broker_execution_id"])].add(identifier)
    links={_location(r["incoming_source_ref"]):r for r in data.get("identity_links") or []}
    used_links=set()
    for category,rows in (("fills",data["fills"]),("financial_events",data["financial_events"])):
        resolved=[]
        for incoming in rows:
            ref=deepcopy(incoming["source_ref"]);loc=_location(ref);identifier=incoming[keys[category]]
            row=deepcopy(incoming);kind=None;match=None;confirmed=None
            strong=anchors[category].get(anchor_key(ref),set())
            if identifier in by_id[category]:
                match=by_id[category][identifier];kind="exact_canonical_fact_id"
            elif len(strong)>1:
                conflicts.append({"kind":category,"id":identifier,"code":"source_row_identity_conflict","source_ref":ref,"message":"同一原件SHA/工作表/原行对应多个既有事实，须先审查"})
            elif strong:
                match=by_id[category][next(iter(strong))];kind="source_sha_record_id" if ref.get("record_id") is not None else "source_sha_sheet_row"
            elif category=="fills" and incoming.get("broker_execution_id"):
                exact=broker_exact.get(_broker(incoming),set())
                if len(exact)==1:match=by_id[category][next(iter(exact))];kind="exact_broker_identity"
                elif len(exact)>1:conflicts.append({"kind":category,"id":identifier,"code":"broker_execution_identity_conflict","source_ref":ref,"message":"精确券商成交身份已对应多个canonical事实，须审查"})
            link=links.get(loc)
            if link:
                used_links.add(loc)
                existing=by_id["fills"].get(link["existing_fact_id"]) if category=="fills" else None
                valid=existing and existing.get("content_hash")==link["expected_existing_hash"] and incoming.get("broker_execution_id") and (incoming["broker_execution_id"]==existing.get("broker_execution_id") or _zero_alias(incoming["broker_execution_id"],existing.get("broker_execution_id")))
                if valid and match and match[keys[category]]!=existing["fill_id"]:valid=False
                if not valid:
                    conflicts.append({"kind":category,"id":identifier,"code":"identity_confirmation_invalid","source_ref":ref,"message":"身份确认已过期、对象不符或编号并非相同/仅前导0差异；不能合并"})
                else:match=existing;kind="explicit_broker_alias";confirmed=link
            if match:
                confirmed_alias=(category=="fills" and match[keys[category]] in broker_exact.get(_broker(incoming),set()))
                alias=(kind in ("exact_broker_identity","explicit_broker_alias") or confirmed_alias) and incoming.get("broker_execution_id")!=match.get("broker_execution_id")
                differences=fact_differences(match,incoming,broker_alias=alias)
                if differences:
                    conflicts.append({"kind":category,"id":identifier,"existing_fact_id":match[keys[category]],"code":"source_row_identity_conflict" if kind.startswith("source_sha_") or kind=="exact_canonical_fact_id" else "broker_identity_fact_conflict","source_ref":ref,"differences":differences,"message":"身份锚命中但核心事实存在物质差异，原canonical不变，必须独立审查"})
                else:
                    row=deepcopy(match);row["source_ref"]=ref
                    proof={"version":VERSION,"match_type":kind,"fact_type":"fill" if category=="fills" else "financial_event","incoming_fact_id":identifier,"canonical_fact_id":match[keys[category]],"canonical_content_hash":match["content_hash"],
                        "incoming_source_ref":ref,"incoming_source_sha256":source_shas.get(ref["source_id"]),"canonical_source_ref":deepcopy(match["source_ref"]),
                        "incoming_broker_execution_id":incoming.get("broker_execution_id"),"canonical_broker_execution_id":match.get("broker_execution_id"),"identity_confirmation":bool(confirmed),"reason":confirmed["reason"] if confirmed else "同原件确切行或已确认精确券商身份复用；未改写原事实"}
                    proofs.append(proof)
            elif category=="fills" and incoming.get("broker_execution_id") and re.fullmatch(r"[0-9]+",incoming["broker_execution_id"]):
                near=broker_zero.get((*_broker(incoming)[:3],incoming["broker_execution_id"].lstrip("0") or "0"),set())
                for old_id in sorted(near):
                    existing=by_id[category][old_id]
                    differences=fact_differences(existing,incoming,broker_alias=True)
                    if not differences:
                        candidates.append({"incoming_source_ref":ref,"existing_fact_id":old_id,"expected_existing_hash":existing["content_hash"],"incoming_broker_execution_id":incoming["broker_execution_id"],"existing_broker_execution_id":existing["broker_execution_id"],"incoming":{k:incoming.get(k) for k in ("ticker","date","time","side","quantity","price","amount","fees","net_cash","reported_quantity_after")},"existing":{k:existing.get(k) for k in ("ticker","date","time","side","quantity","price","amount","fees","net_cash","reported_quantity_after")},"reason":"券商编号仅前导0差异且其他核心事实一致；仍须逐原行人工确认同一成交","confirmation_required":True})
                if near:
                    conflicts.append({"kind":category,"id":identifier,"source_ref":ref,"code":"broker_identity_confirmation_required","message":"券商编号存在前导0身份歧义，须核对明确行对和既有事实后确认；不能新增为另一成交"})
            row["source_ref"]["source_id"]=aliases.get(row["source_ref"]["source_id"],row["source_ref"]["source_id"])
            resolved.append(row)
        result[category]=resolved
    for loc in links.keys()-used_links:conflicts.append({"kind":"fills","code":"unused_identity_confirmation","source_ref":deepcopy(links[loc]["incoming_source_ref"]),"message":"身份确认未对应本次输入成交原行，不能静默忽略"})
    return {"data":result,"conflicts":conflicts,"identity_candidates":candidates,"identity_reconciliations":proofs,"source_imports":source_imports}


__all__=["VERSION","ImportIdentityError","normalize_identity_links","fact_differences","reconcile_import"]
