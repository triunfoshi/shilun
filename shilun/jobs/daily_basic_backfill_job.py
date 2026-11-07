"""Repair missing historical turnover rows only; no strategies or order writes."""
import argparse
from datetime import date, datetime, timezone
import json
from pathlib import Path

from shilun.decision_research.engine import number
from shilun.market.ma5_universe import iso


def missing_turnover(active, existing):
    valid = {r['ts_code'] for r in existing
             if number(r.get('turnover_rate')) and r['turnover_rate'] > 0}
    return sorted(set(active) - valid)


def repair_day(store, client, day, *, apply=False):
    """Only actual trading stocks require a turnover row; never synthesize one."""
    compact = day.replace('-', '')
    basics = {r['ts_code'] for r in store.collection('stock_basic').find({}, {'_id':0,'ts_code':1})}
    bars = list(store.collection('market_daily_bars').find({'date':day},
                {'_id':0,'ticker':1,'volume':1}))
    active = {r['ticker'] for r in bars if r['ticker'] in basics and number(r.get('volume')) and r['volume'] > 0}
    existing = list(store.collection('daily_basic').find({'trade_date':compact},
                    {'_id':0,'ts_code':1,'turnover_rate':1}))
    missing = missing_turnover(active, existing)
    report = {'date':day,'active_n':len(active),'missing_before':len(missing),
              'stored_n':0,'remaining_n':len(missing),'remaining_tickers':missing}
    if not apply or not missing:
        return report
    rows = client.fetch_daily_basic(trade_date=compact).to_dict('records')
    seen = set()
    for r in rows:
        if iso(r.get('trade_date')) != day or not r.get('ts_code') or r['ts_code'] in seen:
            raise ValueError('每日指标存在日期错误或重复证券，停止写入')
        seen.add(r['ts_code'])
    wanted = set(missing)
    payload = [{**r,'source':'tushare_daily_basic','repair_observed_at':datetime.now(timezone.utc).isoformat()}
               for r in rows if r['ts_code'] in wanted and number(r.get('turnover_rate')) and r['turnover_rate']>0]
    if payload:
        report['stored_n'] = store.upsert_daily_basic(payload)
    verified = list(store.collection('daily_basic').find({'trade_date':compact},
                    {'_id':0,'ts_code':1,'turnover_rate':1}))
    report['remaining_tickers'] = missing_turnover(active, verified)
    report['remaining_n'] = len(report['remaining_tickers'])
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--date',required=True,type=date.fromisoformat)
    parser.add_argument('--sessions',type=int,default=27)
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.date >= date.today() or not 1 <= args.sessions <= 120:
        parser.error('仅修复今日以前的历史交易日，窗口须为1—120日')
    from datetime import timedelta
    from shilun.api.review import _open_store
    from shilun.common.config import load_config
    from shilun.data import TushareConfig, TushareDailyClient
    from shilun.review.sources import load_review_sessions
    store=_open_store(); report={'as_of':args.date.isoformat(),'apply':args.apply,'days':[]}
    try:
        cfg=load_config()
        client=TushareDailyClient(TushareConfig(token=cfg.tushare_token or '',base_url=cfg.tushare_base_url or '',
            timeout=cfg.tushare_timeout,min_interval_seconds=cfg.tushare_min_interval_seconds)) if args.apply else None
        days=load_review_sessions(store,args.date.isoformat(),(args.date-timedelta(days=args.sessions*3+20)).isoformat())[-args.sessions:]
        for day in days:
            try:
                row=repair_day(store,client,day,apply=args.apply)
            except Exception as ex:
                row={'date':day,'error':type(ex).__name__}
            report['days'].append(row)
            args.output.parent.mkdir(parents=True,exist_ok=True)
            args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2))
            print(json.dumps({k:v for k,v in row.items() if k!='remaining_tickers'},ensure_ascii=False),flush=True)
    finally:
        store.close()
    return 1 if any(r.get('error') or (args.apply and r.get('remaining_n')) for r in report['days']) else 0


if __name__=='__main__':
    raise SystemExit(main())
