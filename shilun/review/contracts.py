"""Stable vocabulary for the Shilun right-side arbitrage system.

These values intentionally separate four ideas that used to be mixed together:
market posture, stock role, tactic, and expectation result.  A stock may have one
exclusive role, while position shape and attack/defence nature remain independent
observations.
"""

from __future__ import annotations

from enum import StrEnum


class ExpectationGapType(StrEnum):
    EMOTION_CYCLE = "emotion_cycle"
    TAPE = "tape"


class ExpectationState(StrEnum):
    BELOW = "below_expectation"
    IN_LINE = "in_line"
    ABOVE = "above_expectation"
    CONSENSUS = "consensus"
    LOGIC_REVERSAL = "logic_reversal"
    INSUFFICIENT = "insufficient_samples"


class MarketPosture(StrEnum):
    TREND_ATTACK = "trend_attack"
    ROTATION = "rotation"
    EMOTION_DEFENCE = "emotion_defence"


class SectorCycle(StrEnum):
    STARTUP = "startup"
    DIVERGENCE = "divergence"
    DISTRIBUTION_OR_RETURN = "distribution_or_return"
    DECAY = "decay"
    UNCONFIRMED = "unconfirmed"


class StockRole(StrEnum):
    EMOTION_LEADER = "emotion_leader"
    CAPACITY_COMMANDER = "capacity_commander"
    TREND_CORE = "trend_core"
    BACK_ROW = "back_row"


class BoardNature(StrEnum):
    ATTACK = "attack"
    DEFENCE = "defence"
    NEUTRAL = "neutral"


class Tactic(StrEnum):
    RED_K_NET_INFLOW = "red_k_net_inflow"
    STRUCTURE_REPAIR_520 = "structure_repair_520"
    WEEKLY_BREAKOUT = "weekly_breakout"


EXPECTATION_LABELS = {
    ExpectationState.BELOW: "低于预期",
    ExpectationState.IN_LINE: "符合预期",
    ExpectationState.ABOVE: "超预期",
    ExpectationState.CONSENSUS: "预期一致",
    ExpectationState.LOGIC_REVERSAL: "逻辑反转",
    ExpectationState.INSUFFICIENT: "样本不足",
}


ROLE_LABELS = {
    StockRole.EMOTION_LEADER: "情绪龙头",
    StockRole.CAPACITY_COMMANDER: "容量中军",
    StockRole.TREND_CORE: "趋势核心",
    StockRole.BACK_ROW: "后排",
}


POSTURE_LABELS = {
    MarketPosture.TREND_ATTACK: "趋势进攻",
    MarketPosture.ROTATION: "轮动等待",
    MarketPosture.EMOTION_DEFENCE: "弱势抱团",
}


CYCLE_LABELS = {
    SectorCycle.STARTUP: "阶段1 · 启动/发酵",
    SectorCycle.DIVERGENCE: "阶段2 · 去弱留强",
    SectorCycle.DISTRIBUTION_OR_RETURN: "阶段3 · 散货/回流",
    SectorCycle.DECAY: "题材衰减",
    SectorCycle.UNCONFIRMED: "周期待确认",
}

