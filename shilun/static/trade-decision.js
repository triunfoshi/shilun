/* Dated analytical views. Rendering never calls order or holdings endpoints. */
(() => {
  'use strict';
  const state = {plan:null, date:'', direction:'', research:null, researchDate:'', researchRef:'', researchSeq:0, researchPoll:null, evalWindow:'5', mode:'trend', sampleDate:'', sectorKind:'concept', sectorCode:'', sectorScope:'recent', sectorSearch:'', sectorSort:'n', sectorMinimum:'3', sectorPage:0, seq:0, liveSeq:0, poll:null, initialized:false};
  const e = v => String(v ?? '').replace(/[&<>"']/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const ok = v => typeof v === 'number' && Number.isFinite(v);
  const n = (v,d=2) => ok(v) ? v.toFixed(d) : '—';
  const pct = v => ok(v) ? `<span class="${v>0?'decision-positive':v<0?'decision-negative':''}">${v>0?'+':''}${n(v)}%</span>` : '—';
  const amount = v => ok(v) ? `${n(v/1e8)}亿` : '—';
  const statusLabel = {ready:'已有证据', partial:'部分可用', conditional:'条件预案', waiting:'等待行情', missing:'缺数据', observed:'已读取', stale:'数据过期', pending:'待确认'};
  const byId = id => document.getElementById(id);
  const label = h => typeof h==='string'?h:h.label;
  // Caliber's inline HelpCircle + label follows vibe-astock (Apache-2.0;
  // licenses/vibe-astock.txt). Retain our hover/focus/touch and top-layer popup.
  const heading = h => `<span class="decision-heading-label">${e(label(h))}</span>${h.help?` <button type="button" class="decision-help" aria-label="${e(label(h))}口径" aria-expanded="false" data-decision-help="${e(h.help)}"><svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="10"/><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3m.08 4h.01"/></svg><span>口径</span></button>`:''}`;
  const table = (headers,rows,caption='') => `<div class="decision-table-wrap" tabindex="0" role="region" aria-label="${e(caption||headers.map(label).join('、'))}"><table class="decision-table">${caption?`<caption>${e(caption)}</caption>`:''}<thead><tr>${headers.map(h=>`<th scope="col">${heading(h)}</th>`).join('')}</tr></thead><tbody>${rows.length?rows.map(row=>`<tr>${row.map(v=>`<td>${v}</td>`).join('')}</tr>`).join(''):`<tr><td colspan="${headers.length}">该层暂无符合条件的记录，继续观察上游条件。</td></tr>`}</tbody></table></div>`;
  const list = rows => `<ul class="decision-evidence-list">${rows.map(r=>`<li>${e(r)}</li>`).join('')}</ul>`;
  const verdict = (title,body) => `<div class="decision-verdict"><strong>${e(title)}</strong>${body}</div>`;
  const section = (title,html) => `<section class="decision-section"><h2>${e(title)}</h2>${html}</section>`;
  const option = (value,label,selected) => `<option value="${e(value)}"${value===selected?' selected':''}>${e(label)}</option>`;
  const chain = plan => `<ol class="decision-chain" aria-label="七步决策链">${plan.steps.map(s=>`<li><strong>${e(s.id)} ${e(s.title)}</strong><small>${e(statusLabel[s.status]||s.status)}</small><p>${e(s.conclusion)}</p></li>`).join('')}</ol>`;

  function indexTable(plan) {
    const m=plan.market, o=m.outlook, f=m.citic;
    return `<div class="decision-evidence-box">${verdict(o.title,`<div class="decision-evidence-columns"><div><b>支持依据</b>${list(o.support)}</div><div><b>制约与反证</b>${list(o.counter)}</div></div><p><b>升级：</b>${e(o.upgrade)}</p><p><b>失效：</b>${e(o.failure)}</p>`)}
      ${table(['指数','较昨收','站上均线：昨日→当日','MA5 / MA10 / MA20','多头排列'],m.indices.map(r=>[
      e(r.name),pct(r.change_pct),`${r.previous_above_count??'—'} → ${r.above_count??'—'} / 3`,
      ['5','10','20'].map(k=>n(r.ma[k])).join(' / '),r.bull_aligned===null?'未知':r.bull_aligned?'是':'尚未形成'
    ]),'指数涨跌相对昨日收盘；均线为5/10/20个完整交易日')}
      <div class="decision-futures"><h3>中信净空变化 · ${e(f.date)}</h3><p>展示合约合计 <b>${ok(f.net_short)?f.net_short.toLocaleString():'—'}手</b>，较前日 <b>${ok(f.change)?(f.change>0?'+':'')+f.change.toLocaleString():'—'}手</b></p>
      <details><summary>合约明细与判断口径</summary>${table(['合约','持多','持空','净空','净空日变化'],f.rows.map(r=>[e(r.contract),n(r.long,0),n(r.short,0),n(r.net_short,0),ok(r.change)?`${r.change>0?'+':''}${n(r.change,0)}`:'—']))}<p>${e(f.note)}</p><p>${e(o.rule)}</p></details></div></div>`;
  }

  const metricText = (m, unit='%') => m&&ok(m.value)?`${n(m.value)}${unit}<small>有效 ${m.n} / ${m.total}</small>`:'—<small>样本或数据不足</small>';
  const conditionalMetricText = (m, label) => m&&ok(m.value)?`${n(m.value)}%<small>${e(label)} ${m.n} 只</small>`:'—<small>无可确认的条件样本</small>';
  function playbookFacts(mode) {
    const d=mode.latest, m=d.metrics;
    let specifics='';
    if(mode.key==='trend')specifics=table(['最新反馈日','开盘溢价中位数','正溢价占比','低开占比','高开后收低于开盘占比'],[[e(d.date),metricText(m.primary),metricText(m.breadth),metricText(m.low_open),conditionalMetricText(m.giveback,'有效高开样本')]], '首板先手反馈；高开回落占比仅以正溢价样本为分母。日线不能定位何时回落。');
    if(mode.key==='relay')specifics=`${table(['昨日梯队','全体晋级率','昨日非一字晋级率'],(d.ladder||[]).map(r=>[e(r.stage),metricText(r.all),metricText(r.exchange)]),'逐梯队比较；非一字只是换手代理，不证明能买到。晋级率没有固定健康线。')}
      ${table(['昨断板／炸板次日收益','失败样本大跌占比','昨日一字梯队次日收益','昨日一字晋级率'],[[metricText(m.failed_return),metricText(m.negative),metricText(m.locked_return),metricText(m.locked_promotion)]])}
      <p class="decision-muted">今日收盘梯队：${Object.entries(d.heights||{}).map(([height,count])=>`${e(height)}板 ${count}只`).join('、')||'无可确认梯队'}。表面梯队完整不直接判健康。</p>`;
    if(mode.key==='crowding')specifics=table(['昨日核心 / 今日上涨','剔除最强股后的反馈','距近5日最高价回撤','跌破昨收后收回比例'],[[`${d.cohort_n} / ${d.positive_n??'—'}`,ok(d.ex_best_median)?n(d.ex_best_median)+'%':'—',metricText(m.drawdown),conditionalMetricText(m.recovered,'有效跌破昨收样本')]], '核心名单按昨日冻结。回撤为收盘相对近5日最高价；日线收回昨收不等于盘中主动修复。');
    return specifics+table(['最新日换手率','换手 / 自身前10日均值','量 / 自身前10日均量','缩量走弱占比','放量走弱占比','一字占比'],[[metricText(m.turnover),metricText(m.turnover_ratio,'倍'),metricText(m.volume_ratio,'倍'),metricText(m.dry_weak),metricText(m.hot_weak),metricText(m.one_price)]], '量比与换手逐股先对照自身，再取中位数；不使用全市场统一健康换手率。');
  }

  const valueRate = v => ok(v)?`${n(v)}%`:'—';
  const signedRate = v => ok(v)?`${v>0?'+':''}${n(v)}%`:'—';
  const shortDate = d => d ? e(d.slice(5)):'—';
  const coverage = p => `${p.days_n}/${p.days_total} 日达标 · ${p.samples_n}/${p.cohort_n} 股票日`;

  function sectorContext(mode, research) {
    const sectors=research.first_board_sectors, day=mode.daily.find(x=>x.date===state.sampleDate)||mode.latest;
    const group=sectors?.groups.find(x=>x.code===state.sectorCode&&x.kind===state.sectorKind);
    const days=state.sectorScope==='day'?[day]:mode.daily.slice(-Number(state.evalWindow));
    const ticks=group?new Set(group.members):null;
    const rows=days.flatMap(d=>d.members.filter(r=>!ticks||ticks.has(r.ticker)).map(r=>({...r,formation_date:d.formation_date,feedback_date:d.date})));
    return {sectors,day,group,days,rows,stat:state.sectorScope==='day'?(group||sectors)?.daily?.[day.date]:(group||sectors)?.windows?.[state.evalWindow]};
  }

  function sectorFeedback(mode,research) {
    const c=sectorContext(mode,research), a=c.sectors;
    if(!a)return '<p class="decision-muted">当前固定快照尚无板块分组；点击“读取最新评估”查看。</p>';
    const stat = g => state.sectorScope==='day'?g.daily[c.day.date]:g.windows[state.evalWindow];
    let groups=a.groups.filter(g=>g.kind===state.sectorKind&&g.name.toLowerCase().includes(state.sectorSearch.toLowerCase())&&(stat(g)?.total||0)>0&&(stat(g)?.n||0)>=Number(state.sectorMinimum));
    groups.sort((a,b)=>{const x=stat(a)[state.sectorSort],y=stat(b)[state.sectorSort];return (ok(y)?y:-Infinity)-(ok(x)?x:-Infinity)||a.code.localeCompare(b.code);});
    const pages=Math.max(1,Math.ceil(groups.length/12));state.sectorPage=Math.min(state.sectorPage,pages-1);
    const visible=groups.slice(state.sectorPage*12,state.sectorPage*12+12);
    const quantileHead={label:'低位 P25 / 中位 P50 / 高位 P75',help:a.quantile_rule};
    const statsText=q=>q?`${pct(q.p25)} / ${pct(q.p50)} / ${pct(q.p75)}`:'—';
    const selected=c.group?.name||'全部首板（同股同事件去重）';
    const perDay=c.group?c.days.map(d=>{
      const q=c.group.daily[d.date],v=q?.coordination;
      const first=ok(v?.first_board_n)?`${v.first_board_complete?'':'已确认 '}${v.first_board_n} 家<small>分类覆盖 ${v.first_board_valid_n}/${v.member_n}</small>${v.first_board_members?.length?`<details><summary>同日首板名单</summary><p>${v.first_board_members.map(x=>e(x.name)+' '+e(x.ticker)).join('、')}</p></details>`:''}`:'—';
      return [e(d.formation_date),`${v?.member_n??c.group.members.length} 家`,first,pct(v?.median_pct),v?`${v.valid_n}/${v.member_n}`:'—',e(d.date),q?`${q.n}/${q.total}`:'无首板样本',statsText(q)];
    }):[];
    return `<section class="decision-sector-research"><div class="decision-subheading"><h3 class="decision-title-with-help">${heading({label:'首板板块分布与协同',help:a.quantile_rule+' '+a.coordination_rule})}</h3><span class="decision-muted">归属快照 ${e(a.membership_date||'缺失')}</span></div>
      <div class="decision-sector-controls"><label>板块视角<select id="decisionSectorKind">${option('concept','概念题材',state.sectorKind)}${option('industry','三级行业',state.sectorKind)}</select></label><label>样本时间<select id="decisionSectorScope">${option('recent',`最近${state.evalWindow}个反馈日`,state.sectorScope)}${option('day','单个反馈日',state.sectorScope)}</select></label><label>反馈日<select id="decisionSampleDate"${state.sectorScope==='recent'?' disabled':''}>${[...mode.daily].reverse().map(x=>option(x.date,x.date,c.day.date)).join('')}</select></label><label>最低有效样本<select id="decisionSectorMinimum">${option('3','至少3个',state.sectorMinimum)}${option('1','全部有样本',state.sectorMinimum)}</select></label><label>排序<select id="decisionSectorSort">${option('n','有效样本数',state.sectorSort)}${option('p25','低位 P25',state.sectorSort)}${option('p50','中位 P50',state.sectorSort)}${option('p75','高位 P75',state.sectorSort)}</select></label><label>查找板块<input id="decisionSectorSearch" type="search" value="${e(state.sectorSearch)}" placeholder="板块名称" /></label></div>
      <p class="decision-muted">当前容量范围：首板日7日均额${e(research.band_names?.[research.default_band]||research.default_band)}。${e(a.scope_note)} 分位数为样本分布，不是买点，也不据此自动选板块。</p>
      <div class="decision-sector-selection"><button type="button" data-decision-sector="" aria-pressed="${!c.group}">全部首板</button><b>${e(selected)}</b><span>有效 ${c.stat?.n??0}/${c.stat?.total??0} 个事件 · ${c.stat?.stock_n??0} 只股票 · ${c.stat?.dates_n??0} 日</span><span>低／中／高 ${statsText(c.stat)}</span></div>
      ${table(['板块', '板块总家数','有效／样本',quantileHead,'正开盘溢价占比','样本状态'],visible.map(g=>{const q=stat(g);return [`<button type="button" data-decision-sector="${e(g.code)}" aria-pressed="${g.code===c.group?.code}">${e(g.name)}</button>`,`${g.members.length} 家`,`${q.n}/${q.total}<small>${q.stock_n}股 · ${q.dates_n}日</small>`,statsText(q),valueRate(q.positive_pct),e(q.status==='insufficient'?'不足3个，不报分位':q.status==='partial'?'行情覆盖不足95%':q.status==='small'?'小样本，仅供观察':'描述统计')];}),`开盘溢价按股票事件计算；当前 ${groups.length} 个有样本板块，按所选字段排序。`)}
      <div class="decision-sector-pager"><button type="button" data-sector-page="-1"${state.sectorPage===0?' disabled':''}>上一页</button><span>${state.sectorPage+1} / ${pages}</span><button type="button" data-sector-page="1"${state.sectorPage>=pages-1?' disabled':''}>下一页</button></div>
      ${c.group?`<h3>${e(c.group.name)} · 首板日协同与次日开盘分布</h3>${table(['首板日','板块全部个股数',{label:'同日首板数量',help:a.coordination_rule},'板块当日涨幅中位数','涨幅覆盖','反馈日','容量内有效／样本',quantileHead],perDay)}<p class="decision-muted">同日首板统计全板块，不受50／100亿容量档限制，排除连板；点击可核对名单。板块涨幅取全成员对前收的涨幅中位数。分类不完整时仅报已确认数。这里验证同日发酵；分钟级同时启动还需分时数据。${e(c.group.source||'')}；成分采集 ${e(c.group.fetched_at||'未知')}。</p>`:'<p class="decision-muted">点击板块名称，查看该板块逐日协同、开盘分布和下方首板个股。</p>'}</section>`;
  }

  function sampleTable(mode,research) {
    const d=mode.daily.find(x=>x.date===state.sampleDate)||mode.latest;
    const context=mode.key==='trend'?sectorContext(mode,research):null;
    const rows=context?context.rows:(d.members||[]).map(r=>({...r,formation_date:d.formation_date,feedback_date:d.date}));
    const valid=rows.filter(r=>r.prices&&ok(r.opening));
    const sorted=[...valid].sort((a,b)=>(mode.key==='trend'?a.opening-b.opening:a.close_return-b.close_return)||a.ticker.localeCompare(b.ticker)||a.feedback_date.localeCompare(b.feedback_date));
    const candidates=sorted.length?[sorted[0], [...sorted].sort((a,b)=>Math.abs(a.opening)-Math.abs(b.opening)||a.ticker.localeCompare(b.ticker))[0], sorted[Math.floor(sorted.length/2)], [...sorted].sort((a,b)=>(b.first_board_evidence?.avg7_cny||0)-(a.first_board_evidence?.avg7_cny||0)||a.ticker.localeCompare(b.ticker))[0], sorted.at(-1)]:[];
    const chosen=[...new Map(candidates.map(r=>[r.ticker+r.feedback_date,r])).values()];
    const renderRows = rs => rs.map(r=>{
      const p=r.prices||{}, f=r.first_board_evidence;
      const stock=[`<b>${e(r.name)}</b><small>${e(r.ticker)}</small>`,e(r.formation_date),e(r.feedback_date)];
      if(mode.key==='trend')stock.push(amount(f?.float_cap_cny),amount(f?.amount_cny),amount(f?.avg7_cny));
      return [...stock,n(p.previous_close),n(p.open),pct(r.opening),n(p.close),pct(r.close_return),
        f?`<details class="decision-proof"><summary>首板证据</summary><p>${e(f.prior_date)}收盘 ${n(f.prior_close)}，涨停价 ${n(f.prior_up_limit)}，前日连板数 ${f.prior_streak??'未知'}；${e(f.date)}收盘 ${n(f.close)}＝涨停价 ${n(f.up_limit)}。流通市值：${e(f.float_cap_source||'旧版本无字段')}。</p></details>`:ok(p.close)?`${n(p.close)} ÷ ${n(p.previous_close)} − 1`:'价格连续性未通过，收益留空'];
    });
    const headers=['个股',mode.key==='trend'?'首板日期':'冻结日期','反馈日期',...(mode.key==='trend'?[{label:'首板日流通市值',help:research.first_board_sectors?.capital_rule||'按首板当日数据取值，缺失不补今日值。'},'首板日成交额','首板日7日均额']:[]),'前日收盘 / 元','次日开盘 / 元','开盘溢价','次日收盘 / 元','收盘反馈',mode.key==='trend'?'入组依据':'收盘公式'];
    return `<section class="decision-samples"><div class="decision-research-heading"><div><h3>${mode.key==='trend'?'首板个股验算':'冻结名单验算'}</h3><p class="decision-muted">${context?e(context.group?.name||'全部首板')+' · '+(state.sectorScope==='recent'?`近${state.evalWindow}个反馈日`:e(d.formation_date)+' 首板 → '+e(d.date)+' 反馈'):e(d.formation_date)+' 冻结 → '+e(d.date)+' 反馈'} · ${rows.length} 个事件，价格连续性通过 ${valid.length} 个</p></div>${mode.key!=='trend'?`<label>反馈日 <select id="decisionSampleDate">${[...mode.daily].reverse().map(x=>option(x.date,x.date,d.date)).join('')}</select></label>`:''}</div>
      <p class="decision-formula">开盘溢价 ＝（次日开盘 ÷ 前日收盘 − 1）× 100%。收盘反馈另算，均未扣费用。</p>
      ${valid.length?table(headers,renderRows(chosen),'验算示例：按低端、近零、中位附近、高容量和高端选取并去重；不是推荐名单。'): '<div class="decision-empty">所选范围没有可验算的原始价格。可调整板块／日期或读取最新评估；已有收益不倒推价格。</div>'}
      <details><summary>查看全部 ${rows.length} 个冻结样本</summary>${table(headers,renderRows(rows))}</details>
      ${mode.key==='trend'?'<p class="decision-muted">这里的首板是“前日未封板、当日收盘涨停”，包含反包首板及现有档案中的 ST；不等于已通过 MA5 与题材筛选。首板日成交额是单日值，7日均额是容量分档依据，流通市值另算。</p>':''}</section>`;
  }

  function diagnosticPanel(mode) {
    const d=mode.diagnostic;
    if(!d)return '';
    const missing=d.coverage_checks.filter(x=>!x.covered);
    return `<div class="decision-diagnostic"><h3>${missing.length?'为什么暂不能判适用':'评估依据与覆盖'}</h3><p>价格反馈：<b>${d.price_support===true?'已有正反馈支持':d.price_support===false?'尚未达到条件':'样本不足'}</b>；数据覆盖与参与条件继续单独核验。</p>
      <p class="decision-muted">覆盖达标：${d.coverage_checks.filter(x=>x.covered).map(x=>e(x.title)+' '+x.valid_n+'/'+x.total_n).join('；')||'暂无'}。</p>
      ${(d.sample_gaps||[]).length?`<details><summary>剩余边界与原因 · ${d.sample_gaps.length}只</summary>${list(d.sample_gaps.map(r=>r.name+' '+r.ticker+'：'+r.turnover_gaps.map(x=>x.date+' '+x.reason).concat(r.price_reason?[r.price_reason]:[]).join('；')))}<p>上市前和已确认停牌的历史不填造换手数据；价格连续性未核验通过的收益仍留空。</p></details>`:''}
      ${missing.length?`<ul>${missing.map(x=>`<li><b>${e(x.title)}</b>：有效 ${x.valid_n}/${x.total_n} 只，需至少 ${x.required_n} 只。${x.key==='turnover_ratio'?'每只都需自身前10个完整交易日的换手数据。':''}</li>`).join('')}</ul>`:''}
      ${!d.sample_gaps&&d.missing_turnover_dates.length&&missing.some(x=>x.key==='turnover_ratio')?`<p class="decision-muted">缺换手日期：${d.missing_turnover_dates.map(e).join('、')}。缺数据不等于股票走弱；不靠放宽完整性门槛把“未知”改为适用。</p>`:''}
      ${d.comparison_gaps.length?`<details><summary>自身历史对照还有 ${d.comparison_gaps.length} 项覆盖不足</summary>${list(d.comparison_gaps.map(x=>`${x.dimension} · ${x.period}：${x.days_n}/${x.days_total}日达标，至少需要${x.required_days}日。`))}</details>`:''}</div>`;
  }

  function researchFacts(mode) {
    const d=mode.latest, m=d.metrics;
    const facts=mode.key==='trend'?
      [['开盘溢价中位数',pct(m.primary.value)],['正溢价占比',metricText(m.breadth)],['低开占比',metricText(m.low_open)],['高开后收低于开盘',conditionalMetricText(m.giveback,'有效高开样本')]]:
      mode.key==='crowding'?
      [['昨日核心 / 今日上涨',`${d.cohort_n} / ${d.positive_n??'—'}`],['剔除最强股后',pct(d.ex_best_median)],['距近5日高点回撤',metricText(m.drawdown)],['跌破昨收后收回',conditionalMetricText(m.recovered,'有效跌破昨收样本')]]:
      [['换手梯队晋级率',metricText(m.promotion)],['失败票次日收盘',metricText(m.failed_return)],['一字梯队收盘反馈',metricText(m.locked_return)],['一字梯队晋级率',metricText(m.locked_promotion)]];
    return `<div class="decision-fact-strip">${facts.map(([k,v])=>`<div><span>${e(k)}</span><strong>${v}</strong></div>`).join('')}</div>`;
  }

  function renderPlaybooks(research) {
    const a=research.assessments, modes=a.windows[state.evalWindow];
    const mode=modes[state.mode]||modes.trend, d=mode.latest, history=mode.daily;
    const base=mode.dimensions[0].baseline, recent=mode.dimensions[0].recent;
    const sensitive=a.windows['3'][mode.key].status!==a.windows['5'][mode.key].status||a.windows['3'][mode.key].change!==a.windows['5'][mode.key].change;
    const support=mode.reasons.concat(mode.risks.map(x=>'反证：'+x));
    const coverageHelp=a.method+' '+a.qualification_rule+' 收益覆盖是正收益样本比例；板块协同在下方分组单列。';
    const tabs=Object.values(modes).map((m,i)=>`<button type="button" role="tab" id="decisionMode-${e(m.key)}" aria-controls="decisionModePanel" aria-selected="${m.key===mode.key}" tabindex="${m.key===mode.key?'0':'-1'}" data-decision-mode="${e(m.key)}"><span class="decision-tab-name"><b>${e(m.name)}</b><span aria-hidden="true">${String(i+1).padStart(2,'0')}</span></span><span class="decision-tab-metric">${e(m.dimensions[0].label)} · 近${state.evalWindow}日</span><strong class="decision-tab-value">${signedRate(m.dimensions[0].recent.value)}</strong><span class="decision-tab-status"><span>变化 <b>${e(m.change_label)}</b></span><span>${e(m.status_label)}</span></span></button>`).join('');
    const compRows=mode.dimensions.map(x=>`<tr><th scope="row"><b>${e(x.dimension)}</b><small>${e(x.label)}${x.higher_is_better?'':' · 越低越好'}</small></th><td>${ok(x.baseline.value)?(x.key==='primary'?signedRate(x.baseline.value):valueRate(x.baseline.value)):'<span class="decision-muted">证据不足</span>'}<small>${e(coverage(x.baseline))}${!ok(x.baseline.value)?`；需 ≥${x.baseline.required_days} 日`:''}</small></td><td>${ok(x.recent.value)?(x.key==='primary'?signedRate(x.recent.value):valueRate(x.recent.value)):'<span class="decision-muted">证据不足</span>'}<small>${e(coverage(x.recent))}</small></td><td><b>${e(x.change_label)}</b>${ok(x.delta_pp)?`<small>${x.delta_pp>0?'+':''}${n(x.delta_pp)} 个百分点</small>`:'<small>不补零、不作比较</small>'}</td></tr>`).join('');
    const zeroNote=mode.key==='crowding'?`<p class="decision-zero-note"><b>收益与占比请分开看：</b>${e(d.date)} 核心收盘反馈为 ${pct(d.metrics.primary.value)}；大跌占比 ${valueRate(d.metrics.negative.value)}${d.metrics.negative.value===0?' 表示有效样本中没有单日亏损达到 5% 的股票，不是收益为零':''}。缺失值显示“证据不足”。</p>`:'';
    return `<div class="decision-research-heading decision-overview-head"><div><h2>三种玩法，分别判断</h2><p class="decision-muted">各看自己的改善与风险；改善不等于当前可参与。</p></div><label>研究对照窗口 <select id="decisionEvalWindow">${['5','3'].map(v=>option(v,`近${v}日 / 此前10日`,state.evalWindow)).join('')}</select></label></div>
      <div class="decision-mode-tabs" role="tablist" aria-label="选择玩法查看证据">${tabs}</div>
      <p class="decision-muted decision-window-note">点击玩法查看详情。预案固定引用近${a.policy.default_recent}日；本页窗口切换只供研究，不改写预案。</p>
      <section id="decisionModePanel" role="tabpanel" aria-labelledby="decisionMode-${e(mode.key)}" tabindex="0" class="decision-playbook" data-mode="${e(mode.key)}">
        <div class="decision-research-heading decision-panel-heading"><div><h2>${e(mode.name)} · ${e(mode.change_label)}</h2><p>${e(mode.focus)}</p></div><span class="decision-mode-state" data-support="${e(mode.status)}">${e(mode.status_label)}</span></div>
        ${diagnosticPanel(mode)}<div class="decision-analysis-grid"><div class="decision-analysis-main"><div class="decision-subheading"><h3 class="decision-title-with-help">${heading({label:'五维自身对照',help:coverageHelp})}</h3><span class="decision-muted">${shortDate(base.from)}—${shortDate(base.to)} → ${shortDate(recent.from)}—${shortDate(recent.to)}</span></div>
        <div class="decision-table-wrap decision-comparison" role="region" tabindex="0" aria-label="五维自身对照"><table class="decision-table"><thead><tr><th scope="col">维度与口径</th><th scope="col">此前10日</th><th scope="col">近${state.evalWindow}日</th><th scope="col">变化</th></tr></thead><tbody>${compRows}</tbody></table></div>
        <div class="decision-judgement"><h3>盘后判断</h3>${list(support)}${sensitive?'<p class="decision-mode-caution">3日与5日判断不同：窗口敏感，暂不提升确定性。</p>':''}</div></div>
        <aside class="decision-next-session"><h3>次日验证清单</h3><ol>${mode.next_checks.map(x=>`<li>${e(x)}</li>`).join('')}</ol><div class="decision-core-rule"><b>大盘 → 板块 → 个股</b><p>上游失效先暂停；不同玩法分别验证，不自动递补。</p></div><details><summary>样本如何选取</summary><p>${e(mode.cohort)}</p><p>${e(mode.source)}</p></details></aside></div>
        <div class="decision-subheading"><h3>最新反馈 · ${e(d.date)}</h3><span class="decision-muted">红K占比过半连续 ${mode.red_majority_streak} 日 · 最多统计本窗口</span></div>${researchFacts(mode)}${zeroNote}
        <details class="decision-health"><summary>展开换手、量能与${mode.key==='relay'?'晋级梯队':'承接'}明细</summary>${playbookFacts(mode)}<p class="decision-muted">${e(a.participation_rule)}</p><p class="decision-muted">放量走弱提示成交扩大而价格未得到支撑，需核验卖压与承接；不证明主力出货，也不预言次日必跌。低位放量收回、板块同步修复可能是承接，须结合位置、日内走势与大盘核验。成交活跃和方向风险是两件事。</p><p class="decision-muted">${mode.gaps.map(e).join(' ')}</p></details>
        ${mode.key==='trend'?'<p class="decision-definition"><b>正开盘溢价占比</b>：次日开盘高于首板收盘的样本比例。<b>红K频率</b>：次日收盘高于次日开盘的比例。高开低走可有溢价但不是红K；两者都不是板块协同。</p>'+sectorFeedback(mode,research):''}${sampleTable(mode,research)}
        <details class="decision-daily"><summary>逐日反馈与覆盖率 · ${history.length} 个交易日</summary>${table(['反馈日','前日名单','主反馈中位数','红K频率','收益覆盖','亏损尾部占比','量价走弱占比'],history.map(r=>[e(r.date),`${r.cohort_n}只`,metricText(r.metrics.primary),metricText(r.metrics.red),metricText(r.metrics.breadth),metricText(r.metrics.negative),metricText(r.metrics.participation)]))}
        ${mode.key==='relay'?`<h3>昨日断板／炸板样本（失败者保留）</h3>${table(['样本','次日收盘反馈'],(d.failed_members||[]).map(r=>[`${e(r.name)}<small>${e(r.ticker)}</small>`,pct(r.close_return)]))}`:''}</details>
      </section>
      <details class="decision-method"><summary>评估口径与支持度门槛</summary>${list([a.method,a.participation_rule,a.negative_rule,a.qualification_rule,a.cohort_rule])}<p>首板环境沿用现行A路径：量能＞2.3万亿且指数上涨，再核验目标板块；容量按50／100亿档约束。研究对照不代表已证明统计显著性。</p></details>`;
  }

  function assessmentSummary(plan) {
    const c=plan.conclusion, ref=plan.mode_research_ref;
    return `${verdict(plan.route.label,`<p><b>次日新开仓：</b>${e(c.new_entry)}</p><p><b>已有持仓：</b>${e(c.existing_position)}</p>`)}
      ${table(['玩法','较自身此前10日','当前支持度','参与反证与次日验证'],Object.entries(c.groups).map(([k,g])=>[e({trend:'首板套利',relay:'连板接力',crowding:'核心抱团'}[k]),e(g.change_label),e(g.label),`${e(g.health)}<small>${e(g.next_checks?.[0]||'')}</small>`]))}
      <p class="decision-muted">引用 ${e(ref.analysis_date)} 盘后评估；不作模式排名。近期及历史收益、口径与样本在模式收益页查看。</p>
      <a data-mode-research-link href="/ui?decision=research&amp;date=${e(ref.analysis_date)}&amp;snapshot=${e(ref.snapshot_id)}">查看本预案引用的模式收益评估 →</a>`;
  }

  function renderResearch(research) {
    const m=research.market, referenced=!!state.researchRef;
    return `<div class="decision-research-context"><div><span class="decision-muted">${e(research.analysis_date)} 盘后环境</span><h2>${e(m.outlook.title)}</h2></div><dl><div><dt>市场成交额</dt><dd>${ok(m.turnover.market_amount_cny)?n(m.turnover.market_amount_cny/1e12,4)+'万亿':'未知'}</dd></div><div><dt>较前日</dt><dd>${pct(m.turnover_change_pct)}</dd></div><div><dt>首板容量范围</dt><dd>${e(research.band_names[research.default_band]||'全部成交额档')}</dd></div></dl><a href="/ui?decision=plan&amp;date=${e(research.analysis_date)}">明日预案 →</a></div>
      <p class="decision-muted decision-version-note">${referenced?'当前为预案引用的固定版本；读取最新评估可查看本日最新证据。':'当前为本日最新评估；已冻结预案保留原引用。'}</p>
      ${research.assessments?renderPlaybooks(research):verdict('这是旧版收益统计快照','<p>该历史版本尚未包含独立玩法评估；可点击读取最新评估。</p>')}
      ${section('评估来源',`<p>${e(research.provenance.note)}</p><p>生成时间：${e(research.built_at)}。评估按盘后数据形成，次日仍需验证大盘、昨日首板反馈、板块与核心承接。</p><details><summary>评估版本与计算结果</summary><p>${e(research.version)} · ${e(research.snapshot_id)}</p><a href="/api/v1/trade-decision/mode-research?date=${e(research.analysis_date)}&amp;snapshot_id=${e(research.snapshot_id)}" target="_blank" rel="noopener">查看完整样本与评估结果</a></details>`)}`;
  }

  function stockHistory(stock) {
    return table(['日期','较昨收','红K','换手率','换手/前10日均值','成交量/前10日均量','成交额'],stock.history.map(r=>[
      e(r.date),pct(r.return_pct),r.red_k===null?'—':r.red_k?'是':r.one_price?'一字 / 平实体':'否',`${n(r.turnover_pct)}%`,n(r.turnover_ratio_10),n(r.volume_ratio_10),amount(r.amount_cny)
    ]),'成交量与成交额分别比较；基准窗口不完整显示—，不自动判健康');
  }

  function directions(plan) {
    const d=plan.directions.find(r=>r.code===state.direction)||plan.directions[0];
    if(!d) return verdict('当前没有足够证据支持的方向','<p>不补足3＋2名额。等待核心反馈改善或盘中新方向确认。</p>');
    state.direction=d.code;
    const candidates=plan.candidates.filter(c=>c.directions.includes(d.code));
    const refs=d.watch_refs.map(t=>plan.stocks[t]).filter(Boolean);
    return `<p>${e(plan.method.rank)}</p><div class="decision-direction-roster">${plan.directions.map(x=>`<button type="button" data-decision-direction="${e(x.code)}" aria-pressed="${x.code===d.code}"><small>${x.tier==='main'?'主观察':'条件备选'} · ${e(x.mode)}</small>${e(x.name)}</button>`).join('')}</div>
      <div class="decision-direction-detail"><h3>${d.tier==='main'?'主观察':'条件备选'} · ${e(d.name)}</h3>${verdict(d.reason,`<p><b>主要反证：</b>${e(d.counter)}</p><p><b>确认／升级：</b>${e(d.upgrade)}</p><p><b>放弃条件：</b>${e(d.failure)}</p>`)}
      <div class="decision-inline-facts"><div><b>${pct(d.return_median_pct)}</b><span>当日成员收益中位数</span></div><div><b>${pct(d.relative_3_pct)} / ${pct(d.relative_5_pct)}</b><span>近3日 / 5日相对上证</span></div><div><b>${n(d.amount_ratio)}倍</b><span>成交额较昨日</span></div><div><b>${d.valid_n}/${d.member_n}</b><span>价格覆盖</span></div></div>
      <h3>${e(d.role)}</h3>${table(['观察核心','角色','7日均额','较昨收','换手与量能'],refs.map(s=>[`${e(s.name)}<small>${e(s.ticker)}</small>`,d.crowding_refs.includes(s.ticker)?'资格核心':d.relay_refs.includes(s.ticker)?'接力参照':'容量参照',amount(s.avg7_cny),pct(s.return_pct),`<details><summary>逐日证据</summary>${stockHistory(s)}</details>`]))}<p class="decision-muted">${e(d.reference_rule)}</p>
      <h3>${d.mode==='A'?'MA5主备候选':'策略结论'}</h3><p>${d.mode==='B'?'当前方向服务抱团／接力观察；本版未启用对应策略，不为凑名单输出MA5候选。':d.tier==='backup'?'需先满足本方向升级条件，再讨论MA5参与；当前不作为新开仓主选。':'以下为盘后观察主备，竞价与盘中仍须逐层确认。'}</p>
      ${candidates.length?table(['候选','优先级','入选依据','正常预期与失效'],candidates.map(c=>[`${e(c.name)}<small>${e(c.ticker)}</small>`,e(c.priority),e(c.reason),`<p>${e(c.normal)}</p><details><summary>确认与取消条件</summary><p>${e(c.confirmation)}</p><p>${e(c.failure)}</p>${stockHistory(plan.stocks[c.ticker])}</details>`])):d.mode==='A'?'<p class="decision-muted">当前没有同时满足方向与个股条件的MA5候选，等待。</p>':''}</div>
      <details><summary>AI硬件重点监测</summary>${table(['方向','当前安排'],plan.hardware_watch.map(x=>[e(x.name),x.selected?'已进入主备方向':'继续监测，尚未进入主备方向']).concat(plan.hardware_missing.map(x=>[e(x),'独立成员数据待补'])))}<p>重点监测不等于主攻；盘中集中爆发需要当时的群体行情证据。</p></details>`;
  }

  function flowchart(plan) {
    const active=plan.route.mode, m=plan.market, g=plan.conclusion.groups;
    const a=m.turnover.market_amount_cny, idx=m.indices.find(r=>r.ticker===m.main_index), change=idx?.change_pct;
    const fromBool=v=>v===true?'met':v===false?'unmet':'pending';
    const aChecks=[['量能＞2.3万亿',fromBool(ok(a)?a>2.3e12:null),ok(a)?`当前${n(a/1e12,4)}万亿`:'量能缺数据'],
      ['指数较昨收上涨',fromBool(ok(change)?change>0:null),ok(change)?`${idx.name} ${change>0?'+':''}${n(change)}%`:'指数缺数据'],
      ['首板溢价与参与反证核验',fromBool(g.trend.price_support),g.trend.label]];
    const bSignal=g.relay.price_support===true||g.crowding.price_support===true?true:g.relay.price_support===false&&g.crowding.price_support===false?false:null;
    const aFailed=aChecks.some(r=>r[1]==='unmet');
    const aState=active==='A'?'current':aFailed?'unmet':'pending', bState=active==='B'?'current':bSignal===false?'unmet':'standby';
    const badge={current:'当前观察 · 仍待确认',unmet:'当前未满足',pending:'证据待补',standby:'条件备查',future:'待次日验证'};
    const checks=rs=>`<ul class="decision-flow-checks">${rs.map(([label,s,value])=>`<li class="is-${s}" data-condition-state="${s}"><span class="decision-condition-symbol" aria-hidden="true">${s==='met'?'✓':s==='unmet'?'×':'○'}</span><span><b>${e(label)}</b><small>${s==='met'?'已满足':s==='unmet'?'未满足':'待确认'} · ${e(value)}</small></span></li>`).join('')}</ul>`;
    const node=(title,body,s='',extra='')=>`<div class="decision-flow-node${s?' is-'+s:''}"${s==='current'?' aria-current="step"':''}>${s?`<span class="decision-flow-state">${badge[s]}</span>`:''}<strong>${e(title)}</strong>${body?`<p>${e(body)}</p>`:''}${extra}</div>`;
    const arrow='<div class="decision-flow-arrow" aria-hidden="true">↓</div>';
    const first=plan.next_day_first_boards, firstCount=first?`${first.formation_date}首板，${plan.band_names[first.band]||'全部已知成交额档'}共${first.cohort_n}只`:'昨日首板名单待核验';
    return `<figure class="decision-flow" aria-label="次日决策流程图"><figcaption>当前路径：${e(plan.route.label)}。蓝色突出当前观察；灰色标出未满足条件，○表示待确认。以下盘后事实用于准备分支，次日须重新核验。</figcaption>
      ${node('指数结构＋量能＋各组反馈','先判断环境与资金去向，再选方向和个股；不按跨组累计收益排名。')}${arrow}
      <div class="decision-flow-branches decision-flow-fork"><div class="decision-flow-branch" data-branch="A">${node('A · 容量首板套利','',aState,checks(aChecks))}${arrow}${node('强方向 → 容量核心 → MA5','先看昨日首板溢价是否得到兑现，再核验板块与容量核心。量能达高档可纳入100亿以上。',aState==='unmet'?'unmet':'future')}</div>
      <div class="decision-flow-branch" data-branch="B">${node('B · 情绪抱团／接力','',bState,checks([
        ['转查情绪分支',fromBool(active==='B'?true:active==='A'?false:null),active==='B'?'A条件不足，逐项核查B':'当前不以B为主路径'],
        ['抱团或接力有正反馈',fromBool(bSignal),'分别看各组，不凭收益高低二选一'],
        ['核心承接与真实换手','pending','价格反馈不能代替参与条件']]))}${arrow}${node('辨识度核心 → 承接生态','2.1—2.3万亿：7日均额＜100亿；不足2.1万亿：＜50亿。连板看晋级与断板负反馈，抱团看分歧承接。本版仅观察。',bState==='unmet'?'unmet':'future')}</div>
      <div class="decision-flow-branch" data-branch="wait">${node('等待','两类情绪反馈都弱、关键证据不足，或没有可参与机会。',!active?'current':'standby')}${arrow}${node('保留缺口与重启条件','旧核心弱不自动换新核心；没有适用策略或合格个股，继续等待。','future')}</div></div>
      <p class="decision-muted decision-flow-policy">2.1／2.3万亿与50／100亿为现行预案参数，不是蟹老板的固定阈值。资金去向与参与条件仍需逐项核验。</p>
      ${arrow}${node('次日检验：大盘＋昨日首板反馈 → 板块 → 个股','9:25初筛，开盘后继续验证；大盘仍是上游，检查时点不等于自动确认。','future',`<div class="decision-next-check" data-check="first-board"><b>昨日首板的次日正反馈</b><p>${e(firstCount)}，名单已冻结；次日反馈待数据。</p><ul><li>竞价／开盘：对昨收的溢价中位数、正溢价占比，同时看低开与一字跌停等负反馈。</li><li>开盘后：溢价能否保持、涨后回落是否扩散、真实换手与量能是否支持；盘中红K相对当日开盘另看。</li><li>广泛负反馈：下调首板套利预期；抱团和接力各自重新核验，不自动递补。数据缺失保持待确认。</li></ul></div><p>继续核对板块支持、核心承接、个股主动性。预计全天量能不等于已确认成交额。</p>`)}
      ${arrow}<div class="decision-flow-branches decision-flow-outcomes">${node('主选符合预期','上游与所属模式反馈仍成立 → 保留主选，等待承接确认','future')}${node('主选弱、备选强','上游仍成立且备选满足原条件 → 提升观察优先级；净流入单项领先不够','future')}${node('上游失效／都不合格','暂停该方向；不自动递补，回到环境判断','future')}</div>
      <div class="decision-flow-holding"><b>已有持仓：</b>默认次日套利。超预期且原逻辑成立可留；符合预期仅开放已约定例外；不及预期或上游失效则退出预案。</div></figure>`;
  }

  function renderPlan(plan) {
    const market=plan.market, amountCny=market.turnover.market_amount_cny;
    return `<div class="decision-summary"><h2>${e(plan.conclusion.headline)}</h2><p>${e(plan.analysis_date)} 盘后 → ${e(plan.next_date||'下一交易日待核验')}预案 · 测试版仅启用MA5</p><p>${e(market.outlook.title)}。市场成交额 <strong>${n(amountCny/1e12,4)}万亿</strong>，较前日 ${pct(market.turnover_change_pct)}。</p><p>${e(plan.route.label)}；${plan.direction_scope.main_n}个主观察、${plan.direction_scope.backup_n}个条件备选。</p></div>
      ${chain(plan)}${section('大盘先定环境',indexTable(plan))}${section('引用模式收益评估',assessmentSummary(plan))}
      ${section('方向、核心与候选',directions(plan))}${section('次日如何分支',flowchart(plan))}
      ${section('来源与未完成项',`<p>${e(plan.provenance.note)}</p><p>重建时间：${e(plan.built_at)}；复盘缓存生成：${e(plan.provenance.review_generated_at||'缺失')}；成分采集：${e(plan.provenance.taxonomy_fetched_at||'缺失')}。</p><p>${plan.supersedes?'本次为口径升级后的重建；旧版预案已保留，不能将本次结果当作当时已知名单。':''}</p><p>待补齐：竞价价格与正常区间、全市场同时间量能、盘中集中爆发识别。当前不会自动发出委托。</p><a href="/api/v1/trade-decision/plan?date=${e(plan.analysis_date)}" target="_blank" rel="noopener">查看预案计算结果</a>`)}`;
  }

  function renderLive(data) {
    return `<div class="decision-summary"><h2>${e(data.date)} · ${e(data.as_of)} 缓存跟踪</h2><p>盘后依据 ${e(data.baseline_date)}，冻结于 ${e(data.baseline_built_at)}。</p><p>${e(data.route.label)}。${e(data.market_turnover.reason)}</p></div>
      ${section('C07 · 哪一层发生变化',table(['层级','当前证据','预案调整'],(data.layer_changes||[]).map(r=>[`${e(r.layer)}<small>${e({changed:'出现反证',observed:'已观察',missing:'证据不足'}[r.state])}</small>`,e(r.evidence),e(r.action)])))}
      ${section('量能时点',table(['昨日确认全天成交额','今日同时间成交额','预计全天成交额'],[[amount(data.market_turnover.previous_confirmed_cny),amount(data.market_turnover.current_same_time_cny),amount(data.market_turnover.projected_full_day_cny)]]))}
      ${section('指数分时',table(['指数','时点','较昨收','数据状态'],data.market.map(r=>[e(r.name),e(r.last_time||'—'),pct(r.return_pct),e(statusLabel[r.status]||r.status)])))}
      ${section('竞价',`<p>${e(data.auction.note)}</p><p>数据状态：${data.auction.status==='amount_only'?'已有竞价成交额':'暂无竞价数据'}；来源 ${e(data.auction.source||'—')}。</p>`)}
      ${section('预案观察对象对照',table(['观察对象','最后时点','较昨收','相对完整方向','MA5上方','VWAP上方 / 3根','承接细节'],(data.watchlist||data.candidates).map(r=>[
        `${e(r.name)}<small>${e(r.ticker)} · ${e(r.role||'MA5条件候选')}</small>`,e(r.last_time||'—'),pct(r.return_pct),`${pct(r.relative_direction_pct)}<small>${e(r.relative_direction_name||'')}</small>`,r.above_ma5===null||r.above_ma5===undefined?'—':r.above_ma5?'是':'否',r.vwap_above_count??'—',`<details><summary>${e(statusLabel[r.status]||r.status)}</summary><p>${e(r.vwap_note||r.reason||'')}</p><p>MA5收回：${r.ma5_repair_bars??'—'}根完整线；回落/前段上攻均量：${n(r.pullback_to_advance_volume_ratio)}；再上攻/回落均量：${n(r.readvance_to_pullback_volume_ratio)}。</p></details>`])))}
      ${section('方向覆盖',table(['方向','同一时点','可用 / 成员','收益中位数','范围'],data.directions.filter(d=>d.valid_n>0).sort((a,b)=>b.valid_n-a.valid_n).slice(0,30).map(d=>[e(d.name),e(d.time),`${d.valid_n}/${d.member_n}`,pct(d.return_median_pct),d.complete?'完整成员':'部分样本'])))}
      ${section('本次未确认项',`<p>${e(data.new_direction_detection.reason)}</p>${data.notes.map(x=>`<p class="decision-muted">${e(x)}</p>`).join('')}`)}`;
  }

  async function api(path,options) {
    try {
      const response=await fetch(`/api/v1/trade-decision/${path}`,{...options,signal:AbortSignal.timeout(30000)});
      const payload=await response.json();
      if(!response.ok) throw new Error(typeof payload.detail==='string'?payload.detail:`请求失败 (${response.status})`);
      return payload;
    }catch(error){if(error.name==='TimeoutError')throw new Error('状态读取超时，请稍后重新读取；后台任务可能仍在运行。');throw error;}
  }
  const message = (id,text) => {const node=byId(id);if(node) node.textContent=text;};
  const progressText = result => `${result.message||'后台正在生成'}${Number.isFinite(result.elapsed_seconds)?` · 已用时 ${result.elapsed_seconds} 秒`:''}。完成后将自动显示，可离开页面稍后读取。`;
  async function loadResearch(build=false, latest=false) {
    hideHelp();
    const day=byId('decisionResearchDate').value, seq=++state.researchSeq;
    clearTimeout(state.researchPoll);
    let pending=false;
    if(latest||build)state.researchRef='';
    const ref=state.researchRef;
    state.research=null;
    byId('decisionResearchResult').innerHTML='';
    if(!day){message('decisionResearchStatus','请选择盘后评估日期。');return;}
    message('decisionResearchStatus',build?'正在按已收盘日期生成模式评估…':'正在读取模式收益评估…');
    byId('decisionResearchResult').setAttribute('aria-busy','true');
    try {
      const result=await api(`mode-research${build?'/build':''}?date=${encodeURIComponent(day)}${ref?'&snapshot_id='+encodeURIComponent(ref):''}`,build?{method:'POST'}:undefined);
      if(seq!==state.researchSeq||byId('decisionResearchDate').value!==day)return;
      if(result.status==='running'){pending=true;message('decisionResearchStatus',progressText(result));state.researchPoll=setTimeout(()=>loadResearch(),5000);return;}
      if(build&&result.status==='ready'){await loadResearch();return;}
      if(result.status!=='ready'){message('decisionResearchStatus',result.message||'该日或所引用版本尚无评估。');byId('decisionResearchResult').innerHTML='<div class="decision-empty">所选日期或引用版本暂无评估，不会使用其他日期或最新版本替代。可生成本日评估。</div>';return;}
      const research=result.research;
      if(!['mode-research-v1','mode-research-v2','mode-research-v3','mode-research-v4','mode-research-v5'].includes(research.version)||research.analysis_date!==day||(ref&&research.snapshot_id!==ref))throw new Error('评估日期或版本不一致，请重新读取。');
      if(day!==state.researchDate){state.sectorCode='';state.sampleDate='';state.sectorPage=0;}
      state.researchDate=day;state.research=research;
      byId('decisionResearchResult').innerHTML=renderResearch(research);
      message('decisionResearchStatus',`${day} · ${ref?'预案引用版本':'最新评估'}已读取。研究筛选只影响下方统计，不改写评估结论。`);
    }catch(error){if(seq===state.researchSeq){message('decisionResearchStatus',error.message);byId('decisionResearchResult').innerHTML='<div class="decision-empty">模式评估读取失败，请重试。</div>';}}
    finally{if(seq===state.researchSeq)byId('decisionResearchResult').setAttribute('aria-busy',String(pending));}
  }
  async function loadPlan(build=false) {
    hideHelp();
    const day=byId('decisionPlanDate').value, seq=++state.seq;
    clearTimeout(state.poll);
    if(!day) {message('decisionPlanStatus','请选择盘后日期。');return;}
    message('decisionPlanStatus',build?'正在读取日期限定的数据并生成预案…':'正在读取预案…');
    byId('decisionPlanResult').setAttribute('aria-busy','true');
    if(day!==state.date) {byId('decisionPlanResult').innerHTML='';state.plan=null;state.direction='';}
    try {
      const result=await api(`${build?'build':'plan'}?date=${encodeURIComponent(day)}`,build?{method:'POST'}:undefined);
      if(seq!==state.seq||byId('decisionPlanDate').value!==day) return;
      if(result.status==='running') {message('decisionPlanStatus',progressText(result));state.poll=setTimeout(()=>loadPlan(),5000);return;}
      if(build&&result.status==='ready') {await loadPlan();return;}
      if(result.status!=='ready') {message('decisionPlanStatus',result.message||'该日尚无预案，可点击“生成并冻结预案”。');byId('decisionPlanResult').innerHTML='<div class="decision-empty">所选日期尚无可展示预案；不会用其他日期替代。</div>';return;}
      if(result.plan.version!=='decision-pilot-v4'){message('decisionPlanStatus','该日为旧版口径，点击生成预案可升级；旧版会保留。');byId('decisionPlanResult').innerHTML='<div class="decision-empty">新版将引用独立的模式收益评估。</div>';return;}
      state.date=day;state.plan=result.plan;
      if(!state.direction) state.direction=result.plan.directions[0]?.code||'';
      byId('decisionPlanResult').innerHTML=renderPlan(result.plan);
      message('decisionPlanStatus',`${day}盘后依据已读取；七步状态见下方。`);
    } catch(error) {if(seq===state.seq){message('decisionPlanStatus',error.message);byId('decisionPlanResult').innerHTML='<div class="decision-empty">读取失败，请重试。</div>';}}
    finally {if(seq===state.seq)byId('decisionPlanResult').setAttribute('aria-busy','false');}
  }
  async function loadLive() {
    const day=byId('decisionLiveDate').value, at=byId('decisionLiveTime').value, seq=++state.liveSeq;
    if(!day) {message('decisionLiveStatus','请选择盘后依据日。');return;}
    message('decisionLiveStatus','正在按预案日期读取分钟缓存…');byId('decisionLiveResult').setAttribute('aria-busy','true');
    try {
      const result=await api(`tracking?date=${encodeURIComponent(day)}${at?'&at='+encodeURIComponent(at):''}`);
      if(seq!==state.liveSeq||byId('decisionLiveDate').value!==day||byId('decisionLiveTime').value!==at)return;
      byId('decisionLiveResult').innerHTML=renderLive(result);message('decisionLiveStatus',`${result.date} ${result.as_of} · 已读取现有缓存；缺失条件保持待确认。`);
    } catch(error) {if(seq===state.liveSeq){message('decisionLiveStatus',error.message);byId('decisionLiveResult').innerHTML='<div class="decision-empty">当前跟踪不可用。请检查盘后依据日及下一交易日数据。</div>';}}
    finally {if(seq===state.liveSeq)byId('decisionLiveResult').setAttribute('aria-busy','false');}
  }
  async function enter(tab) {
    hideHelp();
    if(!state.initialized) {
      state.initialized=true;
      try {const results=await Promise.allSettled([api('dates'),api('mode-research/dates')]);const dates=results.map(r=>r.status==='fulfilled'?r.value.dates:[]);const day=dates[0][0]||dates[1][0]||byId('globalDate')?.value||'';for(const id of ['decisionPlanDate','decisionLiveDate'])if(!byId(id).value)byId(id).value=day;if(!byId('decisionResearchDate').value)byId('decisionResearchDate').value=dates[1][0]||day;}
      catch {state.initialized=false;message('decisionPlanStatus','日期列表读取失败，可手工选择日期重试。');}
    }
    if(tab==='decision-research')loadResearch();else if(tab==='decision-plan')loadPlan();else loadLive();
  }
  document.addEventListener('click',event=>{
    const modeButton=event.target.closest('[data-decision-mode]');
    if(modeButton&&state.research){
      hideHelp(); 
      state.mode=modeButton.dataset.decisionMode; state.sampleDate='';
      const y=window.scrollY; byId('decisionResearchResult').innerHTML=renderResearch(state.research);
      byId('decisionMode-'+state.mode)?.focus({preventScroll:true}); window.scrollTo(0,y);
    }
    const sectorButton=event.target.closest('[data-decision-sector]'), pageButton=event.target.closest('[data-sector-page]');
    if((sectorButton||pageButton)&&state.research){
      hideHelp();const y=window.scrollY;
      if(sectorButton)state.sectorCode=sectorButton.dataset.decisionSector;
      if(pageButton)state.sectorPage+=Number(pageButton.dataset.sectorPage);
      byId('decisionResearchResult').innerHTML=renderResearch(state.research);
      const selector=sectorButton?`[data-decision-sector="${CSS.escape(state.sectorCode)}"]`:`[data-sector-page="${pageButton.dataset.sectorPage}"]`;
      document.querySelector(selector)?.focus({preventScroll:true});window.scrollTo(0,y);
    }
    const direction=event.target.closest('[data-decision-direction]');
    if(direction&&state.plan){state.direction=direction.dataset.decisionDirection;const y=window.scrollY;byId('decisionPlanResult').innerHTML=renderPlan(state.plan);document.querySelector(`[data-decision-direction="${CSS.escape(state.direction)}"]`)?.focus({preventScroll:true});window.scrollTo(0,y);}
    const action=event.target.closest('[data-decision-action]')?.dataset.decisionAction;
    if(action==='load-plan')loadPlan();if(action==='build')loadPlan(true);if(action==='load-live')loadLive();
    if(action==='load-research')loadResearch(false,true);if(action==='build-research')loadResearch(true);
  });
  document.addEventListener('change',event=>{
    const id=event.target.id;
    if(id==='decisionPlanDate')loadPlan();
    if(id==='decisionResearchDate')loadResearch(false,true);
    if(id==='decisionLiveDate'||id==='decisionLiveTime'){state.liveSeq++;byId('decisionLiveResult').innerHTML='';loadLive();}
    const researchFields={decisionEvalWindow:'evalWindow',decisionSampleDate:'sampleDate',decisionSectorKind:'sectorKind',decisionSectorScope:'sectorScope',decisionSectorMinimum:'sectorMinimum',decisionSectorSort:'sectorSort',decisionSectorSearch:'sectorSearch'};
    if(researchFields[id]&&state.research){hideHelp();state[researchFields[id]]=event.target.value;state.sectorPage=0;if(id==='decisionSectorKind'||id==='decisionSectorSearch')state.sectorCode='';byId('decisionResearchResult').innerHTML=renderResearch(state.research);byId(id)?.focus({preventScroll:true});}

  });
  document.addEventListener('keydown',ev=>{
    const b=ev.target.closest('[data-decision-mode]');
    if(!b||!['ArrowLeft','ArrowRight','Home','End'].includes(ev.key))return;
    ev.preventDefault();const tabs=[...document.querySelectorAll('[data-decision-mode]')], i=tabs.indexOf(b);
    tabs[ev.key==='Home'?0:ev.key==='End'?tabs.length-1:(i+(ev.key==='ArrowRight'?1:-1)+tabs.length)%tabs.length].click();
  });
  document.addEventListener('DOMContentLoaded',()=>{
    setInterval(()=>{if(byId('decisionAutoRefresh')?.checked&&!document.hidden&&!byId('tab-decision-live')?.hidden)loadLive();},30000);
    const query=new URLSearchParams(location.search);
    if(['research','plan','live'].includes(query.get('decision'))){const day=query.get('date');if(/^\d{4}-\d{2}-\d{2}$/.test(day||'')){for(const id of ['decisionResearchDate','decisionPlanDate','decisionLiveDate'])byId(id).value=day;if(query.get('decision')==='research'&&query.has('snapshot')){state.researchRef=query.get('snapshot');}}window.switchTab?.(`decision-${query.get('decision')}`);}
  });
  let helpAnchor=null, helpTimer=null, helpPress=null;
  const helpNode=()=>{let t=byId('decisionTooltip');if(!t){t=document.createElement('div');t.id='decisionTooltip';t.className='decision-tooltip';t.setAttribute('popover','manual');t.setAttribute('role','tooltip');document.body.append(t);t.addEventListener('pointerenter',()=>clearTimeout(helpTimer));t.addEventListener('pointerleave',()=>hideHelp());}return t;};
  function hideHelp(){clearTimeout(helpTimer);const t=byId('decisionTooltip');if(t?.matches(':popover-open'))t.hidePopover();helpAnchor?.removeAttribute('aria-describedby');helpAnchor?.removeAttribute('aria-controls');helpAnchor?.setAttribute('aria-expanded','false');helpAnchor=null;}
  function showHelp(button){if(button.disabled)return;clearTimeout(helpTimer);hideHelp();helpAnchor=button;const t=helpNode();t.textContent=button.dataset.decisionHelp;button.setAttribute('aria-describedby',t.id);button.setAttribute('aria-controls',t.id);button.setAttribute('aria-expanded','true');t.showPopover();const r=button.getBoundingClientRect(),box=t.getBoundingClientRect();t.style.left=Math.max(8,Math.min(r.left,innerWidth-box.width-8))+'px';t.style.top=Math.max(8,r.bottom+8+box.height<=innerHeight?r.bottom+8:r.top-box.height-8)+'px';}
  document.addEventListener('pointerover',ev=>{const b=ev.target.closest('[data-decision-help]');if(b&&!b.contains(ev.relatedTarget)&&ev.pointerType!=='touch'){clearTimeout(helpTimer);helpTimer=setTimeout(()=>showHelp(b),800);}});
  document.addEventListener('pointerout',ev=>{const b=ev.target.closest('[data-decision-help]');if(b&&!b.contains(ev.relatedTarget))helpTimer=setTimeout(hideHelp,150);});
  document.addEventListener('focusin',ev=>{if(ev.target.matches('[data-decision-help]'))showHelp(ev.target);});
  document.addEventListener('focusout',ev=>{if(ev.target.matches('[data-decision-help]'))hideHelp();});
  document.addEventListener('pointerdown',ev=>{const b=ev.target.closest('[data-decision-help]');helpPress=b?{button:b,open:helpAnchor===b}:null;});
  document.addEventListener('click',ev=>{const b=ev.target.closest('[data-decision-help]');if(b){const wasOpen=ev.detail===0?helpAnchor===b:helpPress?.button===b&&helpPress.open;if(wasOpen)hideHelp();else showHelp(b);}else if(!ev.target.closest('#decisionTooltip'))hideHelp();helpPress=null;});
  document.addEventListener('keydown',ev=>{if(ev.key==='Escape')hideHelp();});
  window.addEventListener('resize',hideHelp);
  window.TradeDecision={enter,renderPlan,renderResearch,renderLive,loadPlan,loadResearch,loadLive,state};
})();
