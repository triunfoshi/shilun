import pytest

from shilun.review.reason_attribution import match_reason, rule_records
from shilun.review.theme_selection import ThemeSelectionReplay
from test_theme_selection import inputs, DAYS


def check(code, name, reason, **kwargs):
    return match_reason({'code': code, 'name': name}, reason, kwargs.pop('day', '2026-09-03'), **kwargs)


@pytest.mark.parametrize('code,name,reason,relation', [
    ('BK0825.DC','新零售','连锁零售+半年报增长','business'),
    ('BK0825.DC','新零售','区域零售+硬折扣','business'),
    ('BK1086.DC','粮食概念','粮食储备+玉米种业','business'),
    ('BK0899.DC','CRO','临床前CRO+订单增长','business'),
    ('BK1128.DC','CPO概念','共封装光学模块','alias'),
    ('BK0890.DC','MLCC','多层片式陶瓷电容器','alias'),
    ('BK0877.DC','PCB','高端PCB+高阶HDI','business'),
    ('BK1137.DC','存储芯片','半导体存储','business'),
    ('BK1170.DC','AI制药（医疗）','AI药物研发','business'),
])
def test_named_mapping_has_evidence_and_relation(code,name,reason,relation):
    result=check(code,name,reason)
    assert result['status']=='matched'
    assert result['matches'][0]['relation']==relation
    assert result['matches'][0]['rule_id']
    assert result['matches'][0]['explanation']


@pytest.mark.parametrize('code,name,reason,expected', [
    ('BK0825.DC','新零售','粮油加工+粮食概念+大健康食品','unmapped'),
    ('BK0825.DC','新零售','数字人民币+跨境支付+AI应用','unmapped'),
    ('BK0825.DC','新零售','大消费+速冻食品','ambiguous'),
    ('BK1086.DC','粮食概念','棉花种业+水产种业','unmapped'),
    ('BK1086.DC','粮食概念','种业+转基因','ambiguous'),
    ('BK1106.DC','创新药','医药','ambiguous'),
    ('BK0899.DC','CRO','医药工业','ambiguous'),
    ('BK1128.DC','CPO概念','光模块','ambiguous'),
    ('BK1170.DC','AI制药（医疗）','AI应用+医药','ambiguous'),
    ('BK1106.DC','创新药','创新药+公司否认创新药','ambiguous'),
    ('BK1106.DC','创新药','创新药业务传闻尚未证实','ambiguous'),
    ('BK0825.DC','新零售','','missing'),
])
def test_no_membership_only_or_ambiguous_promotion(code,name,reason,expected):
    assert check(code,name,reason)['status']==expected


def test_dated_identity_guard_and_membership_required():
    assert check('BK0825.DC','已改名','连锁零售')['status']=='ambiguous'
    assert check('BK9999.DC','新零售','连锁零售')['status']=='unmapped'
    assert check('BK0825.DC','新零售','连锁零售',membership=False)['status']=='membership_missing'
    assert check('BK0825.DC','新零售','连锁零售',day='2026-08-20')['status']=='unmapped'
    assert check('BK0899.DC','CRO','ＣＲＯ')['status']=='matched'


def test_multiple_aliases_on_one_stock_are_not_multiple_followers():
    market,taxonomy=inputs()
    for day,snapshot in taxonomy['snapshots'].items():
        snapshot['rows'][0]['code']='BK0825.DC'
        for ticker in market['reasons'][day]['reasons']:
            market['reasons'][day]['reasons'][ticker]='区域零售+硬折扣+连锁零售'
    result=ThemeSelectionReplay(market,taxonomy).evaluate(DAYS[-1])
    assert len(result['candidates'][0]['followers'])==2
    assert len(result['candidates'][0]['events'])==3
    assert len(result['directions'])==1
    old=ThemeSelectionReplay(market,taxonomy,attribution_mode='literal').evaluate(DAYS[-1])
    assert not old['directions']


def test_rule_registry_exposes_business_boundaries_without_tickers():
    rows=rule_records()
    assert len({r['code'] for r in rows})==len(rows)
    assert all(r['valid_from'] and r['created_at'] and r['explanation'] for r in rows)
    assert all('ticker' not in r and 'stock' not in r for r in rows)
