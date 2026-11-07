"""Shared, versioned research rules. Thresholds are hypotheses, not buy signals."""
from dataclasses import asdict, dataclass

VERSION = 'short-term-theme-rules-v1'
CORE_WINDOW = 10
CORE_MIN_BOARDS = 5
CORE_MIN_STREAK = 3
DRAWDOWN_WARNING = .15


@dataclass(frozen=True)
class SelectionRules:
    diffusion_sessions: int = 3
    minimum_followers: int = 2
    minimum_active_dates: int = 2
    core_active_sessions: int = 7
    maximum_directions: int = 3

    def __post_init__(self):
        if (min(self.diffusion_sessions, self.minimum_followers, self.minimum_active_dates,
                self.core_active_sessions, self.maximum_directions) < 1
                or self.minimum_active_dates > self.diffusion_sessions or self.maximum_directions > 3):
            raise ValueError('筛选窗口、数量或最多三个方向配置无效')


@dataclass(frozen=True)
class CycleRules:
    feedback_sessions: int = 3
    weakness_sessions: int = 3
    warning_weakness_sessions: int = 2
    recovery_sessions: int = 2
    replacement_sessions: int = 2
    restart_sessions: int = 2
    drawdown_warning: float = DRAWDOWN_WARNING

    def __post_init__(self):
        if min(self.feedback_sessions, self.weakness_sessions, self.warning_weakness_sessions, self.recovery_sessions,
               self.replacement_sessions, self.restart_sessions) < 2 or self.drawdown_warning != DRAWDOWN_WARNING:
            raise ValueError('单日分歧必须保留，收盘回撤预警固定为15%')


def rule_config(cycle=None):
    return {'version': VERSION, 'entry': asdict(SelectionRules()), 'cycle': asdict(cycle or CycleRules()),
            'core': {'window': CORE_WINDOW, 'boards': CORE_MIN_BOARDS, 'streak': CORE_MIN_STREAK}}
