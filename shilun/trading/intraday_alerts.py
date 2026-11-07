"""盘中 Decision Timeline、监控清单与飞书通知 Outbox。"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo


MARKET_TIMEZONE = ZoneInfo("Asia/Shanghai")
WATCHLIST_COLLECTION = "intraday_watchlist"
EVENT_COLLECTION = "intraday_decision_events"
OUTBOX_COLLECTION = "trading_notification_outbox"

RISK_CODES = {"stop_loss_broken", "support_broken"}
POSITIVE_CODES = {"pressure_breakout", "support_reclaimed", "ma8_reclaim"}


def replace_watchlist(collection: Any, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    now = datetime.now(tz=MARKET_TIMEZONE).isoformat(timespec="seconds")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        ticker = str(item.get("ticker") or "").strip().upper()
        if not ticker or ticker in seen:
            continue
        seen.add(ticker)
        document = {
            "ticker": ticker,
            "name": str(item.get("name") or ticker),
            "sector_name": str(item.get("sector_name") or ""),
            "source": str(item.get("source") or "selection_pool"),
            "enabled": True,
            "feishu_enabled": bool(item.get("feishu_enabled", True)),
            "updated_at": now,
        }
        collection.update_one({"ticker": ticker}, {"$set": document}, upsert=True)
        normalized.append(document)
    collection.update_many(
        {"ticker": {"$nin": list(seen)}},
        {"$set": {"enabled": False, "updated_at": now}},
    )
    return normalized


def list_watchlist(collection: Any, *, enabled_only: bool = True) -> list[dict[str, Any]]:
    query = {"enabled": True} if enabled_only else {}
    return list(collection.find(query, {"_id": 0}).sort("updated_at", -1))


def _decision(candidate: dict[str, Any], market_gate: dict[str, Any]) -> dict[str, Any]:
    signals = [
        item for item in (candidate.get("signal_events") or [])
        if item.get("code") not in {"no_trigger", "realtime_missing"}
    ]
    codes = [str(item.get("code") or "") for item in signals if item.get("code")]
    primary = candidate.get("primary_event") or (signals[0] if signals else {})
    if any(code in RISK_CODES for code in codes):
        action_type, action_label, severity = "manual_verify_risk", "人工核验风险", "critical"
        execution_state = "data_blocked"
    elif any(code in POSITIVE_CODES for code in codes):
        action_type, action_label, severity = "wait_confirmation", "等待个股确认", "medium"
        execution_state = "confirming"
    elif "buy_zone_touched" in codes:
        action_type, action_label, severity = "watch_trigger", "进入触发观察", "medium"
        execution_state = "armed"
    else:
        action_type, action_label, severity = "observe_setup", "观察入选", "low"
        execution_state = "selected"
    realtime = candidate.get("realtime") or {}
    reasons = [str(item.get("detail") or item.get("action") or item.get("title") or "") for item in signals]
    reasons = [item for item in reasons if item]
    next_condition = next(
        (str(item.get("action") or "") for item in signals if item.get("action")),
        "等待下一条个股确认条件",
    )
    primary_reason = reasons[0] if reasons else "候选执行事实已更新"
    return {
        "action_type": action_type,
        "action_label": action_label,
        "severity": severity,
        "position_state": "flat",
        "execution_state": execution_state,
        "scenario_id": str(primary.get("code") or "no_trigger"),
        "scenario_title": str(primary.get("title") or "等待触发"),
        "signal_codes": codes,
        "reasons": reasons[:5],
        "switch_conditions": [str(item.get("action") or "") for item in signals[:3] if item.get("action")],
        "presentation": {
            "state_label": {
                "selected": "已入选",
                "armed": "已布防",
                "confirming": "等待确认",
                "data_blocked": "数据阻断",
            }[execution_state],
            "headline": action_label,
            "instruction": "先核验事实，再继续执行" if execution_state == "data_blocked" else "暂不买入",
            "reason": primary_reason,
            "next_condition": next_condition,
            "urgency": severity,
            "executable": False,
            "data_as_of": realtime.get("quote_at") or realtime.get("update_at"),
        },
        "market_gate": str(market_gate.get("state") or "unknown"),
        "market_gate_label": str(market_gate.get("state_label") or "市场状态待确认"),
        "current_price": realtime.get("current"),
        "pct_chg": realtime.get("pct_chg"),
        "quote_at": realtime.get("quote_at") or realtime.get("update_at"),
        "source": realtime.get("source"),
    }


def _fingerprint(decision: dict[str, Any]) -> str:
    semantic = {
        "action_type": decision.get("action_type"),
        "scenario_id": decision.get("scenario_id"),
        "signal_codes": sorted(set(decision.get("signal_codes") or [])),
        "market_gate": decision.get("market_gate"),
    }
    raw = json.dumps(semantic, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def record_candidate_decisions(
    event_collection: Any,
    outbox_collection: Any,
    candidates: list[dict[str, Any]],
    *,
    session_date: str,
    computed_at: str,
    market_gate: dict[str, Any],
    push_tickers: set[str],
) -> list[dict[str, Any]]:
    created_events: list[dict[str, Any]] = []
    for candidate in candidates:
        ticker = str(candidate.get("ticker") or "").upper()
        if not ticker:
            continue
        decision = _decision(candidate, market_gate)
        fingerprint = _fingerprint(decision)
        previous = event_collection.find_one(
            {"ticker": ticker, "session_date": session_date},
            {"_id": 0},
            sort=[("computed_at", -1)],
        )
        if previous and previous.get("fingerprint") == fingerprint:
            continue
        event_key = f"{ticker}|{session_date}|{computed_at}|{fingerprint}"
        event_id = hashlib.sha256(event_key.encode("utf-8")).hexdigest()
        event = {
            "event_id": event_id,
            "ticker": ticker,
            "name": candidate.get("name") or ticker,
            "sector_name": candidate.get("sector_name") or candidate.get("industry") or "",
            "session_date": session_date,
            "computed_at": computed_at,
            "fingerprint": fingerprint,
            "previous": (previous or {}).get("current"),
            "current": decision,
            "reasons": decision["reasons"],
            # 空仓候选失守观察位只表示候选失效，不属于账户持仓风险；
            # 仍保留审计事件，但不得发送“风险处置”提醒。
            # 候选观察事件只进入站内时间线。飞书只投递工作台归一后的 P0-P2
            # 可执行事件，避免多个候选同时变化时形成消息洪泛。
            "notification_eligible": False,
        }
        event_collection.update_one({"event_id": event_id}, {"$setOnInsert": event}, upsert=True)
        created_events.append(event)
        if event["notification_eligible"]:
            outbox_id = hashlib.sha256(f"{event_id}|feishu".encode("utf-8")).hexdigest()
            outbox_collection.update_one(
                {"outbox_id": outbox_id},
                {"$setOnInsert": {
                    "outbox_id": outbox_id,
                    "event_id": event_id,
                    "ticker": ticker,
                    "channel": "feishu",
                    "status": "pending",
                    "attempts": 0,
                    "next_retry_at": computed_at,
                    "created_at": computed_at,
                    "event": event,
                }},
                upsert=True,
            )
    return created_events


def list_candidate_timeline(
    collection: Any,
    ticker: str,
    *,
    session_date: str,
    limit: int = 5,
) -> list[dict[str, Any]]:
    return list(
        collection.find(
            {"ticker": ticker.upper(), "session_date": session_date},
            {"_id": 0},
        ).sort("computed_at", -1).limit(limit)
    )


def feishu_card(event: dict[str, Any]) -> dict[str, Any]:
    current = event.get("current") or {}
    previous = event.get("previous") or {}
    severity = current.get("severity")
    template = "red" if severity in {"critical", "high"} else "orange" if severity == "medium" else "blue"
    reasons = event.get("reasons") or ["决策语义发生变化"]
    previous_label = previous.get("action_label") or "初始状态"
    price = current.get("current_price")
    pct = current.get("pct_chg")
    price_text = "-" if price is None else str(price)
    pct_text = "-" if pct is None else f"{float(pct) * 100:+.2f}%"
    presentation = event.get("presentation") or current.get("presentation") or {}
    reason = presentation.get("reason") or (reasons[0] if reasons else "执行事实发生变化")
    next_condition = presentation.get("next_condition") or "等待下一条可核验执行事实"
    instruction = presentation.get("instruction") or current.get("action_label") or "继续观察"
    action_type = str(current.get("action_type") or "")
    category = "风险处置" if severity in {"critical", "high"} else "机会确认" if action_type == "prepare_entry" else "执行更新"
    return {
        "header": {
            "template": template,
            "title": {"tag": "plain_text", "content": f"{category}｜{event.get('name')} {event.get('ticker')}"},
        },
        "elements": [
            {"tag": "markdown", "content": f"**动作**：{previous_label} → **{current.get('action_label')}**\n**原因**：{reason}"},
            {"tag": "markdown", "content": f"**执行**：{instruction}\n**下一条件**：{next_condition}\n**现价**：{price_text}（{pct_text}）"},
            {"tag": "note", "elements": [{"tag": "plain_text", "content": f"{current.get('quote_at') or event.get('computed_at') or '-'} · {current.get('market_gate_label') or '市场状态待确认'}"}]},
        ],
    }


def dispatch_feishu_outbox(
    outbox_collection: Any,
    client: Any,
    *,
    now: datetime | None = None,
    limit: int = 3,
    max_age_minutes: int = 30,
) -> dict[str, int]:
    current = now or datetime.now(tz=MARKET_TIMEZONE)
    now_text = current.isoformat(timespec="seconds")
    due_documents = list(
        outbox_collection.find(
            {"channel": "feishu", "status": {"$in": ["pending", "retry"]}, "next_retry_at": {"$lte": now_text}},
            {"_id": 0},
        ).sort("created_at", 1)
    )
    fresh: list[dict[str, Any]] = []
    expired = 0
    freshness_cutoff = current - timedelta(minutes=max_age_minutes)
    for document in due_documents:
        try:
            created_at = datetime.fromisoformat(str(document.get("created_at") or "").replace("Z", "+00:00"))
        except ValueError:
            created_at = datetime.min.replace(tzinfo=MARKET_TIMEZONE)
        if created_at.astimezone(MARKET_TIMEZONE) < freshness_cutoff.astimezone(MARKET_TIMEZONE):
            outbox_collection.update_one(
                {"outbox_id": document["outbox_id"]},
                {"$set": {
                    "status": "expired",
                    "expired_at": now_text,
                    "suppression_reason": "超过通知时效，避免更换通道后集中补发",
                }},
            )
            expired += 1
            continue
        fresh.append(document)

    severity_rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    priority_rank = {"P0": 0, "P1": 1, "P2": 2, "P3": 3, "P4": 4, "P5": 5}
    fresh.sort(key=lambda item: (
        priority_rank.get(str((item.get("event") or {}).get("priority") or "P9"), 9),
        severity_rank.get(str(((item.get("event") or {}).get("current") or {}).get("severity") or "low"), 9),
        str(item.get("created_at") or ""),
    ))
    documents: list[dict[str, Any]] = []
    selected_tickers: set[str] = set()
    for document in fresh:
        ticker = str(document.get("ticker") or "")
        if ticker and ticker in selected_tickers:
            continue
        if ticker:
            selected_tickers.add(ticker)
        documents.append(document)
        if len(documents) >= max(1, int(limit)):
            break

    sent = failed = 0
    for document in documents:
        attempts = int(document.get("attempts") or 0) + 1
        try:
            client.send_card(feishu_card(document.get("event") or {}))
            outbox_collection.update_one(
                {"outbox_id": document["outbox_id"]},
                {"$set": {"status": "sent", "attempts": attempts, "sent_at": now_text, "last_error": None}},
            )
            sent += 1
        except Exception as error:  # noqa: BLE001 - 通知失败不得影响决策事实
            terminal = attempts >= 3
            retry_at = current + timedelta(seconds=(5, 30, 120)[min(attempts - 1, 2)])
            outbox_collection.update_one(
                {"outbox_id": document["outbox_id"]},
                {"$set": {
                    "status": "failed" if terminal else "retry",
                    "attempts": attempts,
                    "next_retry_at": retry_at.isoformat(timespec="seconds"),
                    "last_error": str(error)[:500],
                }},
            )
            failed += 1
    return {"selected": len(documents), "sent": sent, "failed": failed, "expired": expired}
