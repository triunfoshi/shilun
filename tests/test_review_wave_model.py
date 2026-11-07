"""A rejected impulse count must not keep a bullish wave label or target."""
import pandas as pd

from shilun.market.part1 import _build_wave_analysis, _wave_result


def test_invalid_impulse_keeps_observed_pivots_but_removes_inference():
    # Regression values from the 30 October cached count, not a trading fixture.
    points = [
        {"date": "2026-08-25", "price": 3850.86, "kind": "L"},
        {"date": "2026-09-01", "price": 3995.18, "kind": "H"},
        {"date": "2026-09-16", "price": 3842.72, "kind": "L"},
        {"date": "2026-09-22", "price": 3967.68, "kind": "H"},
        {"date": "2026-10-23", "price": 3574.27, "kind": "L"},
    ]
    levels = {"swing_highs": [p for p in points if p["kind"] == "H"],
              "swing_lows": [p for p in points if p["kind"] == "L"]}
    result = _build_wave_analysis(pd.DataFrame([{"close": 3599.02, "amount_ratio_5": 1.0}]), levels, {})
    assert result["current_wave"] == "unclear"
    assert result["wave_phase"] == "unknown"
    assert result["action"] == "wait"
    assert result["structure_status"] == "invalidated"
    assert result["fibonacci_levels"] == []
    assert len(result["wave_rule_violations"]) == 2
    assert [p["price"] for p in result["wave_points"]] == [p["price"] for p in points]
    assert all(p["label"] in ("观察高点", "观察低点") for p in result["wave_points"])
    assert "完整" not in result["reasoning"] and "进入第5浪" not in result["reasoning"]
    # Explicitly preserve existing scoring, rather than changing permission rules.
    assert result["score_adjustment"] == -10


def test_valid_impulse_is_not_blanket_rejected_and_labels_are_chinese():
    points = [{"date": f"2026-09-0{i+1}", "price": v, "kind": k}
              for i, (v, k) in enumerate([(100, "L"), (120, "H"), (110, "L"), (150, "H"), (130, "L")])]
    levels = [{"label": "测试目标", "level": 160, "kind": "target"}]
    result = _wave_result("wave_5", "第5浪假设", "impulse", "medium", "结构待验证", levels, [],
                          "wait", "等待验证", -10, points,
                          ["wave_1_start", "wave_1_end", "wave_2_end", "wave_3_end", "wave_4_end"])
    assert result["current_wave"] == "wave_5"
    assert result["fibonacci_levels"] == levels
    assert result["wave_points"][0]["label"] == "第1浪起点"


def test_abc_context_warning_is_not_misclassified_as_impulse_rule_failure():
    result = _wave_result("wave_c", "C浪假设", "corrective", "low", "调整待确认", [],
                          ["B浪高点超过A浪起点，可能是新一轮推动"], "wait", "观察", -5,
                          [{"date": "2026-09-01", "price": 100, "kind": "H"}], ["A浪起点"])
    assert result["current_wave"] == "wave_c"
    assert result["structure_status"] == "hypothesis"
