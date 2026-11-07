"""Dated sentiment-cycle and industry facts from already archived board pools.

Adapted from Vibe AStock e0d08d19848b50d22a772729a06287cdff511629,
.agents/skills/data-access/scripts/sources/shortline_emotion.py::cycle_position
and shortline_facts.py::theme_structure (Apache-2.0, licenses/vibe-astock.txt).
Changes: shared SH/SZ non-ST scope, strict settled dates and complete windows,
explicit field coverage, no provider request, no current-day substitution.
"""
from math import isfinite
import re

from shilun.review.board_data import settled_target
from shilun.review.board_dynamics import comparable_pools

VERSION = 'sentiment-context-v1'
SCOPE = '沪深非ST'
SLOTS = [('09:25—09:35','093500'), ('09:35—10:00','100000'),
         ('10:00—11:30','113000'), ('11:30—14:00','140000'), ('14:00—15:00','150000')]


def integer(value):
    return int(value) if isinstance(value,(int,float)) and not isinstance(value,bool) and isfinite(value) and value >= 1 and int(value) == value else None


def time_value(value):
    text = str(value or '').replace(':','')
    if not re.fullmatch(r'\d{6}',text):
        return None
    if not ('092500' <= text <= '113000' or '130000' <= text <= '150000'):
        return None
    return text if int(text[2:4]) < 60 and int(text[4:6]) < 60 else None


def rows_for(pools, day):
    doc = pools.get(day)
    if (not doc or not doc.get('complete') or doc.get('snapshot_mode') == 'live'
            or doc.get('date') != day or doc.get('metric_scope') != SCOPE):
        return None
    rows = doc.get('stocks')
    if not isinstance(rows,list):
        return None
    tickers = [row.get('ticker') for row in rows]
    if any(not ticker for ticker in tickers) or len(set(tickers)) != len(tickers):
        return None
    return rows


def day_facts(day, ups, broken):
    current, failed = rows_for(ups,day), rows_for(broken,day)
    if current is None:
        return {'date':day,'limit_up':None,'highest_consec':None,'broken_rate':None,'score':None,
                'reason':'同日完整收盘涨停池缺失或范围不可比'}
    heights = [integer(row.get('boards')) for row in current]
    upper = {row['ticker'] for row in current}
    broken_n = len({row['ticker'] for row in failed} - upper) if failed is not None else None
    attempts = len(current)+broken_n if broken_n is not None else None
    rate = broken_n/attempts if attempts else None
    reasons = []
    if any(height is None for height in heights):
        reasons.append('连板高度字段不完整')
    if failed is None:
        reasons.append('同日完整炸板未回封池缺失或范围不可比')
    elif not attempts:
        reasons.append('无触板样本，炸板率无定义')
    return {'date':day,'limit_up':len(current),'highest_consec':max(heights,default=0) if all(v is not None for v in heights) else None,
            'broken_rate':rate,'score':None,'reason':'；'.join(reasons)}


def normalized(values):
    low,high=min(values),max(values)
    return [(v-low)/(high-low) for v in values] if high>low else [.5]*len(values)


def cycle_position(target, dates, ups, broken, window=10):
    dates=sorted({day for day in dates if day <= target})[-window:]
    series=[day_facts(day,ups,broken) for day in dates]
    missing=[{'date':row['date'],'reason':row['reason']} for row in series if row['reason']]
    result={'available':False,'requested_window':window,'window':len(dates),'scope':SCOPE,
            'series':series,'missing':missing,'current_date':target,
            'note':'涨停家数、最高连板、反向炸板率各自在窗口内归一化后等权平均，分值为0—1的相对热度。与上方当日情绪档位评分不是同一口径；窗口低点仅作启发式起点，不代表精确周期或未来涨跌。'}
    if not dates or dates[-1] != target:
        return {**result,'reason':'所选日不是已归档交易日，不使用其他日期代替。'}
    if len(dates)<3 or missing:
        return {**result,'reason':f'完整观测 {len(dates)-len(missing)}/{len(dates)} 日；至少需要连续3个交易日，且窗口内三项字段均齐全。'}
    n_up=normalized([row['limit_up'] for row in series])
    n_high=normalized([row['highest_consec'] for row in series])
    n_broken=normalized([row['broken_rate'] for row in series])
    for i,row in enumerate(series):
        row['score']=round((n_up[i]+n_high[i]+1-n_broken[i])/3,3)
    scores=[row['score'] for row in series]
    trough_index=min(range(len(scores)),key=scores.__getitem__)
    delta1,delta2=scores[-1]-scores[-2],scores[-2]-scores[-3]
    trend=('连续两日走强' if delta1>.03 and delta2>.03 else '连续两日转弱' if delta1<-.03 and delta2<-.03
           else '今日走强' if delta1>.03 else '今日转弱' if delta1<-.03 else '基本走平')
    return {**result,'available':True,'trough_date':dates[trough_index], 'trough_score':scores[trough_index],
            'current_score':scores[-1],'day_n':len(dates)-trough_index,'trend':trend,
            'range_position':round((scores[-1]-min(scores))/(max(scores)-min(scores)),3) if max(scores)>min(scores) else None,
            'tie_count':scores.count(min(scores))}


def industry_structure(target, ups, broken, top=8):
    current,failed=rows_for(ups,target),rows_for(broken,target)
    base={'available':False,'date':target,'scope':SCOPE,
          'note':'所属行业来自当日涨停池industry字段，分类以该池来源为准；按最终封板股的首次封板时间统计节奏。行业不是事件题材，时间分布不代表板块内全体个股同步上涨。'}
    if current is None:
        return {**base,'reason':'所选日完整收盘涨停池缺失或范围不可比。','themes':[],'timeline':[]}
    upper={row['ticker'] for row in current}
    groups={}
    for row in current:
        sector=str(row.get('industry') or '').strip() or '行业缺失'
        group=groups.setdefault(sector,{'sector':sector,'limit_up':0,'first_boards':0,'consec_boards':0,
                                       'unknown_height_n':0,'heights':[],'times':[],'names':[],'broken':0 if failed is not None else None})
        group['limit_up']+=1
        h=integer(row.get('boards'))
        if h is None:
            group['unknown_height_n']+=1
        else:
            group['heights'].append(h)
            group['first_boards' if h==1 else 'consec_boards']+=1
        if t:=time_value(row.get('seal_time')):
            group['times'].append(t)
        group['names'].append({'name':row.get('name') or row['ticker'],'ticker':row['ticker'],'boards':h})
    for row in failed or []:
        if row['ticker'] in upper:
            continue
        sector=str(row.get('industry') or '').strip() or '行业缺失'
        if sector in groups:
            groups[sector]['broken']+=1
    themes=[]
    for group in groups.values():
        heights,times=group.pop('heights'),group.pop('times')
        group['highest']=max(heights,default=0) if not group['unknown_height_n'] else None
        group['first_seal']=min(times) if times else None
        group['last_seal']=max(times) if times else None
        group['time_valid_n']=len(times)
        group['names']=sorted(group['names'],key=lambda row:(-(row['boards'] or 0),row['ticker']))[:6]
        themes.append(group)
    themes.sort(key=lambda group:(-group['limit_up'],-(group['highest'] or 0),group['sector']))
    times=[t for row in current if (t:=time_value(row.get('seal_time')))]
    timeline=[]
    low='000000'
    for label,high in SLOTS:
        timeline.append({'slot':label,'count':sum(low<t<=high for t in times)})
        low=high
    unclassified_n=sum(row['limit_up'] for row in themes if row['sector']=='行业缺失')
    return {**base,'available':True,'themes':themes[:top],'industry_count':sum(row['sector']!='行业缺失' for row in themes),'total_limit_up':len(current),
            'concentration':themes[0]['limit_up']/len(current) if themes and current and themes[0]['sector']!='行业缺失' else None,
            'industry_valid_n':len(current)-unclassified_n,'unclassified_n':unclassified_n,
            'time_valid_n':len(times),'missing_time_n':len(current)-len(times),'broken_available':failed is not None,
            'timeline':timeline,'source':ups[target].get('source')}


def context_payload(data,target):
    settled=settled_target(target)
    dates=data.sessions(settled,10)[-10:]
    ups=comparable_pools(data.pools(dates,settled=True))
    broken=comparable_pools(data.pools(dates,'broken',settled=True))
    if target!=settled:
        reason='所选日尚未进入完整收盘统计，盘中数据请看下方实时打板情绪。'
        return {'date':target,'version':VERSION,'scope':SCOPE,'cycle':{'available':False,'reason':reason,'series':[]},
                'industry':{'available':False,'reason':reason,'themes':[],'timeline':[]}}
    return {'date':target,'version':VERSION,'scope':SCOPE,
            'cycle':cycle_position(target,dates,ups,broken),'industry':industry_structure(target,ups,broken)}
