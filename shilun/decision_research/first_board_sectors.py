"""Sector slices of first-board opening feedback, with explicit reconstruction scope.

Quartiles pool stock/event observations, not daily medians. Sector coordination
uses the formation day's whole membership, never the next day's price feedback.
"""
from math import floor
from statistics import median

from shilun.market.research_scope import topic_allowed
from .engine import number, ret, ratio, price_path_valid


def quantiles(values):
    values = sorted(v for v in values if number(v))
    def at(q):
        if len(values) < 3:
            return None
        index = (len(values)-1)*q
        i = floor(index)
        return values[i] + (values[min(i+1, len(values)-1)]-values[i])*(index-i)
    return {'p25':at(.25), 'p50':at(.5), 'p75':at(.75), 'n':len(values)}


def feedback_stats(rows):
    # Membership overlap must never duplicate an event within one slice.
    unique = {(r['ticker'],r['feedback_date']):r for r in rows}
    values = [r.get('opening') for r in unique.values()]
    good = [v for v in values if number(v)]
    return {**quantiles(values), 'total':len(values), 'missing':len(values)-len(good),
        'positive_pct':100*sum(v>0 for v in good)/len(good) if good else None,
        'negative_pct':100*sum(v<0 for v in good)/len(good) if good else None,
        'dates_n':len({r['feedback_date'] for r in unique.values()}),
        'stock_n':len({r['ticker'] for r in unique.values()}),
        'status':'insufficient' if len(good)<3 else 'partial' if len(good)<len(values)*.95 else 'small' if len(good)<5 else 'descriptive'}


def coordination(e, ticks, day, streaks=None):
    if streaks is None:
        from .playbook_assessment import streak_history
        streaks = streak_history(e)
    i=e.days.index(day)
    prior=e.days[i-1] if i else None
    pairs=[(t,e.row(t,prior),e.row(t,day)) for t in ticks]
    valid=[(t,p,r) for t,p,r in pairs if price_path_valid([p,r])]
    returns=[ret(r['close'],p['close']) for _,p,r in valid]
    limits=[(r['close'],e.limit.get((t,day))) for t,_,r in valid if number(e.limit.get((t,day)))]
    amounts=[(p.get('amount'),r.get('amount')) for _,p,r in pairs]
    full_amount=bool(amounts) and all(number(a) and a>0 and number(b) and b>0 for a,b in amounts)
    med=median(returns) if returns else None
    covered=bool(ticks) and len(valid)>=len(ticks)*.95
    # All sector members, independent of the feedback sample's capacity band.
    # A confirmed non-limit close is known not to be first-board; a sealed close
    # with unknown prior streak remains unknown rather than counted as zero.
    classified=[streaks.get(day,{}).get(t) for t in ticks]
    first_n=sum(s==1 for s in classified)
    first_valid=sum(s is not None for s in classified)
    first_members=[{'ticker':t,'name':e.info.get(t,{}).get('name',t)}
                   for t,s in zip(ticks,classified) if s==1]
    return {'date':day,'member_n':len(ticks),'valid_n':len(valid),'coverage_complete':covered,
        'first_board_n':first_n if first_valid else None,
        'first_board_valid_n':first_valid,'first_board_complete':first_valid==len(ticks),
        'first_board_members':first_members,
        'median_pct':med if covered else None,
        'limit_up_n':sum(abs(close-u)<.005 for close,u in limits) if limits else None,
        'limit_valid_n':len(limits),
        'amount_ratio':ratio(sum(b for a,b in amounts),sum(a for a,b in amounts)) if full_amount else None}


def build_sector_feedback(e, data, daily):
    from .playbook_assessment import streak_history
    streaks=streak_history(e)
    taxonomy=data.get('taxonomy') or {}
    raw=taxonomy.get('rows',[]) if taxonomy.get('date')==e.date else []
    days=[r['date'] for r in daily]
    events=[{**r, 'feedback_date':d['date'], 'formation_date':d['formation_date']}
            for d in daily for r in d['members']]
    def slices(rows):
        return {str(n):feedback_stats([r for r in rows if r['feedback_date'] in days[-n:]]) for n in (3,5)}
    groups=[]
    for group in raw:
        if not group.get('research_eligible') or not topic_allowed(group):
            continue
        kind=group.get('kind')
        if kind!='concept' and group.get('industry_level')!='l3':
            continue
        ticks=sorted({r['con_code'] for r in group.get('members',[]) if r.get('con_code')})
        if not ticks or group.get('member_count')!=len(ticks):
            continue
        tick_set=set(ticks)
        rows=[r for r in events if r['ticker'] in tick_set]
        if not rows:
            continue
        dates={}
        for d in daily:
            selected=[r for r in rows if r['feedback_date']==d['date']]
            dates[d['date']]={**feedback_stats(selected), 'formation_date':d['formation_date'],
                'coordination':coordination(e,ticks,d['formation_date'],streaks)}
        groups.append({'code':group['code'],'name':group['name'],
            'kind':'concept' if kind=='concept' else 'industry',
            'source':group.get('source_label'), 'membership_date':group.get('membership_date'),
            'fetched_at':group.get('fetched_at'), 'member_ref':group.get('member_ref'),
            'members':ticks,'windows':slices(rows),'daily':dates})
    # Catalogue order is stable; UI sorting is descriptive and never selects a trade.
    groups.sort(key=lambda r:(r['kind'],r['name'],r['code']))
    return {'groups':groups,'dates':days,'windows':slices(events),
        'daily':{d['date']:feedback_stats([r for r in events if r['feedback_date']==d['date']]) for d in daily},
        'membership_date':taxonomy.get('date'), 'fetched_at':taxonomy.get('fetched_at'),
        'scope_note':'概念题材与三级行业分别展示。同股可属多个板块，同板块内同股同次首板只计一次，各板块样本不能相加。使用评估日成分快照回看历史，为归属重建，不证明首板当时已知成员。',
        'quantile_rule':'逐股计算（次日开盘÷首板收盘−1）×100%，按收益排序，以(n−1)×q位置作线性插值：q=25%/50%/75%。低中高为分布位置，不是买点或三个收益等级。近3/5日按股票事件汇总，区别于上方日中位数再取中位数；有效样本不足3只不报分位数，3—4只标小样本。',
        'coordination_rule':'板块全部个股数取成分快照去重家数；同日首板取全板块前日未封板、当日收盘涨停的家数，不受50／100亿容量筛选限制，也不把连板计入首板。分类缺行情时显示已确认数及覆盖，不补成0。板块当日涨幅中位数按全板块个股对前收涨跌幅等权取中位数，价格覆盖须达到95%。同日首板用于观察共同发酵，不能证明分钟级同时启动。下方收益样本仍受当前容量范围限制。',
        'capital_rule':'首板日流通市值优先取同日daily_basic.circ_mv×10000；缺该字段时以同日float_share（万股）×10000×当日原始收盘重建。首板成交额为同日日线amount（千元）×1000。无同日数据不拿今日值补。'}
