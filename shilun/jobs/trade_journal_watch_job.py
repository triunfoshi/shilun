"""Preview/import stable broker exports in one configured folder, without a broker API.

Default is read-only dry-run. Applying requires an explicitly enabled config,
a reviewed fixed column/header profile, stable broker execution identifiers,
and the journal owner's normal conflict/digest checks. Files never move/delete.
"""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import time

from shilun.common.config import load_config
from shilun.common.db import MongoSnapshotStore
from shilun.trade_journal.import_files import LIMITS, inspect_file, parse_file
from shilun.trade_journal.service import TradeJournalError, TradeJournalService

VERSION = "journal-statement-watch-v1"
EXTENSIONS = {".csv", ".tsv", ".xlsx", ".xls"}


def _read_stable(path, *, min_age, now):
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            raise TradeJournalError("只读取普通导出文件，不读取链接或特殊设备")
        if before.st_size > LIMITS["max_file_bytes"]:
            raise TradeJournalError("导出文件超过8MiB，请拆分")
        if now - before.st_mtime < min_age:
            return None
        with os.fdopen(fd, "rb", closefd=False) as handle:
            raw = handle.read(LIMITS["max_file_bytes"] + 1)
        after = os.fstat(fd)
        if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
            return None
        if len(raw) != after.st_size:
            return None
        return raw
    finally:
        os.close(fd)


def _profile_error(inspection, config):
    mapping = config.get("mapping")
    headers = config.get("expected_headers")
    if config.get("profile_confirmed") is not True or not isinstance(mapping, dict) or not mapping.get("tables"):
        return "尚未确认此券商导出格式与列映射；请先检查原件，再填写固定映射配置"
    if not re.fullmatch(r"[a-f0-9]{64}", str(config.get("profile_sample_sha256") or "")):
        return "须记录实际已审查样本的SHA256 profile_sample_sha256，再启用固定格式"
    if not isinstance(headers, dict) or not headers:
        return "缺少已确认的表头原文 expected_headers，不能只靠固定列号自动导入"
    for table in mapping["tables"]:
        if not isinstance(table, dict):
            return "工作表映射无效"
        sheet = next((s for s in inspection["sheets"] if s["name"] == table.get("sheet")), None)
        header = next((h for h in (sheet or {}).get("candidate_headers", []) if h["row"] == table.get("header_row")), None)
        expected = headers.get(table.get("sheet"))
        if not header or not isinstance(expected, list) or [c["label"] for c in header["columns"]] != expected:
            return "原件表头与已确认格式不同，停止自动导入，请人工核对"
    return None


def run_watch(config, service, *, apply=False, now=None, ensure_indexes=None):
    if not isinstance(config, dict) or config.get("version") != VERSION:
        raise TradeJournalError("导入配置版本不正确")
    if apply and config.get("enabled") is not True:
        raise TradeJournalError("配置尚未启用；当前只能dry-run。确认目录和格式后再启用")
    inbox_text = config.get("inbox")
    if not isinstance(inbox_text, str) or not inbox_text.strip():
        raise TradeJournalError("请配置券商导出文件夹 inbox")
    inbox = Path(inbox_text).expanduser()
    if inbox.is_symlink() or not inbox.is_dir():
        return {"version": VERSION, "operation": "apply" if apply else "dry_run",
                "status": "inbox_not_configured", "files": [], "writes_enabled": False,
                "message": "导出文件夹不存在或是链接；请确认真实目录，当前未扫描/未导入"}
    min_age = config.get("min_file_age_seconds", 60)
    if type(min_age) not in (int, float) or not 0 <= min_age <= 86400:
        raise TradeJournalError("min_file_age_seconds须为0至86400的秒数")
    stamp = time.time() if now is None else now
    outputs, prepared = [], []
    paths = sorted(p for p in inbox.iterdir() if p.suffix.lower() in EXTENSIONS)
    if len(paths) > 100:
        raise TradeJournalError("单次最多扫描100份导出，请先整理目录")
    for path in paths:
        result = {"filename": path.name, "status": "pending"}
        outputs.append(result)
        try:
            if path.is_symlink():
                raise TradeJournalError("不读取符号链接")
            raw = _read_stable(path, min_age=min_age, now=stamp)
            if raw is None:
                result.update(status="waiting_for_stable_file", message="文件刚写入或正在变化，下次再读取")
                continue
            encoded = base64.b64encode(raw).decode("ascii")
            inspection = inspect_file(path.name, encoded, encoding=config.get("encoding"))
            result["sha256"] = inspection["sha256"]
            error = _profile_error(inspection, config)
            if error:
                result.update(status="needs_format_confirmation", message=error,
                              suggested_mapping=inspection["suggested_mapping"],
                              candidate_headers={s["name"]: s["candidate_headers"] for s in inspection["sheets"]})
                continue
            parsed = parse_file(path.name, encoded, mapping=config["mapping"],
                                account_alias=config.get("account_alias", "primary"), encoding=config.get("encoding"))
            if not parsed["can_preview"]:
                # Row numbers and reason codes are sufficient; do not log source account cells.
                result.update(status="needs_row_review", issues=[{k: issue[k] for k in ("code", "message", "sheet", "row") if k in issue}
                                                                 for issue in parsed["issues"]])
                continue
            payload = parsed["payload"]
            days = [r["date"] for r in payload["fills"] + payload["financial_events"]]
            result["transaction_dates"] = {"from": min(days), "to": max(days)}
            prepared.append((min(days), path.name, path, raw, payload, result))
        except (TradeJournalError, OSError, ValueError) as error:
            result.update(status="blocked", message=str(error))
    for _, _, path, raw, payload, result in sorted(prepared, key=lambda item: item[:2]):
        try:
            preview = service.preview_import(payload)
            result.update(preview_digest=preview["preview_digest"], counts=preview["counts"], warnings=preview["warnings"])
            if not preview["can_import"]:
                result.update(status="conflict", conflicts=preview["conflicts"])
                continue
            # New exports may overlap older files. Only the immutable broker ID,
            # never same price/quantity or source row number, can join them.
            if any(not r.get("broker_execution_id") for r in payload["fills"]) or payload["financial_events"]:
                result.update(status="needs_identity_review", message="自动导入要求每笔买卖都有稳定券商成交编号；财务事件须走人工预览，避免重叠导出重复入账")
                continue
            if preview.get("identity_candidates"):
                result.update(status="needs_identity_review", message="存在待确认的成交身份，须人工核对原行")
                continue
            if not apply:
                result.update(status="ready_dry_run", message="格式与账本预览通过；本次未写入")
                continue
            current = _read_stable(path, min_age=min_age, now=time.time() if now is None else now)
            if current is None or hashlib.sha256(current).digest() != hashlib.sha256(raw).digest():
                result.update(status="waiting_for_stable_file", message="预览后文件已变化，下次重新读取")
                continue
            if ensure_indexes:
                ensure_indexes(service.store)
            imported = service.import_batch(payload, preview["preview_digest"])
            result.update(status=imported["status"], batch_id=imported["batch_id"], idempotent=imported["idempotent"])
        except (TradeJournalError, OSError, ValueError) as error:
            result.update(status="blocked", message=str(error))
    review = any(r["status"] in {"blocked", "conflict", "needs_format_confirmation", "needs_row_review", "needs_identity_review"} for r in outputs)
    return {"version": VERSION, "operation": "apply" if apply else "dry_run",
            "status": "needs_review" if review else "complete", "files": outputs,
            "writes_enabled": bool(apply), "scheduler_installed_by_this_command": False,
            "statement_coverage_note": "记录交易日期范围，不据此声称无交易日或完整交割覆盖；不改持仓执行账户"}


def _save_receipt(path, result):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--apply", action="store_true", help="配置已启用并确认格式后，才写入通过预览的文件")
    parser.add_argument("--receipt", type=Path)
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    settings = load_config()
    if not settings.mongo_uri:
        parser.error("现有MongoDB连接未配置；账本不回退到本地数据库")
    store = MongoSnapshotStore(settings.mongo_uri, settings.mongo_db, socket_timeout_ms=30000, initialize_indexes=False)
    try:
        from shilun.trade_journal.indexes import ensure_journal_indexes
        result = run_watch(config, TradeJournalService(store), apply=args.apply, ensure_indexes=ensure_journal_indexes)
        result["run_at"] = datetime.now(timezone.utc).isoformat()
        if args.receipt:
            _save_receipt(args.receipt, result)
        print(json.dumps(result, ensure_ascii=False, default=str))
        return 2 if result["status"] in {"needs_review", "inbox_not_configured"} else 0
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
