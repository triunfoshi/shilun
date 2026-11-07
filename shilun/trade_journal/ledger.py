"""Pure historical inventory/FIFO reconstruction; never changes live positions.

Money is yuan. Decimal is used until the JSON boundary. A missing opening cost
or fee stays unknown, including when the position later reaches zero.
"""
from __future__ import annotations

from collections import defaultdict, deque
from copy import deepcopy
from decimal import Decimal
import hashlib
import json


LEDGER_VERSION = "trade-journal-fifo-v1"


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def decimal(value):
    return Decimal(str(value)) if value is not None else None


def money(value):
    return round(float(value), 8) if value is not None else None


def _delta(event):
    return -event["quantity"] if event["side"] == "sell" else event["quantity"]


def _ordered(rows):
    """Reconcile reported post-event balances without inventing execution times."""
    groups = defaultdict(list)
    for row in rows:
        groups[row["date"]].append(deepcopy(row))
    ordered, repairs, conflicts, current = [], [], [], None
    for day, events in sorted(groups.items()):
        events.sort(key=lambda r: (r.get("inventory_order", 10**9), r.get("time") or "", r["event_id"]))
        if current is None:
            first = events[0]
            after = first.get("reported_quantity_after")
            current = after - _delta(first) if after is not None else max(0, -_delta(first))
            if current < 0:
                conflicts.append({"code": "negative_opening_inventory", "event_id": first["event_id"], "date": day,
                    "message": "首笔买入后的余额小于本笔股数，不能推导负期初；需核对缺单或顺序"})
                current = 0
        start = current
        check = current
        valid = True
        for event in events:
            check += _delta(event)
            if check < 0 or (event.get("reported_quantity_after") is not None and check != event["reported_quantity_after"]):
                valid = False
        chosen = events
        if not valid and all(r.get("reported_quantity_after") is not None for r in events):
            solutions, searched, exhausted = [], 0, False
            def visit(qty, left, path):
                nonlocal searched, exhausted
                searched += 1
                if searched > 10000 or len(path) > 200:
                    exhausted = True
                    return
                if len(solutions) >= 2 or exhausted:
                    return
                if not left:
                    solutions.append(path)
                    return
                for i, candidate in enumerate(left):
                    if qty + _delta(candidate) == candidate["reported_quantity_after"]:
                        visit(candidate["reported_quantity_after"], left[:i] + left[i+1:], path + [candidate])
            visit(start, events, [])
            if exhausted:
                conflicts.append({"code": "inventory_order_search_limit", "date": day,
                    "message": "同日库存顺序无法在有限预算内唯一核对，需提供券商真实成交顺序"})
            elif len(solutions) == 1:
                chosen = solutions[0]
                repairs.append({"date": day, "event_ids": [r["event_id"] for r in chosen],
                    "reason": "按明确的前后库存唯一排序；原始时间保留，不推定撮合时刻"})
            elif len(solutions) > 1:
                conflicts.append({"code": "ambiguous_inventory_order", "date": day,
                    "message": "多种库存顺序均成立，需要核对真实成交顺序"})
        for event in chosen:
            before, after = current, current + _delta(event)
            reported = event.get("reported_quantity_after")
            if after < 0 or (reported is not None and after != reported):
                conflicts.append({"code": "inventory_mismatch", "event_id": event["event_id"],
                    "date": day, "expected_after": after, "reported_after": reported,
                    "message": "成交股数与券商股票余额不连续，需核对缺单或公司行为"})
            event["quantity_before"] = before
            event["quantity_after"] = after
            current = after
            ordered.append(event)
    return ordered, repairs, conflicts


def build_ledger(fills, financial_events=()):
    """Return stable zero-to-zero episodes, FIFO matches and balance diagnostics."""
    groups, costs, financial_by_ticker = defaultdict(list), defaultdict(list), defaultdict(list)
    for raw in financial_events:
        event = deepcopy(raw)
        financial_by_ticker[(event["account_alias"], event["ticker"])].append(event)
        if event["kind"] == "ipo_payment":
            target = event.get("linked_ticker") or event["ticker"]
            costs[(event["account_alias"], target)].append(event)
    for raw in fills:
        event = deepcopy(raw)
        event["event_id"] = event["fill_id"]
        groups[(event["account_alias"], event["ticker"])].append(event)
    for raw in financial_events:
        if raw["kind"] != "stock_credit":
            continue
        event = deepcopy(raw)
        event.update(event_id=event["financial_event_id"], side="credit", fees=0.0,
            date=event.get("settlement_date") or event["date"], time="", inventory_order=-1)
        groups[(event["account_alias"], event["ticker"])].append(event)

    episodes, all_fills, all_matches, repairs, conflicts = [], [], [], [], []
    for (account, ticker), rows in sorted(groups.items()):
        ordered, changes, errors = _ordered(rows)
        repairs.extend({"ticker": ticker, **r} for r in changes)
        conflicts.extend({"ticker": ticker, **r} for r in errors)
        active, lots, used_payments = None, deque(), set()
        for event in ordered:
            if active is None:
                opening = event["quantity_before"]
                episode_id = "ep-" + digest([account, ticker, event["event_id"]])[:28]
                active = {"episode_id": episode_id, "account_alias": account, "ticker": ticker,
                    "code": ticker.split(".")[0], "name": event.get("name") or ticker,
                    "names": [], "status": "open", "start_date": event["date"], "end_date": None,
                    "opening_quantity": opening, "opening_cost_status": "unknown" if opening else "known",
                    "opening_balance_verified": event.get("reported_quantity_after") is not None,
                    "quantity": opening, "buy_count": 0, "sell_count": 0, "buy_quantity": 0,
                    "sell_quantity": 0, "fees": 0.0, "buy_cost": 0.0, "sell_proceeds": 0.0,
                    "realized_pnl": 0.0, "realized_known_component": 0.0, "closed_pnl": None,
                    "unrealized_pnl": None, "remaining_cost": None, "fill_ids": [],
                    "financial_event_ids": [], "matches": [], "inventory_valid": not errors,
                    "unknown_cost_sold_quantity": 0, "unknown_fees": False, "ledger_version": LEDGER_VERSION,
                    "cash_net": Decimal(0), "_realized": Decimal(0), "_fees": Decimal(0),
                    "_buy_cost": Decimal(0), "_proceeds": Decimal(0), "_cash_known": True}
                lots = deque()
                if opening:
                    lots.append({"lot_id": "opening-" + episode_id, "quantity": opening, "unit_cost": None})
            if event.get("name") and event["name"] not in active["names"]:
                active["names"].append(event["name"])
            event["episode_id"] = active["episode_id"]
            quantity = event["quantity"]
            if event["side"] == "credit":
                payments = [r for r in costs[(account, ticker)] if r["quantity"] == quantity and r["date"] <= event["date"] and r["financial_event_id"] not in used_payments]
                payment = payments[0] if len(payments) == 1 else None
                cost = -decimal(payment["net_cash"]) if payment and payment.get("net_cash") is not None else None
                lots.append({"lot_id": event["event_id"], "quantity": quantity,
                    "unit_cost": cost / quantity if cost is not None else None})
                active["financial_event_ids"].append(event["financial_event_id"])
                if payment:
                    used_payments.add(payment["financial_event_id"])
                    active["financial_event_ids"].append(payment["financial_event_id"])
                if cost is None:
                    active["opening_cost_status"] = "unknown"
                    active["_cash_known"] = False
                else:
                    active["_buy_cost"] += cost
                    active["cash_net"] -= cost
            else:
                active["fill_ids"].append(event["fill_id"])
                fee = decimal(event.get("fees"))
                net = decimal(event.get("net_cash"))
                if fee is None or net is None:
                    active["unknown_fees"] = True
                    active["_cash_known"] = False
                if fee is not None:
                    active["_fees"] += fee
                if net is not None:
                    active["cash_net"] += net
                if event["side"] == "buy":
                    active["buy_count"] += 1
                    active["buy_quantity"] += quantity
                    cost = -net if net is not None else None
                    lots.append({"lot_id": event["fill_id"], "quantity": quantity,
                        "unit_cost": cost / quantity if cost is not None else None})
                    if cost is not None:
                        active["_buy_cost"] += cost
                else:
                    active["sell_count"] += 1
                    active["sell_quantity"] += quantity
                    if net is not None:
                        active["_proceeds"] += net
                    left = quantity
                    while left and lots:
                        lot = lots[0]
                        matched = min(left, lot["quantity"])
                        proceeds = net * matched / quantity if net is not None else None
                        cost = lot["unit_cost"] * matched if lot["unit_cost"] is not None else None
                        pnl = proceeds - cost if proceeds is not None and cost is not None else None
                        match = {"episode_id": active["episode_id"], "buy_lot_id": lot["lot_id"],
                            "sell_fill_id": event["fill_id"], "quantity": matched,
                            "buy_cost": money(cost), "sell_proceeds": money(proceeds), "realized_pnl": money(pnl)}
                        active["matches"].append(match)
                        all_matches.append(match)
                        if pnl is None:
                            active["unknown_cost_sold_quantity"] += matched
                        else:
                            active["_realized"] += pnl
                        lot["quantity"] -= matched
                        left -= matched
                        if not lot["quantity"]:
                            lots.popleft()
                    if left:
                        active["unknown_cost_sold_quantity"] += left
                        active["inventory_valid"] = False
                all_fills.append(event)
            active["quantity"] = event["quantity_after"]
            if not active["quantity"]:
                active.update(status="closed", end_date=event["date"], end_time=event.get("time"))
            active["realized_known_component"] = money(active["_realized"])
            complete = not active["unknown_cost_sold_quantity"] and not active["unknown_fees"] and active["inventory_valid"]
            active["realized_pnl"] = money(active["_realized"]) if complete else None
            remaining_known = all(lot["unit_cost"] is not None for lot in lots)
            active["remaining_cost"] = money(sum(lot["unit_cost"] * lot["quantity"] for lot in lots)) if remaining_known else None
            active.update(fees=money(active["_fees"]) if not active["unknown_fees"] else None,
                buy_cost=money(active["_buy_cost"]) if active["_cash_known"] else None,
                sell_proceeds=money(active["_proceeds"]) if active["_cash_known"] else None)
            if active["status"] == "closed":
                cash_complete = complete and active["_cash_known"] and active["opening_cost_status"] == "known"
                active["closed_pnl"] = money(active["cash_net"]) if cash_complete else None
                if cash_complete and abs(active["cash_net"] - active["_realized"]) > Decimal("0.000001"):
                    active["inventory_valid"] = False
                    conflicts.append({"code": "fifo_cash_mismatch", "episode_id": active["episode_id"],
                        "message": "FIFO结果与完整清仓净现金流不一致"})
                    active["closed_pnl"] = None
                episodes.append({k: money(v) if isinstance(v, Decimal) else v for k, v in active.items() if not k.startswith("_")})
                active = None
        if active:
            episodes.append({k: money(v) if isinstance(v, Decimal) else v for k, v in active.items() if not k.startswith("_")})
    return {"version": LEDGER_VERSION, "episodes": episodes, "fills": all_fills,
        "matches": all_matches, "repairs": repairs, "conflicts": conflicts}
