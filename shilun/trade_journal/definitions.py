"""Versioned vocabulary for the journal; definitions never issue trade orders.

The registry describes existing evaluators and explicitly marks human concepts.
It imports rule owners rather than maintaining a second set of trading gates.
Consumers freeze the returned definition with each historical journal record.
No database, network, statement import, or price calculation occurs here.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from datetime import date, datetime, time
from typing import Any, Iterable, Mapping

from shilun.review.contracts import (
    CYCLE_LABELS, EXPECTATION_LABELS, POSTURE_LABELS, ROLE_LABELS,
)
from shilun.review.market_mood import MOOD_RULE_VERSION
from shilun.review.strong_stock_candidates import VERSION as CORE_VERSION, RULE as CORE_RULE
from shilun.review.theme_rules import rule_config
from shilun.review.theme_cycles import LABELS as THEME_CYCLE_LABELS, VERSION as THEME_CYCLE_VERSION
from shilun.review.theme_selection import VERSION as THEME_SELECTION_VERSION
from shilun.market.ma5_liquidity import MA5_SELECTION_RULE_VERSION
from shilun.decision_research.playbook_assessment import POLICY as PLAYBOOK_POLICY
from shilun.strategies.registry import get_strategy, list_strategies
from shilun.trading.config import DEFAULT_EXECUTION_SCENARIO_RULES
from shilun.trading.contracts import ACTION_DEFINITIONS, ExecutionState, ScenarioState
from shilun.trading.playbooks.e8_first_board import DEFAULT_E8_RULES
from shilun.trading.playbooks.execution_scenarios import SCENARIO_TITLES

DEFINITIONS_VERSION = "trade-journal-definitions-v2"
VERSION = DEFINITIONS_VERSION
STANDARD = "docs/交易体系/交割单复盘数据标准_v1.0.md"
GUIDE = "docs/交易体系/模式情绪周期与交易复盘口径_v2.0.md"
P2_VERSION = "daily_review_v1.9_eastmoney_taxonomy"

_CATEGORY_LABELS = {
    "mode": "交易模式", "trigger": "入场触发与形态", "action": "决策与管理动作",
    "environment": "市场环境", "emotion": "收盘情绪等级", "cycle": "周期阶段",
    "role": "证券角色", "evidence": "证据性质", "qualification": "数据资格",
    "metric": "指标与收益口径", "strategy": "已注册执行策略", "scope": "统计范围",
    "observation_state": "观察状态",
    "result_type": "研究报告结果类型",
    "expectation": "预期比较结果", "expectation_check": "预期复盘核验", "state": "执行状态", "technical_feature": "客观技术特征",
}


def _json_value(value: Any) -> Any:
    if isinstance(value, (date, datetime, time)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(v) for v in value]
    return value


def _source(path: str, symbol: str, version: str = DEFINITIONS_VERSION) -> dict[str, str]:
    return {"path": path, "symbol": symbol, "version": version}


def _definition(identifier: str, label: str, definition: str, *,
                source: dict[str, str] | list[dict[str, str]],
                status: str = "manual_definition", rule_version: str = DEFINITIONS_VERSION,
                axis: str | None = None, formula: str | None = None,
                numerator: str | None = None, denominator: str | None = None,
                unit: str = "分类", window: str = "由本次记录或所引用规则明确，不默认自然日",
                time_basis: str = "决策当时可用事实；事后补记另存记录时间与所述决策时间",
                universe: str = "本账户原始证券记录；研究样本范围另行声明",
                st_policy: str = "不删除真实成交；研究筛选按来源规则单列",
                missing_policy: str = "缺项保留未知，不填零，不自动推断用户意图",
                limitations: list[str] | None = None, aliases: list[str] | None = None,
                parameters: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "id": identifier, "label": label, "category": identifier.split(".", 1)[0],
        "definition": definition, "status": status, "rule_version": rule_version,
        "axis": axis or identifier.split(".", 1)[0],
        "scope": {"universe": universe, "st_policy": st_policy},
        "formula": formula, "numerator": numerator, "denominator": denominator,
        "unit": unit, "window": window, "time_basis": time_basis,
        "missing_policy": missing_policy,
        "sources": source if isinstance(source, list) else [source],
        "limitations": limitations or ["术语、规则存在或单笔盈利不证明策略收益有效，也不授予下单权限。"],
        "aliases": aliases or [], "parameters": _json_value(parameters or {}),
    }


def _records() -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    add = records.append
    manual_source = _source(GUIDE, "模式、触发、管理动作分层")
    for code, label, text in (
        ("unknown", "模式未知", "没有足够原计划或当事人补记；与明确选择人工计划不同。"),
        ("manual", "用户自定义模式", "用户明确选择自定义判断，并保留原文、适用条件与版本；不自动产生机器规则。"),
        ("mixed", "混合或未分配模式", "同一证券多种意图的股数未明确分配；库存与收益只记一次。"),
        ("arbitrage", "预期差套利", "用户博弈短期价值与预期错位的交易意图；这里不表示无风险套利，须另记具体模式条件。"),
        ("ma5_trend", "MA5趋势", "以趋势及MA5结构为用户入场意图；必须另记确认、期限、退出与策略绑定。靠近均线只是特征。"),
        ("first_board_arbitrage", "首板套利", "围绕启动首板与次日溢价的交易意图；启动样本与次日新买交易分开。"),
        ("relay", "连板接力", "参与已有涨停梯队的延续，需核验换手、失败票反馈、时点和可成交性。"),
        ("core_crowding", "核心抱团", "资金聚焦辨识度核心的交易意图；核心资格代理不等于已确认抱团或资金身份。"),
        ("rotation_arbitrage", "轮动套利", "博弈有限窗口的题材回流或轮动，预期时限必须单独记录，不自动延长为趋势持有。"),
        ("reversal_pry", "反核翘板", "用户意图为恐慌或跌停附近的承接修复；翘板触发、未修复处理需人工明确。"),
        ("leader_first_negative", "龙头首阴", "用户以其认定核心的首次明显分歧或阴线博修复；首阴、核心和承接不能靠名称自动确认。"),
        ("technology_rebound", "科技回流", "用户博弈科技方向回流的意图；科技并非永久风格，他人交易参考不代替本股条件。"),
        ("oversold_overnight", "超跌隔夜套利", "用户意图为企稳后的隔夜反弹；仅显式绑定E7策略并满足其规则时才应用对应时间退出。"),
    ):
        add(_definition("mode." + code, label, text, source=manual_source,
                        status="unknown" if code == "unknown" else "manual_definition"))

    # Preserve the actual strategy IDs and versions, including future registered adapters.
    for item in list_strategies():
        strategy = get_strategy(item["strategy_id"])
        path = type(strategy).__module__.replace(".", "/") + ".py"
        add(_definition("strategy." + item["strategy_id"], item["name"], item["description"],
                        source=[_source("shilun/strategies/registry.py", "list_strategies", item["version"]),
                                _source(path, type(strategy).__name__, item["version"])],
                        status="implemented_rule", rule_version=item["version"],
                        parameters={"strategy_id": item["strategy_id"], "strategy_version": item["version"]},
                        limitations=["策略注册不表示用户当时采用，也不表示当前启用、数据齐备或收益已验证。",
                                     "历史成交缺少意图时，不回填默认MA5或人工计划；E7不能被MA5覆盖。 "]))

    for code, label, text in (
        ("pullback", "回踩确认", "回踩预先指定位置后承接成立；需记录位置、窗口和实际确认事实。"),
        ("breakout", "突破确认", "价格越过预先指定平台、前高或均线，是否确认由绑定规则决定。"),
        ("weak_to_strong", "弱转强", "先写原正常预期和原弱势，再比较主动修复；涨得快本身不是超预期。"),
        ("pry", "翘板承接", "跌停或恐慌状态出现可核验承接；未定义的主观信号保留原文，不自造阈值。"),
        ("first_negative", "首阴修复", "用户观察的首次分歧修复触发，与主模式和实际下单分别保存。"),
        ("external_reference", "外部交易参考", "他人下单或席位作为用户所述参考；记录来源与可得时间，不推断身份或锁仓。"),
        ("unknown", "触发未知", "尚未记录或无法还原实际入场触发。"),
    ):
        add(_definition("trigger." + code, label, text, source=manual_source))

    config = DEFAULT_EXECUTION_SCENARIO_RULES
    for code, label in SCENARIO_TITLES.items():
        category = "trigger" if code.startswith("E") else "action"
        add(_definition(f"{category}.{code}", f"{code} · {label}",
                        "既有执行场景；命中、取消、确认和数量由FullScenarioPlaybook及绑定配置处理，不是新的主模式。",
                        source=_source("shilun/trading/playbooks/execution_scenarios.py", "FullScenarioPlaybook/SCENARIO_TITLES", config.version),
                        status="implemented_rule", rule_version=config.version,
                        time_basis="带时区实时报价与已闭合5分钟K；使用来源时间和新鲜度规则",
                        parameters=asdict(config)))
    add(_definition("trigger.E8", "E8 · 首板候选早盘确认", "已有首板盘中确认场景，行业广度与相对强弱路径、量价结构及早盘观察窗共同判定。",
                    source=_source("shilun/trading/playbooks/e8_first_board.py", "evaluate_e8_buy_point/DEFAULT_E8_RULES", DEFAULT_E8_RULES.version),
                    status="implemented_rule", rule_version=DEFAULT_E8_RULES.version,
                    parameters=asdict(DEFAULT_E8_RULES), time_basis="当日已闭合5分钟K；竞价不能替代盘中确认"))
    for code, label in {"tu_po": "突破点", "qi_zhang": "起涨点", "hui_cai": "回踩点",
                        "chao_di": "抄底点", "zhui_zhang": "追涨点", "none": "未识别形态"}.items():
        add(_definition("trigger.pattern_" + code, label, "五买点形态层标签；按既有优先级识别，与MA5指标层信号并存，不证明用户真实动机。",
                        source=_source("shilun/market/buy_point_patterns.py", "BUY_POINT_LABELS/detect_buy_point_pattern", "ma5_v0_2:0.2.0"),
                        status="implemented_rule", rule_version="ma5_v0_2:0.2.0"))
    for code, label, symbol, text in (
        ("red_k_net_inflow", "红K净流入", "detect_red_k_net_inflow", "P2三战法之一：指数实心红K前提与个股资金流事实；来源口径和板块承接仍须核验。"),
        ("structure_repair_520", "结构修复520", "detect_520", "下降或走平结构稳住后，MA5拐点临界上穿MA20或当日上穿；不是泛化为所有趋势回踩。"),
        ("weekly_breakout", "周K突破套利", "detect_weekly_breakout", "以日K收盘突破之前100交易日最高价作约20周压力突破代理；不是读取未闭合周K。"),
    ):
        add(_definition("trigger." + code, label, text,
                        source=_source("shilun/review/engine.py", symbol, P2_VERSION),
                        status="implemented_rule", rule_version=P2_VERSION,
                        time_basis="收盘后日线事实；不得倒填为上午已确认触发"))

    for code, label, text in (
        ("buy", "首买", "真实库存从可核实空仓开始建仓；当日清仓后重新买入是新轮。"),
        ("add", "加仓", "已有证券库存时继续买入；单独记录原逻辑、依据、计划与风险变化。"),
        ("reduce", "减仓", "卖出部分可卖库存，剩余仓位仍属于当前交易轮。"),
        ("sell", "清仓", "卖出使证券真实库存归零；成交可得性和结算限制单独核验。"),
        ("watch", "观察", "观察候选但没有成交；不得记为真实交易零收益。"),
        ("abandon", "放弃", "明确放弃、未成交或取消的决策；不纳入真实成交胜率。"),
        ("intraday_t", "做T", "以已有可卖旧仓完成减买或买减管理；新买股不因做T名称而获得当日回转权限。"),
        ("stop_loss", "止损", "按本轮有效计划处理失效或风险；不得为未知计划补默认5%或30%阈值。"),
        ("time_exit", "时间退出", "在原计划或绑定策略规定的验证期限退出；不能亏损后无记录延长期限。"),
    ):
        add(_definition("action." + code, label, text, source=_source(STANDARD, "记录对象/成交与盈亏标准", "1.0")))
    for action, info in ACTION_DEFINITIONS.items():
        add(_definition("action." + action.value, info.label, "既有执行动作展示契约；建议、委托和真实成交是不同事件。",
                        source=_source("shilun/trading/contracts.py", "ActionType/ACTION_DEFINITIONS", "trading-p0-v0.2.0"),
                        status="implemented_contract", rule_version="trading-p0-v0.2.0",
                        parameters={"action_type": action.value, "executable_in_owner": info.executable, "urgency": info.urgency},
                        limitations=["executable是既有引擎动作属性；本词典或日志记录不触发下单，也不证明已经成交。 "]))

    for code, label, interval in (("cold", "冰点", "score < 30"), ("normal", "普通", "30 <= score < 60"),
                                  ("active", "活跃", "60 <= score < 80"), ("hot", "亢奋", "score >= 80"),
                                  ("pending", "情绪待确认", "必要当日池、前日池、收益覆盖或跌停价格资料不足")):
        add(_definition("emotion." + code, label, "P2收盘情绪强弱等级；与试错/主升/震荡/退潮阶段是两个轴，不能一一映射。",
                        source=_source("shilun/review/market_mood.py", "_market_mood_state", MOOD_RULE_VERSION),
                        status="implemented_rule", rule_version=MOOD_RULE_VERSION, formula=interval,
                        parameters={"range": interval, "owner_code": code},
                        time_basis="所选交易日收盘；历史回补标historical_reconstruction",
                        missing_policy="必要数据不足为pending，不把未知当冰点；保留缺项和覆盖率"))

    for code, label, meaning in (
        ("trial", "试错期", "资金尝试新方向，延续与亏钱效应尚待验证。"),
        ("main_uptrend", "主升期", "某一明确范围的赚钱效应持续扩展；不能仅由指数上涨或情绪分数认定。"),
        ("oscillation", "震荡期", "同一范围内强弱反复，须区别健康换手、轮动与衰退。"),
        ("retreat", "退潮期", "同一范围的核心与失败样本负反馈扩散；不预设固定天数。"),
        ("unknown", "阶段未知", "没有足够时间顺序与同范围反馈，不强贴阶段。"),
    ):
        add(_definition("cycle." + code, label, meaning, source=_source(GUIDE, "情绪等级与周期分轴"),
                        axis="manual_market_phase", limitations=["这是人工阶段语义，尚无统一自动阈值；不是P2情绪四档或题材状态机的别名。 "]))
    for code, label in THEME_CYCLE_LABELS.items():
        add(_definition("cycle.theme_" + code, label + "（题材固定队列）", "已准入题材按原核心与固定后排、跨日收益及跟随接力顺序转移的状态。",
                        source=[_source("shilun/review/theme_cycles.py", "transition/ThemeCycleReplay", THEME_CYCLE_VERSION),
                                _source("shilun/review/theme_rules.py", "CycleRules/rule_config", rule_config()["version"])],
                        status="implemented_rule", rule_version=THEME_CYCLE_VERSION, axis="theme_fixed_cohort",
                        parameters=rule_config(), time_basis="逐交易日收盘，只使用当日及以前事实；冻结原队列",
                        aliases=["修复"] if code == "recovery" else [],
                        limitations=["阶段不是入场指令；15%收盘回撤是既有预警，不单独强制退出。",
                                     "缺资料/来源冲突不生成阶段；归档退出不删除失败原样本。 "]))
    for code, label in CYCLE_LABELS.items():
        add(_definition("cycle.legacy_" + code.value, label, "旧P2板块当日硬指标阶段；不是固定队列周期，也不证明题材已形成主线。",
                        source=_source("shilun/review/engine.py", "classify_sector_cycle", "sector-cycle-diagnostics-v1"),
                        status="implemented_rule", rule_version="sector-cycle-diagnostics-v1", axis="legacy_daily_sector"))
    for code, label in {"start": "启动", "confirm": "确认", "main_uptrend": "主升", "accelerate": "加速", "divergence": "分歧",
                        "repair": "修复", "decline": "退潮", "second_wave": "二波", "watch": "观察"}.items():
        add(_definition("cycle.sector_" + code, label + "（板块研究）", "旧板块趋势模型的阶段展示语义；能否实际被分类器命中须以当日规则轨迹为准。",
                        source=_source("shilun/market/sector.py", "_stage/_classify_sector_stage", "market_sector_v15_eastmoney"),
                        status="implemented_rule", rule_version="market_sector_v15_eastmoney", axis="sector_trend_model"))

    for code, label in POSTURE_LABELS.items():
        add(_definition("environment." + code.value, label, "旧复盘市场姿态：四宽基方向、MA5与广度形成的路径标签；不等于具体玩法已有正反馈。",
                        source=_source("shilun/review/engine.py", "classify_market_posture", P2_VERSION),
                        status="implemented_rule", rule_version=P2_VERSION, axis="market_posture",
                        limitations=["弱势抱团是既有分类用语，不是弱市必有安全抱团；须另核玩法反馈与可成交性。 "]))
    add(_definition("environment.ma5_liquidity", "MA5容量档", "既有MA5规则按全日市场成交额限制证券7交易日平均成交额，不是按市值大小直接下单。",
                    source=_source("shilun/market/ma5_liquidity.py", "liquidity_rule/market_turnover", MA5_SELECTION_RULE_VERSION),
                    status="implemented_rule", rule_version=MA5_SELECTION_RULE_VERSION, axis="capacity_policy",
                    formula="全日市场额<2.1万亿→7日均额<50亿；2.1万亿至2.3万亿（含端点）→7日均额<100亿；>2.3万亿不设该均额上限",
                    unit="元CNY", window="市场当日；证券连续7个完整交易日",
                    universe="沪深北A股逐股当日成交额去重求和，市场总额含ST，排除ETF与重叠指数重复额",
                    time_basis="全日收盘资料；盘中全天估计额不能冒充全日已实现额",
                    limitations=["这是当前候选容量研究门槛，未证明阈值最优；与旧P2的300亿市值权限不同。 "]))
    for code, label, text in (
        ("growth_value", "成长/价值风格", "V2以创业板涨跌幅减沪深300涨跌幅作风格代理；不能代替连板/趋势/抱团生态。"),
        ("trend", "趋势型", "板块趋势结构与容量的观察标签，不等于科技等行业的永久属性。"),
        ("emotion", "情绪型", "涨停梯队及活跃前排较突出的一种板块观察标签。"),
        ("mixed", "混合型", "趋势与情绪玩法可同时存在；不得强制互斥选一。"),
        ("unknown", "环境未知", "环境资料不足或各轴分化；不能按零分当弱市。"),
    ):
        add(_definition("environment." + code, label, text,
                        source=_source("shilun/market/market_v2.py" if code == "growth_value" else "shilun/review/service.py",
                                       "_style_rotation" if code == "growth_value" else "DailyReviewService._sectors", P2_VERSION),
                        status="implemented_proxy", rule_version=P2_VERSION, axis="style"))

    for code, label in ROLE_LABELS.items():
        add(_definition("role." + code.value, label, "旧P2互斥角色标签；由高度、容量或趋势候选事实推得，不证明因果带动或永久市场地位。",
                        source=_source("shilun/review/engine.py", "assign_exclusive_role", P2_VERSION),
                        status="implemented_proxy", rule_version=P2_VERSION))
    add(_definition("role.core_candidate", "已认证强股/核心候选", CORE_RULE,
                    source=_source("shilun/review/strong_stock_candidates.py", "evaluate_stock/RULE", CORE_VERSION),
                    status="implemented_rule", rule_version=CORE_VERSION, parameters=rule_config()["core"],
                    window="新连板段首板起10个交易日，停牌/未知日不压缩日历",
                    universe="来源已核验A股目录及日期行情；资格按个股独立认证",
                    st_policy="题材研究入口stock_allowed排除ST/退市，原始账户记录始终保留",
                    limitations=["认证的是明确价格资格，不等于真龙头、可买到或保证修复；不能取当日幸存者倒选历史。 "]))
    for code, label, text in (
        ("leader_candidate", "龙头候选", "需指定范围、参照对象、承接与带动证据；最高、最早上板、名人持有均非充分条件。"),
        ("follower", "跟随观察", "同方向出现跟随事件或保留成员观察；共享行业标签不自动证明同一催化。"),
        ("catch_up", "补涨观察", "用户判断的空间补偿角色；后排不自动等于补涨龙，需独立时序与依据。"),
        ("unknown", "角色未知", "缺少范围、锚点或关系证据，保留未知。"),
    ):
        add(_definition("role." + code, label, text, source=_source(GUIDE, "核心、候选与角色变化")))

    for code, label, text in (
        ("contemporaneous", "当时归档", "决策当时已经实际保存且可证明当时可用的记录；仅有行情日期不够。"),
        ("historical_reconstruction", "历史重建", "后来按当时及以前行情重算的客观事实；不证明当时已采集或用户曾看到。"),
        ("retrospective_recall", "事后回忆", "用户之后补记当时意图或观察；保留原文、实际补记时间和所述决策时间。"),
        ("unknown", "证据时点未知", "无法证明可得或保存时间；不自动提升为当时归档。"),
    ):
        add(_definition("evidence." + code, label, text, source=_source(STANDARD, "时间与证据标准", "1.0"), status="data_contract"))
    for code, label, text in (
        ("accounting", "成交可核算", "库存、期初成本、费用、公司行为和成交纠错可核对；否则只查看原始记录，不补收益。"),
        ("decision_review", "决策可复盘", "有用户意图或明确标注的补记，并能与实际操作和证据关联；不等于事前可核验。"),
        ("ex_ante", "事前可核验", "当时冻结的计划、候选、条件和所用可得事实均有时点证明；事后回忆不足以通过。"),
        ("rule_backtest", "规则可回测", "规则可机器判定、有版本、研究范围与历史输入完整，费用、可卖库存及成交限制能模拟。"),
    ):
        add(_definition("qualification." + code, label, text, source=_source(STANDARD, "观察池与回测资格", "1.0"), status="data_contract",
                        limitations=["四个维度分别给eligible和reasons，不用完整度总分互相替代，也不证明策略有效。 "]))

    metrics = (
        ("market_amount", "市场全日成交额", "逐股同日成交额求和，行情amount由千元转元。", "合格当日A股成交额之和", None, "元CNY", "当日收盘", "shilun/market/ma5_liquidity.py", "market_turnover"),
        ("market_expansion_1000_eod", "日终增加1000亿标记", "当日全日额−前一交易日全日额>=1000亿元；不是独立开仓条件。", "当日全日成交额−前日全日成交额", None, "亿元/布尔", "相邻交易日收盘", "shilun/review/engine.py", "classify_market_posture"),
        ("intraday_amount_ratio", "盘中同时间槽量比", "本时点累计额/历史同时间槽累计额；全日估计额另存方法与估计时点。", "当前同时间槽累计额", "已指定基线的同时间槽累计额", "倍", "带时区采样时点", "docs/交易体系/交割单复盘数据标准_v1.0.md", "盘中量能记录"),
        ("opening_premium", "次日开盘溢价", "(次日开盘价/样本形成日收盘价−1)×100。", "次日开盘价−前收盘价", "前收盘价", "百分点", "固定名单形成日→下一交易日开盘", "shilun/decision_research/playbook_assessment.py", "observe"),
        ("close_return", "次日收盘反馈", "(次日收盘价/样本形成日收盘价−1)×100。", "次日收盘价−前收盘价", "前收盘价", "百分点", "固定名单形成日→下一交易日收盘", "shilun/decision_research/playbook_assessment.py", "observe"),
        ("red_k", "红K", "收盘价>同日开盘价；与相对昨收上涨、开盘溢价为不同指标。", "收盘价−同日开盘价", None, "布尔", "所选日收盘", "shilun/decision_research/playbook_assessment.py", "observe"),
        ("promotion_rate", "晋级率", "固定前日同板数/同市场类别名单中，次日收盘封板的有效比例；一字与换手另列。", "次日已核验收盘封板数", "同一冻结队列中有效可观察数", "比例0—1；UI可格式化%", "相邻交易日", "shilun/review/board_dynamics.py", "build_dynamics"),
        ("broken_rate", "炸板率", "当日触板未封数/(当日收盘封板数+触板未封数)；池范围必须可比且去重。", "当日触板但收盘未封的去重证券数", "同范围封板数+触板未封数", "比例0—1", "所选日收盘", "shilun/review/board_dynamics.py", "build_dynamics/comparable_pools"),
        ("mood_score", "P2情绪分", "六项正向成分和减独立跌停风险扣分，截到0—100；是现有规则分，不是概率。", "规则加权成分减风险扣分", None, "分0—100", "所选日收盘", "shilun/review/market_mood.py", "_market_mood_state"),
        ("theme_diffusion", "题材已确认扩散", "已认证核心之外、同日成分及涨停原因支持的独立跟随，按股票去重、活动日期另计。", "窗口归因明确的非核心独立涨停证券数", None, "只/活动交易日", "近3交易日，准入另要求至少2只、跨2日", "shilun/review/theme_selection.py", "ThemeSelectionReplay.evaluate/events"),
        ("positive_feedback_breadth", "玩法正反馈广度", "首板用正开盘溢价占比；接力/核心用次日收盘上涨占比；不是板块成员普涨广度。", "同玩法固定样本的正反馈数", "该指标有效样本数", "百分数0—100", "固定前日名单→当日反馈", "shilun/decision_research/playbook_assessment.py", "daily_evidence/rate"),
        ("loss_tail", "明显亏损尾部", "现有玩法研究用≤−5%作描述切片，首板看开盘，接力看昨断板/炸板，核心看昨日核心收盘。", "符合该玩法亏损尾部切片的有效样本数", "对应切片的有效固定队列数", "百分数0—100", "一个反馈交易日", "shilun/decision_research/playbook_assessment.py", "POLICY/daily_evidence"),
        ("realized_pnl", "实际已实现盈亏", "FIFO匹配卖出净收入与对应含费买入成本，买卖费按股数分配；完整轮与净现金流对账。", "卖出净收入−匹配含费买入成本", None, "元CNY", "实际卖出事件；完整轮另按清仓日归属", STANDARD, "收益口径"),
        ("unrealized_pnl", "未实现盈亏", "剩余库存按带时点与来源的价格估值，减剩余含费成本，与已实现独立。", "剩余库存估值−剩余含费成本", None, "元CNY", "标明估值时点", STANDARD, "收益口径"),
        ("round_return", "整轮成本回报率", "完整轮实际含费盈亏/累计含费买入成本；必须同时显示分母类型，不冒充最大资金占用回报。", "完整轮实际含费盈亏", "累计含费买入成本", "比例；展示百分比", "已核实空仓建仓至清仓", STANDARD, "收益口径"),
        ("win_rate", "真实完整轮胜率", "净盈亏>0的完整、已清仓且成本可核算轮数/同口径全部完整轮；未平仓、未知成本及模拟观察单列。", "完整已清仓可核算轮中净盈亏>0的轮数", "同范围完整已清仓且成本可核算轮数", "比例0—1", "明确首买日或清仓日统计区间，不混用", STANDARD, "观察池与回测资格/收益口径"),
        ("mfe_mae", "最大有利/不利波动", "持有期间行情路径诊断，区分首仓固定与真实动态库存；最高价不是可实现卖价。", "指定库存模型下持有期最有利/不利估值变化", "预先声明的成本/占用基准", "元/比例，必须声明", "持有区间及数据分辨率", STANDARD, "如何检验卖点问题"),
    )
    metric_versions = {
        "shilun/market/ma5_liquidity.py": MA5_SELECTION_RULE_VERSION,
        "shilun/review/engine.py": P2_VERSION,
        "shilun/review/market_mood.py": MOOD_RULE_VERSION,
        "shilun/review/theme_selection.py": THEME_SELECTION_VERSION,
        "shilun/decision_research/playbook_assessment.py": "mode-research-v5",
        "shilun/review/board_dynamics.py": DEFINITIONS_VERSION,
        STANDARD: "1.0",
    }
    for code, label, formula, numerator, denominator, unit, time_basis, path, symbol in metrics:
        owner_version = metric_versions.get(path, DEFINITIONS_VERSION)
        account_metric = code in {"realized_pnl", "unrealized_pnl", "round_return", "win_rate", "mfe_mae"}
        add(_definition("metric." + code, label, formula, source=_source(path, symbol, owner_version),
                        status="implemented_metric" if path.endswith(".py") else "data_contract",
                        rule_version=owner_version,
                        formula=formula, numerator=numerator, denominator=denominator, unit=unit,
                        time_basis=time_basis, window=time_basis,
                        universe="明确账户与完整交易轮范围，原始证券不因研究排除而消失" if account_metric else
                                 "来源模块指定的逐股市场或冻结研究队列；必须显示来源范围与覆盖，禁止拼接不相同分母",
                        st_policy="真实账户含ST/ETF/IPO，主动交易与其他事件另组" if account_metric else
                                  "按该来源池范围保留或排除；与真实账户不共用默认ST筛选",
                        limitations=["必须保留样本数、原始分母、有效覆盖、来源范围、单位和规则版本；零样本不等于0收益或0风险。",
                                     "未来开高低收或完整收盘名单不得进入更早决策依据；观察统计不是实际可成交收益。 "]))
    add(_definition("metric.playbook_comparison", "玩法近况比较", "三玩法分别比较近期3/5个完整交易日与紧邻、非重叠此前10日；改善和当前支持度分开。",
                    source=_source("shilun/decision_research/playbook_assessment.py", "POLICY/period", "mode-research-v5"),
                    status="implemented_research_rule", rule_version="mode-research-v5",
                    parameters=PLAYBOOK_POLICY,
                    numerator="每日玩法主反馈中位数/每日有效比例，再按既有period规则汇总", denominator="完整预定窗口与每日冻结样本",
                    unit="百分点/百分数，随指标明确", window="3/5交易日对紧邻此前10交易日",
                    limitations=["门槛是现有研究假设，不是新开仓许可证；不跨玩法按累计收益排名，不跳过缺失日寻找替代。 "]))

    for code, label, text, path, symbol in (
        ("actual_account", "真实账户范围", "所有真实证券、ST、ETF、新股、分红及资金划转保留原始事实；主动二级交易、IPO和其他事件分组。", STANDARD, "原始成交/收益口径"),
        ("theme_research", "题材研究范围", "研究排除ST、退市/停牌上市状态及因子/指数型题材；不修改原始证券与账户成交。", "shilun/market/research_scope.py", "stock_allowed/topic_allowed"),
        ("board_rates", "打板率可比范围", "不同来源先统一沪深非ST分母；北交所、ST或未知来源另列，不拼接不相同池范围。", "shilun/review/board_dynamics.py", "comparable_pools/_scope"),
        ("manual_watchlist", "人工观察名单", "用户实际保存的观察子集；加入、未买和移除只代表该份名单，不冒充系统候选全集。", "shilun/trade_journal/service.py", "TradeJournalService.append_observation"),
        ("strategy_cohort", "已核验研究候选全集", "按精确评估快照、玩法、窗口与形成/反馈日期核验完整成员及事实；只相对于该冻结研究群体完整，不代表全市场或当时可用。", "shilun/trade_journal/research_bridge.py", "freeze_strategy_cohort"),
    ):
        add(_definition("scope." + code, label, text, source=_source(path, symbol), status="data_contract",
                        universe=text, st_policy=text))
    for code, label, text in (
        ("watching", "观察中", "已加入保存范围，尚未记录最终选择或成交；不是入场许可。"),
        ("not_bought", "未买", "记录该观察证券没有买入，原因可未知；不当作真实交易零收益。"),
        ("not_triggered", "未触发", "用户或已有规则说明预定条件未触发；须保留原条件及证据，不能由未买反推。"),
        ("unfilled", "未成交", "已尝试参与但无成交；须另核委托/回报，不把观察收益列入已实现盈亏。"),
        ("abandoned", "已放弃", "主动放弃该观察机会并保留理由，不从下跌结果推断当时已放弃。"),
        ("removed", "已移除", "从个人观察范围移除的追加事件；保留原记录和父观察，不删除失败候选。"),
    ):
        add(_definition("observation_state." + code, label, text,
                        source=_source("shilun/trade_journal/service.py", "TradeJournalService.append_observation"),
                        status="data_contract", axis="observation_lifecycle"))
    for code, label in EXPECTATION_LABELS.items():
        add(_definition("expectation." + code.value, label, "与同标签历史Q25/中位数/Q75比较；一致和逻辑反转须独立依据，不能由涨速或低开单独判定。",
                        source=_source("shilun/review/engine.py", "build_expectation_band/classify_expectation", P2_VERSION),
                        status="implemented_rule", rule_version=P2_VERSION,
                        parameters={"minimum_samples": 5, "quantiles": [.25, .5, .75]},
                        numerator="本次实际观察指标", denominator="同单位同标签历史分布（非除法）",
                        time_basis="基线只能使用决策时已经可得的历史结果"))
    for code, label, text in (
        ("supported", "预期获支持", "人工评估或已归档数值比较支持所记录预期；必须关联原预期、对应窗口和证据，并保留评估类别。"),
        ("refuted", "预期被反证", "人工评估或已归档数值比较否定原预期；与整轮最终赚钱或亏损分别记录，并保留评估类别。"),
        ("unknown", "预期无法核验", "原预期、窗口、实际事实或时点证据不足，不能强判成立或失败。"),
    ):
        add(_definition("expectation_check." + code, label, text,
                        source=[_source("shilun/trade_journal/service.py", "TradeJournalService.append_expectation_check"),
                                _source(GUIDE, "预期复盘核验")], status="review_outcome", axis="expectation_review",
                        limitations=["状态本身不证明机器核验；须另看evaluation_class和machine_verified。默认人工评估；数值比较仅核对冻结条件与不可变来源，不证明来源真实性、事前性、因果或模式有效。", "与P2预期分布分类分别记录。 "]))
    for enum, axis in ((ExecutionState, "execution"), (ScenarioState, "scenario")):
        for state in enum:
            add(_definition(f"state.{axis}_{state.value}", state.value, "既有执行/场景流程状态，不是成交事实、情绪或收益结论。",
                            source=_source("shilun/trading/contracts.py", enum.__name__),
                            status="implemented_contract", axis=axis))
    for code, label, text in (
        ("near_ma5", "价格靠近MA5", "连续客观特征，保留价格相对哪日MA5的偏离及均线方向；不自动推为用户做MA5。"),
        ("chase", "追涨或远离均线", "旧交割单的公式研究标签；必须带原公式与阈值版本，不能覆盖用户翘板等意图。"),
        ("resilience", "抗跌", "指定下跌窗口内相对参照物跌得少或逆势强；不自动证明回暖后领涨。"),
        ("lead_after_resilience", "抗跌后领涨", "先冻结下跌阶段抗跌候选，再用后来企稳窗口观察主动领涨和带动；未有统一机器阈值。"),
    ):
        add(_definition("technical_feature." + code, label, text, source=_source(GUIDE, "客观特征与先后验证"),
                        status="research_hypothesis" if code == "lead_after_resilience" else "descriptive_feature"))
    for code, label, text, symbol in (
        ("limit_up", "收盘涨停", "收盘与同日官方涨停价匹配；不按名称猜5/10/20/30%限制。", "daily_evidence"),
        ("broken_board", "炸板未回封", "当日触及涨停但收盘未封；盘中开板后回封仍归收盘封板组。", "daily_evidence"),
        ("board_break", "断板", "前日连续涨停者当日未继续收盘封板；与当天是否触板的炸板是不同轴。", "evaluate_stock"),
        ("one_price", "一字封死", "日线高低收与官方涨停价等必要事实确认一字；不是已证明换手承接或可以买到。", "daily_evidence"),
        ("exchange_proxy", "换手代理", "非一字日线有价格区间；需成交量及逐笔资料才能进一步讨论可参与性。", "daily_evidence"),
    ):
        add(_definition("technical_feature." + code, label, text,
                        source=_source("shilun/review/strong_stock_candidates.py", symbol, CORE_VERSION),
                        status="implemented_proxy", rule_version=CORE_VERSION))
    for code, label, text in (
        ("mainline_candidate", "主线候选", "核心资格、同题材归因明确的跨日扩散和反馈满足准入后得到的观察方向；不是所有单日共振都是主线。"),
        ("absorption", "承接", "抛压后价格是否被接住的观察；日线收益与收位只是代理，不证明盘口买单主体。"),
        ("initiative", "主动性", "相对指数及同类先行、分歧后自主走强的观察；未给统一算法时保留人工原文。"),
        ("linkage", "带动与联动", "同涨只证明共同价格表现；带动须更细时间顺序和参照证据，不能直接认因果。"),
    ):
        selection_feature = code == "mainline_candidate"
        feature_version = THEME_SELECTION_VERSION if selection_feature else P2_VERSION
        add(_definition("technical_feature." + code, label, text,
                        source=_source("shilun/review/theme_selection.py" if selection_feature else "shilun/review/theme_evidence.py",
                                       "ThemeSelectionReplay.evaluate" if selection_feature else "front_support/theme_evidence", feature_version),
                        status="descriptive_feature", rule_version=feature_version))
    for code, label, text in (
        ("draft", "方法草稿", "方法或研究协议尚在整理，允许字段缺失；不授交易或回测资格。"),
        ("trial", "方法试用", "用户以明确版本开展试用/研究；不表示胜率或风险已经验证。"),
        ("active", "方法使用中", "用户将该版本标为使用中；不是策略优势成立、自动启用或开仓许可。"),
        ("retired", "方法已退役", "停止当前使用而保留全部旧版本、尝试与结果；不是删除历史或证明失败因果。"),
    ):
        add(_definition("state.method_" + code, label, text,
                        source=_source("shilun/trade_journal/mode_cards.py", "STATES/ModeCardService", "trade-journal-mode-cards-v1"),
                        status="implemented_contract", rule_version="trade-journal-mode-cards-v1", axis="method_usage"))
    for code, label, text in (
        ("actual_trades", "真实成交报告值", "来自已归档实际成交来源的人工研究报告；仍须核对完整范围、成本和统计分母，不因标签变为机器已验证。"),
        ("simulation", "模拟报告值", "按声明的模型和数据形成的模拟结果，与真实已实现盈亏分开；须记录费用、可成交及样本范围。"),
        ("observation", "观察反馈", "未买或固定研究群体的行情反馈；不能填写为真实交易胜率、已实现盈亏或交易收益。"),
        ("inconclusive", "研究无结论", "证据或范围不足，只保存问题、备注与缺项；不以零样本填胜率或收益。"),
    ):
        add(_definition("result_type." + code, label, text,
                        source=_source("shilun/trade_journal/mode_cards.py", "ModeCardService._append_items", "trade-journal-mode-cards-v1"),
                        status="implemented_contract", rule_version="trade-journal-mode-cards-v1", axis="research_report_type",
                        limitations=["数值须有已有来源、正分母与截止日；报告值machine_verified=false，方法状态不证明优势。",
                                     "在登记验证日期范围内只表示时间范围相符；后加参数须按自己的登记时间和协议版本判断。 "]))
    for code, label, text, symbol in (
        ("closed_episode", "完整清仓库存轮", "同账户同证券已闭合且成本/费用/库存可核对的一整轮；总体统计每轮一次。", "JournalAnalyticsService.report"),
        ("mode_slice", "模式数量切片", "完整真实库存轮中，按确切模式版本和实际FIFO股数归属的部分；同一轮可出现在多个模式组，不能把切片n相加当独立交易轮。", "JournalAnalyticsService.report"),
        ("subjective_labels", "主观决策标签", "环境、角色、模式及执行差异来自用户决策原文；未填写或多种判断为unknown/mixed，不能替代数量归属。", "_decision_dimension"),
        ("unallocated_mode", "模式未分配残余", "analytics的mode.unallocated是未完成明确股数归属的统计占位；保留真实账户现金结果，不把它当用户意图或已知模式收益。", "JournalAnalyticsService.report"),
        ("current_portfolio_snapshot", "当前组合事实快照", "在本次captured_at保存当前持仓、已有报价/时点、配置、风险和提议情景；不是过去某笔交易的账户状态或完整历史净值。", "PortfolioRiskService.capture_snapshot"),
    ):
        add(_definition("scope." + code, label, text,
                        source=_source("shilun/trade_journal/analytics.py", symbol, "trade-journal-analytics-v1"),
                        status="implemented_contract", rule_version="trade-journal-analytics-v1", universe=text,
                        time_basis="按所选完整轮清仓日期/模式切片归属，或组合快照真实captured_at；分别显示"))
    add(_definition("scope.quantity_allocation", "逐股模式归属", "每笔真实成交按mode_ref与quantity分配；卖出还须对应真实FIFO买入批次。未分配、未知与混合保留，不因补标签改变真实库存或总盈亏。",
                    source=_source("shilun/trade_journal/allocations.py", "TradeAllocationService._normalize/_calculate", "trade-journal-allocations-v1"),
                    status="implemented_contract", rule_version="trade-journal-allocations-v1", unit="股/真实FIFO匹配",
                    limitations=["同一成交分配总股数不得超过真实股数；模式卖出不能借用后来买入。",
                                 "数量归属是人工审计，不授予事前/回测资格，不证明计划实际执行。 "]))
    for code, label, formula, numerator, denominator, unit, path, symbol, owner_version in (
        ("mode_slice_pnl", "模式切片含费盈亏", "复用真实FIFO匹配盈亏按明确归属股数切分；未能证明买入模式库存时归属未知。", "已知模式归属的FIFO含费盈亏切片之和", None, "元CNY", "allocations.py", "TradeAllocationService._calculate", "trade-journal-allocations-v1"),
        ("mode_slice_return", "模式切片成本回报", "模式切片含费盈亏/对应FIFO实际含费买入成本；不是账户权益或最大资金占用回报。", "已知模式切片净盈亏", "对应数量切片的实际含费买入成本", "比例；可为负或超过1", "analytics.py", "_stats", "trade-journal-analytics-v1"),
        ("group_win_rate", "分组成员获利比例", "净盈亏>0的已知成员数/已知成员数；必须显示完整轮或模式切片的单位。", "组内净盈亏>0的有效成员数", "组内净盈亏已知成员数", "比例0—1", "analytics.py", "_stats", "trade-journal-analytics-v1"),
        ("group_profit_factor", "分组收益损失比", "正净盈亏之和/负净盈亏绝对值之和；无亏损分母为未知，不报无穷优势。", "组内正净盈亏之和", "组内负净盈亏绝对值之和", "倍", "analytics.py", "_stats", "trade-journal-analytics-v1"),
        ("current_portfolio_value", "所读组合持仓估值", "逐持仓数量×带原始时点的已有报价；缺价/时点则总值未知，已知组成单列。", "所读取持仓的可估值市值之和", None, "元CNY", "analytics.py", "PortfolioRiskService.context", "trade-journal-analytics-v1"),
        ("configured_stop_risk", "既有止损风险合计", "仅汇总既有单票calculate_dynamic_target的full_hold_worst_loss；不是组合VaR或极端跳空损失保证。", "已有配置且可核算持仓的单票stop风险之和", None, "元CNY", "analytics.py", "PortfolioRiskService.context", "trade-journal-analytics-v1"),
    ):
        add(_definition("metric." + code, label, formula,
                        source=_source("shilun/trade_journal/" + path, symbol, owner_version),
                        status="implemented_metric", rule_version=owner_version, formula=formula,
                        numerator=numerator, denominator=denominator, unit=unit,
                        time_basis="分组按已核对完整清仓轮；组合按本次真实captured_at和报价原始时间", window="明确筛选区间/快照时点",
                        limitations=["每个结果必须显示单位、有效n/完整轮episode_n、排除与未知；分组切片n不可相加冒充独立交易轮。",
                                     "主观标签不代替股数归属，当前账户配置不代替核验净资产或历史净值；缺历史权益不计算账户回撤。 "]))
    return records


def get_definitions(category: str | None = None) -> dict[str, Any]:
    """Return an isolated JSON-safe catalogue; no unknown-to-manual coercion."""
    records = _records()
    identifiers = [record["id"] for record in records]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("Duplicate trade-journal definition IDs")
    if category is not None:
        records = [record for record in records if record["category"] == category]
    return deepcopy({
        "version": DEFINITIONS_VERSION,
        "categories": [{"id": key, "label": label} for key, label in _CATEGORY_LABELS.items()],
        "definitions": records,
        "boundary": "词典描述现有规则及人工语义，不推定用户采用、不改变执行策略、不证明收益。",
    })


def get_definition(identifier: str) -> dict[str, Any] | None:
    """Resolve an exact stable ID, returning None for an unknown ID or label."""
    return next((row for row in get_definitions()["definitions"] if row["id"] == identifier), None)


def validate_definition_references(
    references: Iterable[str | Mapping[str, Any]] | str | Mapping[str, Any],
    expected_category: str | None = None,
) -> dict[str, Any]:
    """Validate exact IDs, category, and any frozen rule version.

    A historical frozen definition is retained by its owner; a mismatch here means
    it cannot be silently relabelled using today's registry. Empty refs are valid
    structurally and do not establish any data-qualification dimension.
    """
    if isinstance(references, (str, Mapping)):
        references = [references]
    catalogue = {row["id"]: row for row in get_definitions()["definitions"]}
    errors, resolved = [], []
    for ref in references:
        identifier = ref if isinstance(ref, str) else ref.get("id") if isinstance(ref, Mapping) else None
        row = catalogue.get(identifier) if isinstance(identifier, str) else None
        if row is None:
            errors.append({"id": identifier, "code": "unknown_definition", "message": "未知术语引用；保留原文，不自动替换为manual。"})
            continue
        category = expected_category or (ref.get("category") if isinstance(ref, Mapping) else None)
        if category and category != row["category"]:
            errors.append({"id": identifier, "code": "category_mismatch", "message": "术语所属轴与记录字段不一致。"})
            continue
        version = ref.get("rule_version") if isinstance(ref, Mapping) else None
        if version is not None and version != row["rule_version"]:
            errors.append({"id": identifier, "code": "version_mismatch", "message": "术语规则版本不一致；历史快照不得改绑当前定义。"})
            continue
        resolved.append(deepcopy(row))
    return {"valid": not errors, "version": DEFINITIONS_VERSION, "resolved": resolved, "errors": errors}


validate_references = validate_definition_references

__all__ = ["DEFINITIONS_VERSION", "VERSION", "get_definitions", "get_definition",
           "validate_definition_references", "validate_references"]
