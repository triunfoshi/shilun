#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum MarketPermission {
    Attack,
    Hold,
    Defense,
    Empty,
}

impl MarketPermission {
    pub fn as_code(self) -> &'static str {
        match self {
            MarketPermission::Attack => "attack",
            MarketPermission::Hold => "hold",
            MarketPermission::Defense => "defense",
            MarketPermission::Empty => "empty",
        }
    }

    pub fn label_zh(self) -> &'static str {
        match self {
            MarketPermission::Attack => "进攻",
            MarketPermission::Hold => "持有",
            MarketPermission::Defense => "防守",
            MarketPermission::Empty => "空仓",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Part1HardTrigger {
    Defense,
    Empty,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct MarketScores {
    pub trend_score: i32,
    pub volume_score: i32,
    pub breadth_score: i32,
    pub theme_score: i32,
    pub risk_score: i32,
}

impl MarketScores {
    pub fn total_score(self) -> i32 {
        self.trend_score + self.volume_score + self.breadth_score + self.theme_score - self.risk_score
    }
}

pub fn classify_market_permission(scores: MarketScores, hard_triggers: &[Part1HardTrigger]) -> MarketPermission {
    if hard_triggers.iter().any(|trigger| *trigger == Part1HardTrigger::Empty) {
        return MarketPermission::Empty;
    }

    let total_score = scores.total_score();
    if scores.risk_score <= 1
        && scores.trend_score >= 3
        && scores.breadth_score >= 1
        && scores.theme_score >= 1
        && total_score >= 5
    {
        return MarketPermission::Attack;
    }

    if hard_triggers.iter().any(|trigger| *trigger == Part1HardTrigger::Defense) {
        return MarketPermission::Defense;
    }

    if (1..=4).contains(&total_score) && scores.risk_score <= 2 {
        return MarketPermission::Hold;
    }

    if (-3..=0).contains(&total_score) || scores.risk_score >= 3 {
        return MarketPermission::Defense;
    }

    if total_score < -3 {
        return MarketPermission::Empty;
    }

    MarketPermission::Hold
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn attack_requires_low_risk_and_confirmation() {
        let scores = MarketScores {
            trend_score: 6,
            volume_score: 2,
            breadth_score: 2,
            theme_score: 2,
            risk_score: 0,
        };

        assert_eq!(MarketPermission::Attack, classify_market_permission(scores, &[]));
    }

    #[test]
    fn hard_empty_trigger_has_priority() {
        let scores = MarketScores {
            trend_score: 6,
            volume_score: 2,
            breadth_score: 2,
            theme_score: 2,
            risk_score: 0,
        };

        assert_eq!(
            MarketPermission::Empty,
            classify_market_permission(scores, &[Part1HardTrigger::Empty])
        );
    }
}
