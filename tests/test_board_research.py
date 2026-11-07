"""Financial denominators, Vibe parity, dated cohorts and interrupted research."""
import asyncio
import gzip
import json
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from shilun.review.board_effects import build_effects, return_pct, stats, seal_time
from shilun.review.board_data import BoardResearchData, normalize_pool, fetch_historical_pool
from shilun.review import research_ai


def row(code="000001", ret=1, boards=1, **extra):
    return dict(code=code, name=code, ret=ret, prev_boards=boards, close=11,
                limit_price=11, seal_time="093000", sector="电子", **extra)


class BoardEffectsTests(unittest.TestCase):
    def test_population_retains_unknown_seal_and_missing_feedback(self):
        day = '2026-09-15'
        unknown_time = {**row('000002', ret=-3), 'seal_time': None}
        missing_return = {**row('000003', ret=None), 'seal_time': None}
        result = build_effects(dates=['2026-09-14', day], per_day={day: [row()]},
            industry_cohorts={day: [row(), unknown_time, missing_return]},
            broken_by_day={day: [row('000004', ret=8)]})
        population = next(g for g in result['strategies'] if g['id'] == 'all')
        assert population['name'] == '全体涨停'
        assert population['overall']['sample_total'] == 3
        assert population['overall']['sample'] == 2
        assert population['overall']['avg'] == -1
        assert population['excess'] is None
        assert population['comparison_role'] == 'population'
        assert result['strategies'][0]['excess'] == 2
        assert result['strategies'][-1]['excess'] is None
        assert result['strategies'][-1]['comparison_role'] == 'independent_risk'

    def test_industry_missing_mapping_is_unknown_not_empty_or_full_coverage(self):
        dates = ['2026-09-14', '2026-09-15', '2026-09-16', '2026-09-17']
        cohorts = {dates[1]: [{**row(), 'sector': '', 'classification_status': 'taxonomy_missing'}],
                   dates[2]: [], dates[3]: [row()]}
        g = build_effects(dates=dates, per_day=cohorts, industry_cohorts=cohorts)['strategies'][3]
        assert not g['daily'][0]['available']
        assert g['daily'][0]['missing_reason'] == '首板日行业分类待补齐'
        assert g['daily'][1]['available'] and g['daily'][1]['sample_total'] == 0
        assert g['coverage']['days'] == 2
        assert g['coverage']['missing'] == [dates[1]]
        assert g['daily'][2]['rolling']['covered_days'] == 2

    def test_industry_ranking_keeps_full_pool_and_missing_prices(self):
        prior, day = '2026-09-14', '2026-09-15'
        pool = [{**row('000001', ret=None), 'sector': 'A'},
                {**row('000002', boards=2), 'sector': 'A', 'seal_time': None},
                {**row('000003', ret=-2), 'sector': 'B'},
                {**row('000004'), 'sector': 'C'}]
        # Feedback-only ranking would select B/C; the complete prior pool picks A/B.
        g = build_effects(dates=[prior, day], per_day={day: pool[2:]},
                          industry_cohorts={day: pool})['strategies'][3]
        assert {r['code'] for r in g['rows']} == {'000001', '000003'}
        assert g['overall']['sample_total'] == 2 and g['overall']['sample'] == 1
        assert g['overall']['avg'] == -2
        assert g['daily'][0]['industry_coverage']['top_industries'][0]['limit_up_count'] == 2

    def test_feedback_date_classification_cannot_replace_formation_day(self):
        prior, day = '2026-09-14', '2026-09-15'
        pool = [{**row(), 'taxonomy_date': day}]
        g = build_effects(dates=[prior, day], per_day={day: pool}, industry_cohorts={day: pool})['strategies'][3]
        assert not g['daily'][0]['available']

    def test_adapter_joins_industries_on_previous_session(self):
        prior, day = '2026-09-14', '2026-09-15'
        store = MagicMock()
        store.collection.return_value.find.return_value = []
        data = BoardResearchData(store)
        pool = normalize_pool([dict(c='000001', n='甲', lbc=1, lbt='143000', p=10000)], eastmoney=True)
        def classify(_, target, records, **kwargs):
            assert target == prior and kwargs['industry_only'] is True
            return [{**r, 'industry': '零售', 'industry_code': 'BK0001.DC',
                     'taxonomy_date': target, 'classification_status': 'ready'} for r in records]
        with patch.object(data, 'sessions', return_value=[prior, day]), patch.object(data, 'pools', side_effect=[{prior: {'stocks': pool}}, {}]), patch('shilun.market.taxonomy.classify_stocks', side_effect=classify) as mapper:
            result = data.effects(target=day)
        assert mapper.call_count == 1
        assert result['strategies'][3]['rows'][0]['taxonomy_date'] == prior
        assert result['strategies'][3]['overall']['sample_total'] == 1

    def test_optional_daily_reference_preserves_ex_rights_basis(self):
        import pandas as pd
        from shilun.data.providers.tushare_provider import TushareDailyClient
        raw = pd.DataFrame([dict(ts_code="000001.SZ",trade_date="20260911",open=8,high=9,low=8,close=8.8,vol=100,amount=800,pre_close=8)])
        old = TushareDailyClient._normalize_daily_frame(raw)
        new = TushareDailyClient._normalize_daily_frame(raw,include_reference=True)
        self.assertNotIn("pre_close", old.columns)
        self.assertEqual(8, new.iloc[0]["pre_close"])
        self.assertAlmostEqual(10, return_pct(new.iloc[0]["close"],new.iloc[0]["pre_close"]))

    def test_history_source_rejects_wrong_day_or_wrong_pool_type(self):
        import pandas as pd
        client = MagicMock()
        item = dict(ts_code="000001.SZ",trade_date="20260911",limit="U",name="甲",close=11,
                    limit_times=4,first_time="93000",last_time="143000",float_mv=2e9,amount=2e8)
        client._call_api.return_value = pd.DataFrame([item])
        result = fetch_historical_pool(client,"2026-09-11","up")
        self.assertEqual(4,result["stocks"][0]["boards"])
        self.assertEqual(2e9,result["stocks"][0]["float_cap"])
        self.assertEqual("093000",result["stocks"][0]["seal_time"])
        for target,kind in [("2026-09-10","up"),("2026-09-11","broken")]:
            with self.assertRaises(ValueError):fetch_historical_pool(client,target,kind)

    def test_four_boards_can_be_highest_without_changing_three_plus(self):
        day="2026-09-11"
        result=build_effects(dates=["2026-09-10",day],per_day={day:[row(boards=4,is_highest=True),row("000002",-1,3)]},highest_ready={day})
        self.assertEqual(2,result["strategies"][5]["overall"]["sample"])
        self.assertEqual(1,result["strategies"][-2]["overall"]["sample"])

    def test_frozen_vibe_seven_groups_and_seal_curve(self):
        with gzip.open(Path(__file__).parent/"fixtures/board_effects_vibe.json.gz", "rt") as f:
            fixture = json.load(f)
        args = {k: fixture[k] for k in ("dates", "per_day", "heat")}
        corrected = build_effects(**args)
        # Reconstruct Vibe's treatment of its 000000 sentinel solely for parity.
        # Production excludes these three unknown times instead of calling them early.
        with patch("shilun.review.board_effects.seal_time", side_effect=lambda v: "000000" if v == "000000" else seal_time(v)):
            result = build_effects(**args)
        self.assertEqual(356, corrected["strategies"][1]["overall"]["sample"])
        self.assertEqual(1.27, corrected["strategies"][1]["overall"]["avg"])
        self.assertIn("3条", corrected["quality_note"])
        self.assertEqual(3, corrected["excluded_count"])
        self.assertEqual(5, len(corrected["seal_curve"]))
        self.assertEqual(result["strategies"][0]["overall"]["sample"]-3, corrected["strategies"][0]["overall"]["sample"])
        self.assertEqual(result["strategies"][6]["overall"]["sample"], corrected["strategies"][6]["overall"]["sample"])
        expected = fixture["expected"]
        for key in ("days_used", "date_from", "date_to", "missing_days"):
            self.assertEqual(expected[key], result[key], key)
        for actual, (name, original) in zip(result["strategies"], expected["strategies"].items()):
            with self.subTest(group=name):
                for key, value in original["overall"].items():
                    self.assertEqual(value, actual["overall"][key], key)
                daily = {d["date"]:d for d in actual["daily"]}
                for day in original["daily"]:
                    for key in ("sample", "avg"):
                        self.assertEqual(day[key], daily[day["date"]][key])
        for actual, original in zip(result["seal_curve"], expected["seal_curve"]):
            for key, value in original.items():
                if key != "bucket": self.assertEqual(value, actual[key])
        self.assertEqual(-.37, result["strategies"][0]["excess"])
        self.assertEqual(.23, result["strategies"][1]["excess"])

    def test_recent_is_calendar_seven_and_median_is_pooled(self):
        dates = [f"2026-09-{i:02}" for i in range(1,11)]
        per_day = {dates[1]:[row(ret=90)], dates[3]:[row(ret=-2)],
                   dates[-1]:[row(code=f"{i:06}",ret=2) for i in range(10)]}
        result = build_effects(dates=dates, per_day=per_day)
        first = result["strategies"][0]
        self.assertEqual(2, first["recent"]["median"])
        self.assertEqual(11, first["recent"]["sample"])
        self.assertEqual(90, first["prior"]["avg"])
        self.assertEqual(2, first["daily"][-1]["rolling"]["covered_days"])
        self.assertIsNone(first["daily"][-2]["median"])

    def test_last_seal_selects_early_not_first_seal(self):
        raw = [{"c":"000001","n":"甲","p":11000,"lbc":1,"fbt":"093000","lbt":"145900"}]
        stock = normalize_pool(raw,eastmoney=True)[0]
        self.assertEqual("093000",stock["seal_time"])
        result = build_effects(dates=["2026-09-10","2026-09-11"],per_day={"2026-09-11":[{**row(),"seal_time":stock["last_seal_time"]}]})
        self.assertEqual(0,result["strategies"][1]["overall"]["sample"])
        self.assertEqual(1,result["strategies"][2]["overall"]["sample"])

    def test_empty_unknown_and_price_missing_are_distinct(self):
        d = ["2026-09-01","2026-09-02","2026-09-03","2026-09-04"]
        result = build_effects(dates=d,per_day={d[1]:[],d[3]:[row(ret=None)]})
        daily = result["strategies"][0]["daily"]
        self.assertTrue(daily[0]["available"])
        self.assertFalse(daily[1]["available"])
        self.assertEqual(1,daily[2]["sample_total"])
        self.assertEqual(0,daily[2]["sample"])
        self.assertIsNone(daily[2]["avg"])
        self.assertEqual([d[2],d[3]],result["missing_days"])

    def test_broken_rebound_is_not_limit_price_profit(self):
        r = row(ret=return_pct(10.2,10),limit_cost_ret=return_pct(10.2,11))
        result = build_effects(dates=["2026-09-10","2026-09-11"],per_day={},broken_by_day={"2026-09-11":[r]})
        broken = result["strategies"][-1]
        self.assertEqual(2,broken["overall"]["avg"])
        self.assertEqual(-7.27,broken["segments"]["limit_cost_ret"]["avg"])

    def test_limit_unknown_excluded_only_from_limit_denominator(self):
        r = [{**row(),"date":"2026-09-11"},{**row("000002",-1),"date":"2026-09-11","limit_price":None}]
        result = stats(r)
        self.assertEqual(2,result["sample"])
        self.assertEqual(1,result["limit_up_sample"])
        self.assertEqual(1,result["limit_up_rate"])
        self.assertEqual(.5,result["win_rate"])

    def test_duplicates_and_long_windows_rejected(self):
        with self.assertRaises(ValueError):
            build_effects(dates=["2026-09-10","2026-09-11"],per_day={"2026-09-11":[row(),row()]})
        for days in (7,21,60,90):
            with self.assertRaises(ValueError): build_effects(dates=[],per_day={},requested=days)

    def test_highest_uses_full_prior_pool_not_survivors(self):
        prior,day = "2026-09-10","2026-09-11"
        store = MagicMock()
        # The outcome provider omitted one failed eight-board stock; the prior pool retains it.
        previous = dict(analysis_date=day,previous_date=prior,source="fixture",rows=[
            dict(ticker="000004.SZ",name="四板",return_1d=.1,consecutive_count=4,close=11,up_limit=11)])
        price_rows = [dict(ticker="000008.SZ",trade_date="20260911",pre_close=20,up_limit=22)]
        def collection(name):
            c=MagicMock(); c.find.return_value = [previous] if name=="review_previous_limit_pools" else price_rows if name=="daily_price_limits" else [dict(ticker="000008.SZ",date=day,close=18,open=19)] if name=="market_daily_bars" else []
            return c
        store.collection.side_effect=collection
        store.find_daily_bars.return_value=[dict(ticker="000008.SZ",date=day,close=18,open=19)]
        up = normalize_pool([dict(c="000008",n="昨日最高",p=20000,lbc=8,lbt="143000"),dict(c="000009",n="并列最高",p=20000,lbc=8,lbt="143000"),dict(c="000004",n="四板",p=10000,lbc=4,lbt="143000")],eastmoney=True)
        data=BoardResearchData(store)
        with patch.object(data,"sessions",return_value=[prior,day]), patch.object(data,"pools",side_effect=[{prior:dict(stocks=up)},{}]):
            result=data.effects(target=day)
        highest=result["strategies"][-2]
        self.assertEqual({"000008","000009"},{r["code"] for r in highest["rows"]})
        self.assertEqual(-10,highest["overall"]["avg"])
        self.assertEqual(2,highest["overall"]["sample_total"])
        self.assertEqual(1,highest["overall"]["sample"])

    def test_api_rejects_invalid_window_before_database(self):
        from shilun.api.review import router
        app=FastAPI();app.include_router(router)
        with TestClient(app) as client, patch("shilun.api.review._open_store") as opening:
            for value in (7,21,60,90):
                self.assertEqual(422,client.get(f"/api/v1/review/board-effects?days={value}").status_code)
            opening.assert_not_called()


class ResearchStreamTests(unittest.IsolatedAsyncioTestCase):
    async def run_stream(self, events):
        store=MagicMock()
        body="".join("data: "+json.dumps(e)+"\n\n" for e in events)
        def factory(**kwargs):
            return httpx.AsyncClient(transport=httpx.MockTransport(lambda req:httpx.Response(200,text=body)),**kwargs)
        config=dict(ready=True,key="private-key",model="gpt-6-test",api="responses",base_url="https://provider.invalid")
        with patch.object(research_ai,"settings",return_value=config):
            output=[v async for v in research_ai.stream_analysis(dict(code="000001",name="甲"),"2026-09-11",store,client_factory=factory)]
        return output,store

    async def test_complete_persists_but_interrupted_keeps_previous_note(self):
        delta=dict(type="response.output_text.delta",delta="**已知事实**\n一条证据")
        output,store=await self.run_stream([delta,dict(type="response.completed",response=dict(status="completed"))])
        store.collection.return_value.update_one.assert_called_once()
        self.assertIn('"type": "done"',output[-1])
        for ending in ([],[dict(type="response.incomplete")]):
            output,store=await self.run_stream([delta,*ending])
            store.collection.return_value.update_one.assert_not_called()
            self.assertIn('"type": "error"',output[-1])
            self.assertNotIn("private-key","".join(output))

    async def test_cancel_propagates_without_saving(self):
        class Stream:
            status_code=200
            async def __aenter__(self):return self
            async def __aexit__(self,*args):pass
            async def aiter_lines(self):
                yield 'data: {"type":"response.output_text.delta","delta":"partial"}'
                raise asyncio.CancelledError()
        class Client:
            def __init__(self,**kwargs):pass
            async def __aenter__(self):return self
            async def __aexit__(self,*args):pass
            def stream(self,*args,**kwargs):return Stream()
        store=MagicMock()
        with patch.object(research_ai,"settings",return_value=dict(ready=True,key="secret",model="test",api="responses",base_url="https://invalid")):
            with self.assertRaises(asyncio.CancelledError):
                async for _ in research_ai.stream_analysis(dict(code="000001",name="甲"),"2026-09-11",store,client_factory=Client):pass
        store.collection.assert_not_called()


if __name__ == "__main__":unittest.main()
