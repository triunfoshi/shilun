from __future__ import annotations

from shilun.market.market_v2 import build_market_v2


def _payload() -> dict:
    return {
        "analysis_date": "2026-07-15",
        "metrics": {
            "index_open": 3940,
            "index_high": 3950,
            "index_low": 3898,
            "index_close": 3913.79,
            "index_pct_chg": -0.0206,
            "index_ma5": 3938,
            "index_ma10": 3928,
            "index_ma20": 3905,
            "ma5_slope": -0.003,
            "amount": 900_000,
            "amount_ma5": 800_000,
            "amount_ratio_5": 1.125,
            "gap_state": "向下跳空，日内已回补",
            "up_count": 1000,
            "up_count_ma5": 2000,
            "down_count": 4200,
            "down_count_ma5": 3000,
            "limit_up_count": 30,
            "limit_up_count_ma5": 40,
            "limit_down_count": 55,
            "limit_down_count_ma5": 22,
        },
        "levels": {"support_1": 3938, "support_2": 3900, "pressure_1": 3970},
        "cross_index_context": {
            "items": [
                {"ticker": "399006.SZ", "name": "创业板指", "pct_chg": -0.031, "state": "明显走弱"},
                {"ticker": "000300.SH", "name": "沪深300", "pct_chg": -0.0196, "state": "回落"},
            ]
        },
    }


def test_market_v2_does_not_fake_missing_sector_or_futures_scores():
    result = build_market_v2(_payload())

    assert result["style_rotation"]["growth_pressure"] == -0.0114
    assert result["style_rotation"]["conclusion"] == "成长风格退潮"
    assert result["core_sectors"]["score"] is None
    assert result["market_health"]["modules"]["institution"]["score"] is None
    assert result["market_health"]["coverage"] == 0.55
    assert result["index_structure"]["gap_state"] == "向下跳空，日内已回补"
    assert result["sentiment"]["up_count_vs_ma5"] == -0.5
    assert result["sentiment"]["down_count_vs_ma5"] == 0.4
    assert result["sentiment"]["limit_up_count_vs_ma5"] == -0.25
    assert result["sentiment"]["limit_down_count_vs_ma5"] == 1.5


def test_market_v2_uses_all_five_weighted_modules_when_available():
    sector_payload = {
        "analysis_date": "2026-07-15",
        "all_sectors": [{
            "sector_name": "半导体",
            "metrics": {
                "return_1d": -0.02,
                "amount": 100,
                "amount_ma5": 80,
                "limit_up_count": 1,
                "big_up_count": 2,
                "main_net_inflow": -10000,
            },
            "fund_flow": {"as_of_date": "2026-07-15", "is_stale": False},
            "leader_candidates": [{"ticker": "000001.SZ", "name": "示例", "leader_score": 80}],
        }],
    }
    futures = {
        "status": "ok",
        "products": [],
        "institutional_summary": {"available": True, "risk_score": 65, "behavior": "主力净空增加"},
    }

    result = build_market_v2(_payload(), sector_payload=sector_payload, institutional_futures=futures)

    assert result["market_health"]["coverage"] == 1.0
    assert result["market_health"]["complete"] is True
    assert result["core_sectors"]["source_scope"] == "all_sectors"
    assert result["core_sectors"]["funds_date"] == "2026-07-15"
    assert result["core_sectors"]["funds_date_is_stale"] is False
    assert result["capital_behavior"]["mainline"] == "主线资金撤离"
