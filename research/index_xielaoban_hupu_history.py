"""Offline audit/index of the independent Hupu export; never calls trading services."""
import hashlib
import json
import re
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output/xielaoban-hupu-history-20260922"
EVIDENCE = ROOT / "docs/evidence/xielaoban-hupu-20260922"
LEGACY = ROOT / "output/hupu_real_xielaoban_2024-08-19_to_2026-08-19/replies.jsonl"
# IDs are local to their thread. A reply ID alone is not globally unique.
SELECTED = [
    ("629466854", "385682", "模式是多条件组合；90%为作者自评，未核验"),
    ("629217617", "361123", "复盘描述与临盘信号有别"),
    ("629217617", "369110", "对复盘计划与盘口量化的质疑，不是自动化的有效性证明"),
    ("629466854", "379947", "作者偏重形态和盘口"),
    ("629466854", "382834", "选形态时自称不关注分时；限制统一盘口门槛"),
    ("629466854", "344858", "同股趋势卖点与抱团激进买点并存"),
    ("629466854", "376682", "无单一核心的首板组织方式；量化因果仅为作者归因"),
    ("629466854", "388397", "自称首板战法改进版；引用者整段描述不能归给作者"),
    ("629466854", "338886", "警惕缩量直线首板；资金身份未核验"),
    ("629466854", "376883", "后来认可缩量首板群体；与一刀切禁令有张力"),
    ("629466854", "388338", "缩量板问答，具体票在图片中，不补造身份"),
    ("629466854", "336855", "跟随者成交样本可能偏向亏损票"),
    ("629466854", "350668", "首板执行速度与排队劣势"),
    ("629466854", "360480", "持股行情不等于存在新买点"),
    ("629466854", "390961", "看最高结束分歧，交易最强跟随股；低风险属自评"),
    ("628676880", "366963", "当时以前一日50亿限制接力；不是7日均额参数"),
    ("629466854", "356145", "特定反包预期用开盘5分钟；未核对图片标的"),
    ("629466854", "390909", "另一案例前5分钟未看，后发现走弱再割"),
    ("629466854", "386347", "自述提前点火的预判型参与；成交未经核验"),
    ("629466854", "389780", "明确承认竞价参与属于预判"),
    ("629466854", "350828", "依赖对手行为的模式可能失效"),
    ("629466854", "378624", "趋势分歧未带来预期抱团加强，选择等待"),
    ("629466854", "373574", "主张专注一种模式；提问原文被平台隐藏"),
    ("629466854", "391149", "多种玩法能赚钱不代表个人应全部参与"),
    ("629466854", "391066", "执行自己的系统，不追别人的仓位"),
    ("629466854", "376763", "特定账户缓冲与固定单票金额示例，不作默认仓位"),
    ("629466854", "390228", "小资金不分仓说法；与其他分仓建议存在冲突"),
    ("629466854", "386661", "复盘指数、题材、前后排、大小票、涨停炸板及溢价"),
    ("629466854", "348325", "盘后上涨不能倒推出早盘买点"),
    ("629466854", "391429", "9月21日无主线，形态首板与抱团两条分支"),
    ("629466854", "391437", "9月21日回踩五日线再上板的当时评价"),
    ("629466854", "391551", "9月21日按自身体量偏好抱团"),
    ("629466854", "390941", "9月16日上午缩量空仓表态"),
    ("629466854", "390985", "随后观察大小票反馈背离"),
    ("629466854", "391013", "午后重新参与表态，未给完整成交细节"),
    ("629466854", "391016", "午后点名通鼎"),
    ("629466854", "391050", "盘后又压低大票收益预期，完整因果不明"),
    ("629466854", "390657", "强调情绪节点而非孤立个股"),
    ("629466854", "390739", "9月14日对次日低开仍给参与预期"),
    ("629466854", "390784", "9月15日承认穿越预期失败"),
    ("629466854", "390822", "承认无法解释失败，不强行补因果"),
    ("629466854", "391586", "9月22日新观察；不可倒填9月21日计划"),
    ("629217617", "352265", "高低切为相对比较，并非固定一进二"),
    ("629466854", "385590", "个人资金体量也限制可选标的"),
    ("629466854", "348410", "反对仅为摊低成本追加失败超短单"),
    ("629466854", "345936", "连板分仓建议"),
    ("629466854", "356355", "另有不上榜不分仓观点；不能合成为统一规则"),
    ("629466854", "338711", "早期抱团与其他风格互斥的说法"),
    ("629466854", "377436", "后来描述连板趋势抱团同好，限制互斥两态"),
    ("629466854", "366544", "建议先练单一模式，无匹配机会不出手"),
]
TOPICS = {
    "环境与节点": r"指数|量能|放量|缩量|情绪|节点|退潮|主线",
    "玩法与角色": r"趋势|抱团|套利|接力|首板|核心|前排|后排|龙头|高低切",
    "执行与退出": r"盘口|分时|日\s*K|形态|均线|5\s*日线|五日线|买点|卖点|竞价|承接|炸板|回封|低吸|预期|止损|割了",
    "能力与仓位": r"模式|系统|分仓|仓位|资金量|体量|知行|排不|买不到|抢不到|分歧",
}

def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")

def local_time(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(ZoneInfo("Asia/Shanghai")).isoformat()

def main():
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    monthly = OUT / "monthly"
    monthly.mkdir(exist_ok=True)
    manifest_path = OUT / "crawl-manifest.json"
    manifest = json.loads(manifest_path.read_text())
    # Preserve the failed sandbox attempt as history rather than a current crawl error.
    if manifest.get("status") == "complete" and manifest.get("error"):
        manifest.setdefault("priorAttemptErrors", []).append(manifest.pop("error"))
    blob = (OUT / "replies.jsonl").read_bytes()
    rows = [json.loads(x) for x in blob.splitlines()]
    key = lambda r: f"{r['threadId']}:{r['replyId']}"
    by_id = {key(r): r for r in rows}
    assert len(rows) == len(by_id) == manifest["recordCount"]
    assert hashlib.sha256(blob).hexdigest() == manifest["recordsSha256"]
    assert manifest["status"] == "complete" and manifest["pages"][-1]["nextPage"] is False
    assert all(r["authorId"] == "100051653" and r["createdAt"] for r in rows)
    pages = manifest["pages"]
    for i, page in enumerate(pages):
        payload_blob = (OUT / "pages" / page["file"]).read_bytes()
        page["sanitizedResponseSha256"] = hashlib.sha256(payload_blob).hexdigest()
        payload = json.loads(payload_blob)
        assert len(payload["data"]["replyWithQuoteDtoList"]) == page["rawCount"]
        assert all(str(r["puid"]) == "100051653" for r in payload["data"]["replyWithQuoteDtoList"])
        if i:
            assert page["cursor"] == pages[i-1]["nextCursor"]
    dump(manifest_path, manifest)
    legacy = {key(r): r for r in map(json.loads, LEGACY.read_text().splitlines())}
    topics = {k: [key(r) for r in rows if re.search(pattern, r["text"])] for k, pattern in TOPICS.items()}
    relevant = set().union(*map(set, topics.values()))
    # SQLite is a full-author-text index; quoted content is kept in a separate column.
    with sqlite3.connect(OUT / "corpus.sqlite3") as db:
        db.execute("DROP TABLE IF EXISTS utterances")
        db.execute("CREATE TABLE utterances (source_id TEXT PRIMARY KEY, published_at TEXT, author_text TEXT, quoted_text TEXT, url TEXT, own_images_json TEXT)")
        db.executemany("INSERT INTO utterances VALUES (?,?,?,?,?,?)", [(key(r), local_time(r['createdAt']), r['text'], r['quotedText'], r['url'], json.dumps(r['images'])) for r in rows])
        assert db.execute("SELECT COUNT(*) FROM utterances").fetchone()[0] == len(rows)
    groups = defaultdict(list)
    for r in rows:
        groups[local_time(r["createdAt"])[:7]].append(r)
    for month, items in groups.items():
        parts = [f"# {month} real蟹老板公开回复\n", "本人正文与引用上下文分开；图片仅保留链接，未全面OCR。\n"]
        for r in items:
            parts += [f"\n## {local_time(r['createdAt'])} · {key(r)}\n", f"[原帖]({r['url']})\n", f"本人：{r['text']}\n"]
            if r["quotedText"]:
                parts.append(f"引用上下文（{r['quotedAuthor']}，不归于本人）：{r['quotedText']}\n")
            if r["images"]:
                parts.append("本人附件：" + " · ".join(f"[图{i+1}]({url})" for i, url in enumerate(r['images'])) + "\n")
        (monthly / f"{month}.md").write_text("\n".join(parts))
    evidence = []
    evidence_md = ["# 虎扑独立证据索引\n", "H编号为本次研究编号；本人原文与被引用者分开。以下归纳均不证明个人成交或策略胜率，附件未全面核验。完整原文保存在本地JSON证据与全量档案中。\n", "|编号|北京时间|本人观点的研究用途（转述）|原帖|", "|---|---|---|---|"]
    for i, (tid, pid, note) in enumerate(SELECTED, 1):
        r = by_id[f"{tid}:{pid}"]
        eid = f"H{i:02d}"
        evidence.append({"evidenceId": eid, "researchNote": note, "publishedAtChina": local_time(r["createdAt"]), **r})
        evidence_md.append(f"|{eid}|{local_time(r['createdAt'])}|{note}|[原帖]({r['url']})|")
    dump(EVIDENCE / "author-evidence.json", evidence)
    (EVIDENCE / "author-evidence.md").write_text("\n".join(evidence_md) + "\n")
    audit = {
        "subject": manifest["subject"], "pages": len(pages), "records": len(rows),
        "earliestChina": local_time(rows[0]["createdAt"]), "latestChina": local_time(rows[-1]["createdAt"]),
        "crawlFinishedAt": manifest["finishedAt"], "stopReason": manifest["stopReason"],
        "duplicateCount": manifest["duplicateCount"], "missingTimestampCount": manifest["missingTimestampCount"],
        "threadCounts": dict(Counter(r["threadId"] for r in rows)),
        "authorTextCharacters": sum(len(r["text"]) for r in rows),
        "withOwnImages": sum(bool(r["images"]) for r in rows),
        "withOwnImagesAndFewerThan8TextCharacters": sum(bool(r["images"]) and len(r["text"]) < 8 for r in rows),
        "withQuotedText": sum(bool(r["quotedText"]) for r in rows),
        "legacy": {"records": len(legacy), "added": len(by_id.keys()-legacy.keys()), "notReturned": len(legacy.keys()-by_id.keys()), "changedAuthorText": sum(by_id[k]["text"] != legacy[k]["text"] for k in legacy.keys() & by_id.keys())},
        "topicHitCounts": {k: len(v) for k, v in topics.items()}, "topicMatchedDistinctRecords": len(relevant),
        "curatedEvidenceRecords": len(evidence), "recordsSha256": manifest["recordsSha256"],
        "scope": "完整遍历本次公开用户回复接口；不保证隐藏、删除、接口更早未返回内容；主帖另核，图片未全面解读。",
        "method": "全量正文和引用分别存档、按月编排并建SQL索引；主题检索辅助筛读，50条证据回读上下文。主题词命中不等于有效规则。",
    }
    main_audit_path = OUT / "main-post-audit.json"
    if main_audit_path.exists():
        main_audit = json.loads(main_audit_path.read_text())
        audit["mainPostAudit"] = {
            "checkedThreads": len(main_audit["checks"]),
            "readThreads": sum(c["status"] == "read" for c in main_audit["checks"]),
            "ownMainPosts": len(main_audit["ownPosts"]), "scope": main_audit["scope"],
        }
    dump(EVIDENCE / "coverage-audit.json", audit)
    dump(OUT / "topic-index.json", {"patterns": TOPICS, "sourceIds": topics})
    readme = [
        "# real蟹老板虎扑独立历史档案\n",
        f"公开回复 {len(rows):,} 条，{len(pages)} 页；{audit['earliestChina']} 至 {audit['latestChina']}。本人正文与引用分字段保存。\n",
        "范围：公开回复接口本次返回的历史。隐藏、删除、全部自建主帖及图片内文字不保证覆盖；图片未全面识别。\n",
        "## 文件\n",
        "- [爬取与哈希清单](crawl-manifest.json)",
        "- [本人回复及引用上下文 JSONL](replies.jsonl)",
        "- [涉及主题的主帖核查](main-post-audit.json)",
        "- [SQLite 文本索引](corpus.sqlite3)：表 utterances；author_text 为本人正文，quoted_text 为引用上下文。中文检索可用 LIKE。",
        "- [主题词索引](topic-index.json)：仅为辅助检索标签，不是有效规则数量。",
        "- [50条核对证据](../../docs/evidence/xielaoban-hupu-20260922/author-evidence.md)",
        "- [修订后的决策框架](../../docs/xielaoban_decision_framework_20260922.md)",
        "- [本次修订说明](../../docs/xielaoban_hupu_framework_revision_20260922.md)\n",
        "## 按月全文\n",
    ]
    readme.extend(f"- [{month}](monthly/{month}.md)：{len(items)} 条" for month, items in sorted(groups.items()))
    (OUT / "README.md").write_text("\n".join(readme) + "\n")
    print(json.dumps(audit, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
