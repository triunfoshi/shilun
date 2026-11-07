"""Job 8 真实盘中验收的只读预检。"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo


MARKET_TIMEZONE = ZoneInfo("Asia/Shanghai")
REQUIRED_INDICES = ("000001.SH", "399006.SZ")


def _positive(value: Any) -> bool:
    try:
        return float(value) > 0
    except (TypeError, ValueError):
        return False


def _check(check_id: str, passed: bool, label: str, detail: str) -> dict[str, Any]:
    return {
        "check_id": check_id,
        "passed": bool(passed),
        "state": "passed" if passed else "blocked",
        "label": label,
        "detail": detail,
    }


def build_trading_readiness(
    *,
    holdings: list[dict[str, Any]],
    quote_tickers: set[str],
    minute_tickers: set[str],
    latency_summary: dict[str, Any],
    api_key_configured: bool,
    now: datetime,
    last_sync_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """区分“可开始明日采样”和“45 秒阈值已经完成校准”。"""

    local_now = now.astimezone(MARKET_TIMEZONE)
    active_tickers = [str(item.get("ticker") or "").upper() for item in holdings]
    required_tickers = set(active_tickers) | set(REQUIRED_INDICES)
    quantity_complete = all(_positive(item.get("quantity")) for item in holdings)
    cost_complete = all(_positive(item.get("cost_basis") or item.get("entry_price")) for item in holdings)
    stop_complete = all(_positive(item.get("stop_loss_1")) for item in holdings)
    quote_complete = required_tickers.issubset(quote_tickers)
    minute_complete = required_tickers.issubset(minute_tickers)

    checks = [
        _check(
            "api_key",
            api_key_configured,
            "KlineShare 安全凭据",
            "已通过环境变量/安全凭据配置" if api_key_configured else "未持久配置 SHILUN_KLINESHARE_API_KEY",
        ),
        _check("active_holdings", bool(holdings), "活动持仓", f"当前 {len(holdings)} 只 active/reduced 持仓"),
        _check("quantity", quantity_complete, "真实股数", "全部已配置" if quantity_complete else "仍有持仓缺少真实股数"),
        _check("cost_basis", cost_complete, "成本口径", "全部已配置" if cost_complete else "仍有持仓缺少成本或入场价"),
        _check("hard_stop", stop_complete, "有效止损", "全部已配置" if stop_complete else "仍有持仓缺少有效止损"),
        _check("quote_coverage", quote_complete, "报价历史覆盖", "活动持仓与双指数均有快照" if quote_complete else f"缺少 {sorted(required_tickers - quote_tickers)}"),
        _check("minute_coverage", minute_complete, "5 分钟历史覆盖", "活动持仓与双指数均有分钟 K" if minute_complete else f"缺少 {sorted(required_tickers - minute_tickers)}"),
    ]
    collection_ready = all(item["passed"] for item in checks)
    calibration_ready = bool(latency_summary.get("calibration_ready"))
    blockers = [item["detail"] for item in checks if not item["passed"]]
    if not calibration_ready:
        blockers.append("45 秒阈值尚未取得每个标的至少 15 个真实交易时段样本")
    return {
        "status": "ok",
        "as_of": local_now.isoformat(timespec="seconds"),
        "market_day": local_now.weekday() < 5,
        "collection_ready": collection_ready,
        "job8_status": "validated" if collection_ready and calibration_ready else "pending_live_samples" if collection_ready else "blocked",
        "calibration_ready": calibration_ready,
        "latency_summary": latency_summary,
        "checks": checks,
        "blockers": blockers,
        "required_tickers": sorted(required_tickers),
        "last_sync_state": last_sync_state,
        "next_action": (
            "真实交易时段连续采集，达到每标的至少 15 个样本后评估 P95"
            if collection_ready and not calibration_ready
            else "修复阻断项后再启动盘中采集"
            if not collection_ready
            else "检查 P95、超阈值率和硬止损轨迹后签署 Job 8 验收"
        ),
    }
