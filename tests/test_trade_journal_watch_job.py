"""Stable broker exports reuse reviewed parser and immutable ledger ownership."""
import base64
from copy import deepcopy
import csv
import hashlib
from io import StringIO

import pytest

from shilun.jobs.trade_journal_watch_job import VERSION, run_watch
from shilun.trade_journal.import_files import inspect_file
from shilun.trade_journal.service import TradeJournalError, TradeJournalService
from test_trade_journal import FakeStore


HEADER = ["成交日期", "成交时间", "证券代码", "证券名称", "买卖类别", "成交数量", "成交价格", "成交金额", "发生金额", "费用合计", "股票余额", "成交编号", "币种"]
BUY = ["2026-10-26", "09:54:50", "603278", "大业股份", "证券买入", "1000", "17.48", "17480", "-17485.18", "5.18", "1000", "00080001968265", "人民币"]


def write_csv(path, rows, header=HEADER):
    out = StringIO()
    writer = csv.writer(out)
    writer.writerows([header, *rows])
    path.write_text(out.getvalue())
    return path.read_bytes()


def setup_watch(tmp_path, rows=None):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    raw = write_csv(inbox / "export.csv", rows or [BUY])
    mapping = inspect_file("export.csv", base64.b64encode(raw).decode())["suggested_mapping"]
    config = {"version": VERSION, "enabled": True, "inbox": str(inbox), "profile_confirmed": True,
              "profile_sample_sha256": hashlib.sha256(raw).hexdigest(),
              "min_file_age_seconds": 0, "expected_headers": {"CSV": HEADER}, "mapping": mapping}
    store = FakeStore()
    return inbox, config, store, TradeJournalService(store)


def test_default_dry_run_checks_but_never_writes(tmp_path):
    _, config, store, journal = setup_watch(tmp_path)
    result = run_watch(config, journal)
    assert result["operation"] == "dry_run"
    assert result["files"][0]["status"] == "ready_dry_run"
    assert result["files"][0]["counts"]["new_fills"] == 1
    assert not store.writes and not result["writes_enabled"]


def test_apply_is_idempotent_for_same_file_and_overlapping_export(tmp_path):
    inbox, config, store, journal = setup_watch(tmp_path)
    first = run_watch(config, journal, apply=True)
    assert first["files"][0]["status"] == "imported"
    writes = deepcopy(store.writes)
    repeat = run_watch(config, journal, apply=True)
    assert repeat["files"][0]["status"] == "already_imported"
    assert store.writes == writes
    sell = deepcopy(BUY)
    sell[0], sell[4], sell[8], sell[10], sell[11] = "2026-10-27", "证券卖出", "17474.82", "0", "80001968266"
    write_csv(inbox / "overlap.csv", [BUY, sell])
    result = run_watch(config, journal, apply=True)
    assert result["status"] == "complete", result
    assert journal.get_summary()["counts"]["fills"] == 2
    assert all(name.startswith("trade_journal_") for name, _, _ in store.writes)


def test_disabled_apply_and_unconfirmed_profile_do_not_write(tmp_path):
    _, config, store, journal = setup_watch(tmp_path)
    config["enabled"] = False
    with pytest.raises(TradeJournalError, match="尚未启用"):
        run_watch(config, journal, apply=True)
    config["enabled"], config["profile_confirmed"] = True, False
    assert run_watch(config, journal, apply=True)["files"][0]["status"] == "needs_format_confirmation"
    assert not store.writes


def test_changed_header_blocks_fixed_column_import(tmp_path):
    inbox, config, store, journal = setup_watch(tmp_path)
    write_csv(inbox / "export.csv", [BUY], ["发生日期", *HEADER[1:]])
    result = run_watch(config, journal, apply=True)
    assert result["files"][0]["status"] == "needs_format_confirmation"
    assert not store.writes


def test_reviewed_sample_must_be_recorded_before_apply(tmp_path):
    _, config, store, journal = setup_watch(tmp_path)
    config.pop("profile_sample_sha256")
    result = run_watch(config, journal, apply=True)
    assert result["files"][0]["status"] == "needs_format_confirmation"
    assert not store.writes


def test_missing_broker_id_and_financial_events_require_manual_review(tmp_path):
    row = deepcopy(BUY)
    row[11] = ""
    inbox, config, store, journal = setup_watch(tmp_path, [row])
    assert run_watch(config, journal, apply=True)["files"][0]["status"] == "needs_identity_review"
    row[2], row[4], row[5], row[6], row[7], row[8], row[10] = "", "红利入账", "0", "0", "0", "10", "0"
    write_csv(inbox / "export.csv", [row])
    assert run_watch(config, journal, apply=True)["files"][0]["status"] == "needs_identity_review"
    assert not store.writes


def test_fresh_file_waits_and_symlink_is_not_followed(tmp_path):
    inbox, config, store, journal = setup_watch(tmp_path)
    config["min_file_age_seconds"] = 60
    (inbox / "linked.csv").symlink_to(inbox / "export.csv")
    result = run_watch(config, journal)
    assert [r["status"] for r in result["files"]] == ["waiting_for_stable_file", "blocked"]
    assert not store.writes


def test_missing_inbox_does_not_create_it_or_claim_enabled(tmp_path):
    missing = tmp_path / "not_yet_confirmed"
    result = run_watch({"version": VERSION, "inbox": str(missing)}, TradeJournalService(FakeStore()))
    assert result["status"] == "inbox_not_configured"
    assert not result["writes_enabled"] and not missing.exists()
