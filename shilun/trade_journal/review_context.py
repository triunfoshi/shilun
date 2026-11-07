"""Read-only review dates: imported trades are not proof of statement coverage."""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from .market_checks import JournalMarketCheckService
from .service import TradeJournalService, _time


VERSION = "journal-review-context-v1"


def _count_open_days(owner, calendar, start, end):
    """Require one explicit open/closed record for every calendar date."""
    count, current = 0, date.fromisoformat(start)
    last = date.fromisoformat(end)
    while current <= last:
        opened = owner._open(calendar, current.isoformat())
        if opened is None:
            return None
        count += int(opened)
        current += timedelta(days=1)
    return count


class JournalReviewContextService:
    def __init__(self, store, *, journal=None, now_factory=None):
        self.store = store
        self.journal = journal or TradeJournalService(store, now_factory=now_factory)
        self.now_factory = now_factory or self.journal.now_factory

    def get(self, episode_id):
        now = _time(self.now_factory())
        today = now.date().isoformat()
        detail = self.journal.get_episode(episode_id)
        episode = detail["episode"]
        account = episode["account_alias"]
        fills = [r for r in self.journal._facts()[0] if r["account_alias"] == account]
        fill_dates = sorted({r["date"] for r in fills if r["date"] <= today})
        batches = [r for r in self.journal._committed_batches() if r.get("account_alias") == account
                   and (r.get("fill_ids") or r.get("financial_event_ids"))]
        latest_import = max((r["recorded_at"] for r in batches), default=None)
        latest_fill = fill_dates[-1] if fill_dates else None
        start = min([episode["start_date"], *(fill_dates[-1:] or [])])
        # A long exchange holiday must not be replaced by a weekday guess.
        end = (max(date.fromisoformat(start), now.date()) + timedelta(days=40)).isoformat()
        calendar_owner = JournalMarketCheckService(self.store, journal=self.journal)
        calendar = calendar_owner._calendar(start, end)
        cursor = now.date()
        latest_trade_date = None
        while cursor.isoformat() >= start:
            opened = calendar_owner._open(calendar, cursor.isoformat())
            if opened is None:
                break
            if opened:
                latest_trade_date = cursor.isoformat()
                break
            cursor -= timedelta(days=1)
        date_is_current = latest_fill == latest_trade_date if latest_fill and latest_trade_date else None
        buys = []
        for row in detail["fills"]:
            if row["side"] != "buy":
                continue
            current = date.fromisoformat(row["date"]) + timedelta(days=1)
            next_day = None
            while current.isoformat() <= end:
                opened = calendar_owner._open(calendar, current.isoformat())
                if opened is None:
                    break
                if opened:
                    next_day = current.isoformat()
                    break
                current += timedelta(days=1)
            buys.append({key: row.get(key) for key in
                         ("fill_id", "date", "executed_at", "price", "quantity", "side", "time_precision")})
            buys[-1].update(next_trading_day=next_day, earliest_sell_date=next_day,
                            next_trading_day_status="complete_calendar" if next_day else "unknown")
        gap = _count_open_days(calendar_owner, calendar,
                              (date.fromisoformat(latest_fill) + timedelta(days=1)).isoformat(), today) if latest_fill else None
        return {
            "version": VERSION, "episode_id": episode_id, "as_of": now.isoformat(timespec="seconds"),
            "as_of_date": today, "timezone": "Asia/Shanghai", "episode_start_date": episode["start_date"],
            "calendar_days_since_entry": (now.date() - date.fromisoformat(episode["start_date"])).days,
            "buy_fills": sorted(buys, key=lambda r: (r["date"], r.get("executed_at") or "", r["fill_id"])),
            "freshness": {
                "account_alias": account, "latest_fill_date": latest_fill, "latest_imported_at": latest_import,
                "statement_coverage_end": None, "coverage_status": "unconfirmed",
                "needs_statement_confirmation": True, "gap_trading_days": gap,
                "latest_expected_trade_date": latest_trade_date,
                "date_is_current": date_is_current,
                "date_currentness": "unknown" if date_is_current is None else "current" if date_is_current else "stale",
                "date_check_scope": "比较已存成交日期与最近交易日，仅用于提示收起；不证明交割单完整或当日已收盘",
                "gap_calendar_status": "complete_calendar" if gap is not None else "unknown",
                "gap_scope": "最后成交日期之后至今天（含今天）的交易日；不证明这些日期有成交或漏单",
                "message": "最后一笔成交日期不等于交割单完整覆盖日期。请确认之后是否有交易，或导入新的券商导出文件。",
                "import_prompt": "这之后有新的交易吗？如有，请导入新的交割单；如无，可继续复盘，成交是否完整仍待确认。",
            },
            "import_automation": {
                "enabled": False, "configured": Path(__file__).resolve().parents[2].joinpath("交割单待导入").is_dir(), "mode": "dry_run",
                "scheduler_status": "not_enabled",
                "recommended_inbox": str(Path(__file__).resolve().parents[2] / "交割单待导入"),
                "message": "支持固定文件夹定时扫描；尚未确认文件夹、导出格式与启用调度。脚本不能替券商生成导出文件。",
            },
            "default_target_policy": {
                "anchor": "selected_buy_fill", "trading_day_offset": 1,
                "note": "T+1是所选这笔买入后的下一交易日。每次加仓可单独记录预期；不会把旧批次的卖出日期顺延。",
            },
            "read_only": True,
        }
