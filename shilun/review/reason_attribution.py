"""Versioned editorial-reason attribution to existing, dated DC identities.

Business relationships are research mappings, not synonyms or proof of a
company catalyst. Membership is independently required by the consumer.
"""
from dataclasses import asdict, dataclass
import re
import unicodedata

VERSION = 'dc-reason-attribution-v1'
CREATED_AT = '2026-09-23'
VALID_FROM = '2026-08-21'
RELATIONS = {'exact': '名称一致', 'alias': '名称同义', 'business': '相关业务', 'uncertain': '含义待核验'}


def normalize(value):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', str(value or ''))).casefold()


def tokens(reason):
    return [v.strip() for v in re.split(r'[+＋、;；/|,，：:\n]', str(reason or '')) if v.strip()]


@dataclass(frozen=True)
class MappingRule:
    code: str
    name: str
    relation: str
    phrases: tuple[str, ...]
    explanation: str
    uncertain: tuple[str, ...] = ()
    valid_from: str = VALID_FROM
    valid_to: str | None = None


RULES = (
    MappingRule('BK0825.DC', '新零售', 'business',
        ('零售', '商业零售', '连锁零售', '区域零售', '百货零售', '免税零售', '零售消费', '大连零售', '硬折扣'),
        '当日新零售成分内，原文明确指向零售经营或零售业态；食品、服装或大消费本身不算零售扩散。',
        ('大消费', '消费', '本地生活', '山姆渠道')),
    MappingRule('BK1655.DC', '零售概念', 'business',
        ('商业零售', '连锁零售', '区域零售', '百货零售', '免税零售', '零售消费', '大连零售', '硬折扣', '新零售'),
        '零售经营与业态词归到当日已确认的零售概念；保留与新零售不同的DC身份。',
        ('大消费', '消费', '本地生活', '山姆渠道')),
    MappingRule('BK1086.DC', '粮食概念', 'business',
        ('粮食储备', '粮食贸易', '粮食种植', '粮食加工', '粮油加工', '粮油食品', '玉米种业', '玉米制种',
         '转基因玉米', '水稻种业', '水稻出海', '小麦种业', '玉米种植'),
        '当日粮食成分内，原文明确指向粮食或具体粮食作物；棉花、水产、化肥不自动归入。',
        ('种业', '生物育种', '育种研发', '转基因', '现代农业', '农作物种业', '制种基地', '央企种业')),
    MappingRule('BK0899.DC', 'CRO', 'business', ('临床前CRO', '创新药CRO', '医药研发服务', '药物研发服务'),
        '当日CRO成分内的药物研发服务或明确CRO业务；泛医药、生产、注册不等于研发外包。', ('医药', '医疗服务', '医药工业')),
    MappingRule('BK1600.DC', '医疗研发外包', 'business', ('CRO', '临床前CRO', '创新药CRO', '医药研发服务', '药物研发服务'),
        '研发外包三级行业与CRO概念分别保留，只有明确研发服务才建立业务归因。', ('医药', '医疗服务', '医药工业')),
    MappingRule('BK1106.DC', '创新药', 'business', ('中药创新药', '创新药研发', '创新药CRO'),
        '原文必须明确出现创新药业务；中药、化学制药、医药工业或药品注册不能直接推断创新药。',
        ('医药', '医疗服务', '医药工业', '化学制药', '中药', '药品注册')),
    MappingRule('BK1128.DC', 'CPO概念', 'alias', ('共封装光学', '共封装光学模块', '光电共封装'),
        'CPO全称写法；普通光模块或光通信并不自动等于CPO。', ('光模块', '光通信')),
    MappingRule('BK0877.DC', 'PCB', 'business', ('印制电路板', '印刷电路板', '高端PCB', 'HDI板', '高阶HDI'),
        '明确电路板及HDI板业务，要求同时属于当日PCB成分。'),
    MappingRule('BK0890.DC', 'MLCC', 'alias', ('多层陶瓷电容器', '多层片式陶瓷电容器', '片式多层陶瓷电容器'),
        'MLCC产品名称；泛陶瓷、电容不直接视为MLCC。', ('陶瓷', '电容')),
    MappingRule('BK1137.DC', '存储芯片', 'business', ('存储器芯片', '半导体存储', '存储芯片封测', 'DRAM', 'NAND闪存'),
        '明确存储芯片业务；存储设备或数据存储不能直接替代芯片归因。', ('数据存储', '存储设备')),
    MappingRule('BK1170.DC', 'AI制药（医疗）', 'business', ('AI制药', 'AI药物研发', '人工智能制药'),
        '原文明确AI用于制药或药物研发；AI应用加医药两个泛标签不足以合成。', ('AI医疗', 'AI应用')),
    MappingRule('BK1136.DC', '光通信模块', 'business', ('光模块', '光通信', '光模块测试'),
        '按当日光通信模块成分核对明确光通信业务；不据此升级为CPO。'),
    MappingRule('BK1138.DC', '液冷服务器', 'business', ('液冷', '液冷散热', '液冷硅油', '服务器液冷'),
        '当日液冷服务器成分内的明确液冷业务，泛散热不自动计入。', ('散热',)),
    MappingRule('BK1341.DC', '房产租赁经纪', 'business', ('房产经纪', '房地产经纪', '房产租赁'),
        '明确租赁经纪业务；保留和AI等其他明示标签各自的证据。', ('房地产',)),
    MappingRule('BK1504.DC', '粮油加工', 'business', ('粮食加工', '油脂加工', '粮油食品'),
        '粮油加工行业内明确加工业务；与粮食概念保留独立身份。'),
)


def rule_records():
    return [{**asdict(r), 'id': f'{VERSION}:{r.code}', 'relation_label': RELATIONS[r.relation],
             'created_at': CREATED_AT, 'historical_reconstruction': True} for r in RULES]


def match_reason(topic, reason, day, *, membership=True):
    """Return evidence, never a stock-specific or preferred-theme whitelist."""
    result = {'version': VERSION, 'status': 'unmapped', 'matches': [], 'uncertain_tokens': [],
              'explanation': '仅有成分关联，原文未命中已核验的归因词；不计入扩散。'}
    if not membership:
        return {**result, 'status': 'membership_missing', 'explanation': '缺少当日成分证据，不能确认归属。'}
    parts = tokens(reason)
    if not parts:
        return {**result, 'status': 'missing', 'explanation': '当日涨停原因缺失，等待补齐。'}
    name = topic['name']
    exact = {normalize(name)}
    if name.endswith('概念'):
        exact.add(normalize(name[:-2]))
    rule = next((r for r in RULES if r.code == topic['code']), None)
    if rule and normalize(rule.name) != normalize(name):
        return {**result, 'status': 'ambiguous', 'explanation': '分类代码对应名称已变化，映射需要重新核验。'}
    applicable = rule and day >= rule.valid_from and (not rule.valid_to or day <= rule.valid_to)
    aliases = {normalize(p) for p in rule.phrases} if applicable else set()
    uncertain = {normalize(p) for p in rule.uncertain} if applicable else set()
    for part in parts:
        token = normalize(part)
        if token in exact or token in aliases:
            relation = 'exact' if token in exact else rule.relation
            result['matches'].append({'token': part, 'relation': relation, 'relation_label': RELATIONS[relation],
                'rule_id': f'{VERSION}:{topic["code"]}' if relation != 'exact' else f'{VERSION}:literal',
                'explanation': '当日成分及题材名称与原文标签一致。' if relation == 'exact' else rule.explanation})
        elif token in uncertain:
            result['uncertain_tokens'].append(part)
        elif any(word in token for word in ('否认', '未涉及', '不涉及', '尚未', '传闻', '未证实', '澄清')) and any(p in token for p in exact | aliases):
            result['uncertain_tokens'].append(part)
            result['contradiction'] = True
    if result.get('contradiction'):
        result.update(status='ambiguous', explanation='原文包含否认、传闻或未证实表述，不能确认为涨停归因。')
    elif result['matches']:
        result.update(status='matched', explanation='；'.join(dict.fromkeys(m['relation_label'] + '：' + m['token'] for m in result['matches'])))
    elif result['uncertain_tokens']:
        result.update(status='ambiguous', explanation='原文标签过宽或含义不明确：' + '、'.join(result['uncertain_tokens']))
    return result
