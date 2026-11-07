/* Hallmark · pre-emit critique: P5 H5 E4 S5 R5 V4
 * Reference terminal layout; existing workbench colors and data contracts. */
(() => {
  'use strict';
  const $=id=>document.getElementById(id),root=()=>$('firstboardStrength');
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const valid=v=>typeof v==='number'&&Number.isFinite(v);
  const pct=v=>valid(v)?(v>0?'+':'')+(v*100).toFixed(2)+'%':'—';
  const money=v=>valid(v)?Math.abs(v)>=1e8?(v/1e8).toFixed(2)+'亿':(v/1e4).toFixed(2)+'万':'—';
  const number=v=>valid(v)?v.toLocaleString('zh-CN',{maximumFractionDigits:2}):'—';
  const tone=v=>valid(v)?v>0?'sr-up':v<0?'sr-down':'':'';
  const cell=(value,cls='',title='')=>({value,cls,title});
  const notice=text=>'<p class="sr-notice" role="status">'+esc(text)+'</p>';
  const head=(title,tools='')=>'<header class="sr-section-head"><h3>'+title+'</h3>'+tools+'</header>';
  const table=(heads,rows,cls,widths,id='')=>'<div class="sr-scroll" '+(id?'id="'+id+'"':'')+' tabindex="0" role="region" aria-label="'+(cls==='sr-rank'?'区间涨幅名单':cls==='sr-history'?'个股逐日记录':'龙虎榜席位')+'，可滚动"><table class="sr-table '+cls+'"><colgroup>'+widths.map(w=>'<col style="width:'+w+'%">').join('')+'</colgroup><thead><tr>'+heads.map(h=>'<th scope="col">'+h+'</th>').join('')+'</tr></thead><tbody>'+rows.map(row=>'<tr class="'+(row.selected?'sr-selected-row':'')+'" '+(row.key?'data-sr-'+row.kind+'="'+esc(row.key)+'"':'')+'>'+row.cells.map(c=>'<td class="'+c.cls+'" '+(c.title?'title="'+esc(c.title)+'"':'')+'>'+c.value+'</td>').join('')+'</tr>').join('')+'</tbody></table></div>';
  function keepScroll(id,render){
    const el=$(id),top=el?.scrollTop||0,left=el?.scrollLeft||0,focused=document.activeElement;
    const attr=['data-sr-stock','data-sr-day'].find(a=>el?.contains?.(focused)&&focused?.hasAttribute?.(a)),key=attr?focused.getAttribute(attr):null;
    render();if($(id)){$(id).scrollTop=top;$(id).scrollLeft=left;}
    if(attr)[...($(id)?.querySelectorAll?.('button['+attr+']')||[])].find(b=>b.getAttribute(attr)===key)?.focus({preventScroll:true});
  }
  function revealSelection(id,selector,header=0){
    const list=$(id),row=list?.querySelector?.(selector);if(!row)return;
    const box=list.getBoundingClientRect(),item=row.getBoundingClientRect();
    if(item.top<box.top+header)list.scrollTop+=item.top-box.top-header;
    else if(item.bottom>box.bottom)list.scrollTop+=item.bottom-box.bottom;
  }
  let data,detail,selected,day,minute,mode='minute',market='ALL',seq=0,detailSeq=0,chartSeq=0,requestController;
  let start='',limit=50,windowDays=20,resizeObserver,active=false;
  const stockButton=r=>'<button type="button" class="sr-stock" data-sr-stock="'+esc(r.ticker)+'" aria-pressed="'+(selected===r.ticker)+'" title="'+esc(r.name)+' · '+esc(r.ticker)+'">'+esc(r.name)+'</button>';
  async function api(path,options={}){
    const response=await fetch(path,options),body=await response.json();
    if(!response.ok)throw new Error(typeof body.detail==='string'?body.detail:'数据读取失败，请重试。');
    return body;
  }
  function fitViewport(){
    if(!active||!root()?.getBoundingClientRect)return;
    const top=root().getBoundingClientRect().top+window.scrollY;
    root().style.setProperty('--sr-height',Math.max(380,window.innerHeight-top-8)+'px');
  }
  function shell(){
    active=true;resizeObserver?.disconnect();
    root().innerHTML='<section class="sr-workspace" aria-label="强势股复盘工作台">'+
      '<form id="srRange" class="sr-controls"><strong>强势股复盘</strong><label>区间<input id="srStart" aria-label="区间开始日期" type="date" value="'+esc(start)+'"></label><span>至</span><input id="srEnd" aria-label="区间结束日期" type="date" value="'+esc($('firstboardDate').value)+'">'+
      '<select id="srWindow" aria-label="快捷区间"><option value="custom" '+(start?'selected':'')+'>自选区间</option>'+[5,10,20,60,120].map(n=>'<option value="'+n+'" '+(n===windowDays&&!start?'selected':'')+'>近'+n+'个交易日</option>').join('')+'</select>'+
      '<select id="srLimit" aria-label="显示数量">'+[20,50,100,200].map(n=>'<option value="'+n+'" '+(n===limit?'selected':'')+'>前'+n+'只</option>').join('')+'</select><button type="submit">查询</button><button type="button" id="srExpand">展开工作台</button>'+
      '<details class="sr-help"><summary>说明</summary><p>按区间涨幅排名；核心单独认证，须本段三连板且10个交易日内至少5板。默认近20个交易日前50只。点击股票联动逐日记录、龙虎榜和行情，双击图表进入个股研究。各分区独立滚动，长文本悬停查看。'+esc(data?.scope||'')+'</p></details></form>'+
      '<div class="sr-grid"><section id="srRanking" class="sr-panel" aria-label="区间涨幅排名">'+head('区间涨幅榜')+notice('正在读取区间涨幅…')+'</section><section id="srHistory" class="sr-panel" aria-label="个股逐日记录">'+head('逐日复盘')+notice('选择股票查看逐日记录')+'</section><section id="srLhb" class="sr-panel" aria-label="龙虎榜">'+head('龙虎榜')+notice('选择股票查看龙虎榜')+'</section><section id="srChart" class="sr-panel" aria-label="行情图">'+head('分时 / 日K')+notice('选择股票查看行情')+'</section></div>'+
      '<footer id="srMessage" class="sr-status" role="status">正在读取区间涨幅…</footer></section><dialog id="srExpanded" aria-label="强势股复盘完整工作台"></dialog>';
    $('srRange').addEventListener('submit',e=>{e.preventDefault();start=$('srStart').value;limit=Number($('srLimit').value);$('firstboardDate').value=$('srEnd').value;load();});
    $('srWindow').addEventListener('change',e=>{if(e.target.value==='custom'){start=$('srStart').value;return;}windowDays=Number(e.target.value);start='';$('firstboardDate').value=$('srEnd').value;load();});
    $('srStart').addEventListener('input',()=>{$('srWindow').value='custom';});
    $('srExpand').addEventListener('click',()=>{
      const dialog=$('srExpanded');
      if(dialog.open){dialog.close();return;}
      dialog.append(root().querySelector('.sr-workspace'));dialog.showModal();$('srExpand').textContent='返回页面';drawChart();
    });
    const dialog=$('srExpanded');
    dialog.addEventListener('close',()=>{
      if(dialog!==$('srExpanded'))return;
      const workspace=dialog.querySelector('.sr-workspace');
      if(workspace){root().prepend(workspace);$('srExpand').textContent='展开工作台';fitViewport();drawChart();if(active)$('srExpand').focus();}
    });
    if(window.ResizeObserver){
      resizeObserver=new ResizeObserver(()=>{fitViewport();drawChart();});
      resizeObserver.observe(root());resizeObserver.observe($('srChart'));
    }
    window.requestAnimationFrame?.(fitViewport);
  }
  async function load(options={}){
    requestController?.abort();requestController=new AbortController();const request=++seq;
    detailSeq++;chartSeq++;detail=null;selected=null;minute=null;
    const expanded=$('srExpanded')?.open||(!active&&window.innerWidth>=1024);if($('srExpanded')?.open)$('srExpanded').close();
    if(options.latest)$('firstboardDate').value='';
    const params=new URLSearchParams({window:windowDays,limit});
    if($('firstboardDate').value)params.set('date',$('firstboardDate').value);
    if(start)params.set('start',start);
    shell();if(expanded)$('srExpand').click();$('firstboardRefresh').disabled=true;
    try{
      const next=await api('/api/v1/review/strength?'+params,{signal:requestController.signal});
      if(request!==seq)return;
      data=next;$('firstboardDate').value=data.date;
      $('firstboardNext').disabled=!data.next_date||data.next_date>new Date().toLocaleDateString('sv-SE',{timeZone:'Asia/Shanghai'});
      $('srStart').value=data.start;$('srEnd').value=data.date;
      $('srMessage').textContent=data.start+' 至 '+data.date+' · '+data.days.length+'个交易日 · 按区间涨幅展示'+data.rows.length+'只。'+data.scope;
      $('srMessage').title=$('srMessage').textContent;
      $('firstboardStatus').textContent='区间涨幅榜读取完成';renderRank();fitViewport();
      if(data.rows.length)void selectStock(data.rows[0].ticker);
      else $('srHistory').innerHTML=head('逐日复盘')+notice('所选区间暂无可排名股票，请调整区间。');
    }catch(error){
      if(request===seq){$('srMessage').textContent=error.name==='AbortError'?'读取已取消':error.message;$('firstboardStatus').textContent='涨幅榜读取未完成';$('srRanking').innerHTML=head('区间涨幅榜')+notice('涨幅榜读取失败，可点击查询重试。');}
    }finally{if(request===seq)$('firstboardRefresh').disabled=false;}
  }
  function renderRank(){
    const hints=data.rows.filter(r=>r.core_status==='qualified').slice(0,6);
    keepScroll('srRankScroll',()=>{
      $('srRanking').innerHTML='<div class="sr-hints"><span class="sr-strip-label">核心提示</span><div>'+(
        hints.map(r=>'<div>'+stockButton(r)+'<span class="'+tone(r.daily_return)+'">'+pct(r.daily_return)+'</span><span title="'+esc(r.core_qualified_at)+'取得五板资格">五板已认证</span></div>').join('')||'<span>当前名单暂无已认证核心</span>'
      )+'</div></div>'+head('区间涨幅榜','<span>'+data.rows.length+'只 · 区间涨幅降序</span>')+
      table(['序','代码','名称','涨幅%','现价','开始日期','结束日期','交易日','区间涨幅%','所属行业','核心'],data.rows.map(r=>({key:r.ticker,kind:'stock',selected:selected===r.ticker,cells:[
        cell(r.rank,'sr-num'),cell(esc(r.ticker.split('.')[0]),'sr-code',r.ticker),cell(stockButton(r),'sr-name'),
        cell(pct(r.daily_return),'sr-num '+tone(r.daily_return)),cell(number(r.close),'sr-num'),
        cell(esc(r.start?.replaceAll('-','')),'sr-date-cell',r.start),cell(esc(r.end?.replaceAll('-','')),'sr-date-cell',r.end),cell((r.sessions??'—')+'日','sr-num'),
        cell(pct(r.return),'sr-num sr-return '+tone(r.return)),cell(esc(r.industry||'—'),'sr-text',r.industry||'行业缺归档'),
        cell(r.core_status==='qualified'?'已认证':'—','sr-core',r.core_status==='qualified'?r.core_qualified_at+'取得五板资格':'尚无当前五板资格')
      ]})),'sr-rank',[3.5,7.5,10,8.5,6,10,10,6,10,10.5,8],'srRankScroll');
    });
  }
  async function selectStock(ticker){
    const request=++detailSeq;chartSeq++;selected=ticker;detail=null;minute=null;renderRank();
    $('srHistory').innerHTML=head('逐日复盘')+notice('正在读取该股逐日记录…');
    $('srLhb').innerHTML=head('龙虎榜')+notice('正在读取上榜记录…');
    $('srChart').innerHTML=head('分时 / 日K')+notice('正在读取行情…');
    try{
      const result=await api('/api/v1/review/strength/detail?'+new URLSearchParams({ticker,date:data.date,start:data.start}));
      if(request!==detailSeq)return;
      detail=result;day=data.date;await selectDay(day);
    }catch(error){if(request===detailSeq)$('srHistory').innerHTML=head('逐日复盘')+'<p class="sr-notice" role="alert">'+esc(error.message)+'</p><button data-sr-stock="'+esc(ticker)+'">重试</button>';}
  }
  function renderHistory(){
    const labels={ALL:'沪深京',SH:'沪市',SZ:'深市',BJ:'北交所'};
    const sealedRows=detail.rows.filter(r=>r.state==='sealed');
    keepScroll('srHistoryScroll',()=>{
      $('srHistory').innerHTML=head(esc(detail.name)+' · 逐日复盘','<span>'+sealedRows.length+'个涨停日</span><select id="srMarket" aria-label="市场统计范围">'+Object.entries(labels).map(([v,n])=>'<option value="'+v+'" '+(v===market?'selected':'')+'>'+n+'</option>').join('')+'</select>')+
      table(['日期','个股涨幅','成交额','涨停原因','涨停','炸板','跌停','封板率','上证涨幅'],sealedRows.map(r=>{
        const m=r.market?.markets?.[market],coverage=m?'已覆盖'+m.covered+'/'+m.expected+(m.complete?'':' · 部分覆盖'):'缺少该日市场归档';
        return {key:r.date,kind:'day',selected:r.date===day,cells:[
          cell('<button class="sr-date '+(r.state==='sealed'?'sr-sealed':'')+'" data-sr-day="'+r.date+'" aria-pressed="'+(r.date===day)+'" title="'+(r.state==='sealed'?'收盘涨停':r.state==='missing'?'缺少涨停证据':'收盘未涨停')+'">'+r.date+'</button>'),
          cell(pct(r.daily_return),'sr-num '+tone(r.daily_return)),cell(money(r.amount),'sr-num'),
          cell(esc(r.reason||(r.state==='sealed'?'原因缺归档':'—')),'sr-text',r.reason||(r.state==='sealed'?'当日原因缺归档':'收盘未涨停')),
          ...['up','broken','down'].map(k=>cell(m?number(m[k]):'—','sr-num',coverage)),
          cell(m?pct(m.seal_rate).replace(/^\+/,'')+(m.complete?'':'*'):'—','sr-num',coverage),cell(pct(r.benchmark_return),'sr-num '+tone(r.benchmark_return))
        ]};
      }),'sr-history',[13,11,10,24,6,6,6,11,13],'srHistoryScroll')+(!sealedRows.length?notice('所选区间没有收盘涨停记录。'):'')+
      '<div class="sr-panel-note" title="市场统计剔除ST与退市股；炸板指日内触及涨停、收盘未封住。封板率=涨停÷（涨停+炸板）。'+esc(detail.reason_note)+'">'+labels[market]+' · 已剔除ST及退市 · * 部分行情覆盖，悬停数量查看覆盖率 · 仅显示收盘涨停日</div>';
    });
    $('srMarket').addEventListener('change',e=>{market=e.target.value;renderHistory();});
  }
  function renderLhb(){
    const events=detail.lhb.filter(e=>e.date===day),dates=[...new Set(detail.lhb.map(e=>e.date))],r=detail.rows.find(r=>r.date===day);
    keepScroll('srLhbDates',()=>keepScroll('srLhbBody',()=>{
      $('srLhb').innerHTML=head('龙虎榜','<button id="srSyncLhb" title="补齐区间龙虎榜与涨停原因">补齐数据</button>')+
      '<div class="sr-lhb-layout"><nav id="srLhbDates" class="sr-timeline" aria-label="区间上榜日期"><span>上榜历史</span>'+
      (dates.map(d=>'<button data-sr-day="'+d+'" aria-pressed="'+(d===day)+'" class="'+(d===day?'sr-selected':'')+'">'+d+'</button>').join('')||'<span>暂无归档</span>')+
      '</nav><div id="srLhbBody" class="sr-lhb-body" tabindex="0" role="region" aria-label="龙虎榜详情，可滚动"><div class="sr-lhb-stock"><strong>'+esc(detail.name)+'</strong><span>'+esc(day)+'</span>'+
      (r?'<span class="'+tone(r.daily_return)+'">'+number(r.close)+'　'+pct(r.daily_return)+'</span><span>成交额 '+money(r.amount)+'</span>':'')+'</div>'+
      (!events.length?notice(detail.lhb_missing_dates.includes(day)?'当日龙虎榜缺归档，可点击补齐数据。':'当日无上榜记录，可选择左侧上榜日期。'):'')+
      events.map(e=>'<article class="sr-event"><div class="sr-reason" title="'+esc(e.summary.reason)+'">'+esc(e.summary.reason)+'</div><div class="sr-lhb-totals"><span>买入 <b class="sr-up">'+money(e.summary.l_buy)+'</b></span><span>卖出 <b class="sr-down">'+money(e.summary.l_sell)+'</b></span><span>净额 <b class="'+tone(e.summary.net_amount)+'">'+money(e.summary.net_amount)+'</b></span></div>'+
        ['0','1'].map(side=>table([side==='0'?'买入前五营业部':'卖出前五营业部','买入','卖出','净额'],e.seats.filter(s=>String(s.side)===side).sort((a,b)=>(b[side==='0'?'buy':'sell']??-1)-(a[side==='0'?'buy':'sell']??-1)).map(s=>({cells:[
          cell(esc(s.exalter),'sr-text',s.exalter),cell(money(s.buy),'sr-num sr-up'),cell(money(s.sell),'sr-num sr-down'),cell(money(s.net_buy),'sr-num '+tone(s.net_buy))
        ]})),'sr-seats',[49,17,17,17])).join('')+(!e.seats.length?notice('当日席位明细缺失。'):'')+'</article>').join('')+
      '<p id="srLhbStatus" class="sr-inline-status" role="status">'+(detail.lhb_missing_dates.length?'区间还有'+detail.lhb_missing_dates.length+'日未归档':'')+'</p></div></div>'+
      '<div class="sr-panel-note" title="同日不同上榜理由分别展示，不相加重复统计；上榜理由与涨停原因分别保留。">交易所龙虎榜 · Tushare归档 · 同日多条理由分别列示</div>';
    }));
    $('srSyncLhb').addEventListener('click',async()=>{
      const request=detailSeq,button=$('srSyncLhb');button.disabled=true;$('srLhbStatus').textContent='正在补齐龙虎榜与涨停原因…';
      try{
        const result=await api('/api/v1/review/strength/evidence?'+new URLSearchParams({date:data.date,start:data.start}),{method:'POST'});
        if(request!==detailSeq)return;
        const failed=result.results.filter(r=>r.status==='unavailable').length;
        const nextDetail=await api('/api/v1/review/strength/detail?'+new URLSearchParams({ticker:selected,date:data.date,start:data.start}));
        if(request!==detailSeq)return;
        detail=nextDetail;renderHistory();renderLhb();$('srLhbStatus').textContent=failed?failed+'项数据暂未补齐，可稍后重试。':'龙虎榜与涨停原因已更新';
      }catch(error){if(request===detailSeq)$('srLhbStatus').textContent=error.message;}finally{button.disabled=false;}
    });
  }
  async function selectDay(value){
    if(!detail)return;
    const request=++chartSeq;day=value;minute=null;renderHistory();renderLhb();revealSelection('srHistoryScroll','.sr-selected-row',28);revealSelection('srLhbDates','[aria-pressed="true"]');renderChart();
    try{
      const next=await api('/api/v1/review/strength/minutes?'+new URLSearchParams({ticker:selected,date:day}));
      if(request!==chartSeq)return;minute=next;renderChart();
    }catch(error){if(request===chartSeq){minute={bars:[],reason:error.message};renderChart();}}
  }
  function plot(rows,candles,width=640,height=300){
    const validRows=rows.filter(r=>valid(r.close)&&(!candles||['open','low','high'].every(k=>valid(r[k]))));
    if(!validRows.length)return notice('所选图形缺少有效行情。');
    width=Math.max(280,width);height=Math.max(140,height);
    const left=48,right=12,top=12,bottom=height-22,priceBottom=Math.floor(height*.70),volumeTop=priceBottom+16;
    const lo=Math.min(...validRows.map(r=>candles?r.low:r.close)),hi=Math.max(...validRows.map(r=>candles?r.high:r.close)),range=hi-lo||1;
    const x=i=>left+i*(width-left-right)/Math.max(1,rows.length-1),y=v=>top+(hi-v)/range*(priceBottom-top);
    const vmax=Math.max(1,...rows.map(r=>valid(r.volume)?r.volume:0)),bw=Math.max(1,Math.min(6,(width-left-right)/rows.length*.6));
    const volumes=rows.map((r,i)=>valid(r.volume)?'<rect class="sr-volume" x="'+(x(i)-bw/2)+'" y="'+(bottom-r.volume/vmax*(bottom-volumeTop))+'" width="'+bw+'" height="'+(r.volume/vmax*(bottom-volumeTop))+'"/>':'').join('');
    const marks=candles?rows.map((r,i)=>valid(r.close)&&valid(r.open)&&valid(r.high)&&valid(r.low)?'<g class="'+(r.close>=r.open?'sr-candle-up':'sr-candle-down')+'"><title>'+esc(r.label)+' 开'+number(r.raw?.open)+' 高'+number(r.raw?.high)+' 低'+number(r.raw?.low)+' 收'+number(r.raw?.close)+'</title><line x1="'+x(i)+'" y1="'+y(r.high)+'" x2="'+x(i)+'" y2="'+y(r.low)+'"/><rect x="'+(x(i)-bw/2)+'" y="'+Math.min(y(r.open),y(r.close))+'" width="'+bw+'" height="'+Math.max(1,Math.abs(y(r.open)-y(r.close)))+'"/></g>':'').join(''):
      '<path class="sr-price-line" d="'+rows.map((r,i)=>valid(r.close)?(i&&valid(rows[i-1].close)?'L':'M')+x(i)+','+y(r.close):'').join(' ')+'"/>';
    const grid=[0,.25,.5,.75,1].map(f=>'<line class="sr-grid-line" x1="'+left+'" y1="'+(top+f*(priceBottom-top))+'" x2="'+(width-right)+'" y2="'+(top+f*(priceBottom-top))+'"/><text x="'+(left-6)+'" y="'+(top+f*(priceBottom-top)+4)+'" text-anchor="end">'+number(hi-f*(hi-lo))+'</text>').join('');
    return '<svg class="sr-plot" viewBox="0 0 '+width+' '+height+'" role="img" aria-label="'+(candles?'区间日K与成交量':'所选日分时与成交量')+'">'+grid+marks+volumes+'<text x="'+left+'" y="'+(height-5)+'">'+esc(rows[0]?.label)+'</text><text x="'+(width-right)+'" y="'+(height-5)+'" text-anchor="end">'+esc(rows.at(-1)?.label)+'</text></svg>';
  }
  function drawChart(){
    const el=$('srChartPlot');if(!active||!detail||!el)return;
    const rows=mode==='daily'?detail.rows.filter(r=>r.date<=day).map(r=>({...r.chart,label:r.date,volume:r.volume,raw:r})):(minute?.bars||[]).map(r=>({...r,label:String(r.datetime).slice(11,16)}));
    el.innerHTML=mode==='daily'||rows.length?plot(rows,mode==='daily',el.clientWidth||640,el.clientHeight||300):notice(minute?.reason||'正在读取所选日分时…');
  }
  function renderChart(){
    if(!detail)return;
    $('srChart').innerHTML=head('<span class="sr-chart-tabs"><button data-sr-mode="minute" aria-pressed="'+(mode==='minute')+'">分时</button><button data-sr-mode="daily" aria-pressed="'+(mode==='daily')+'">日K</button></span>',
      '<span class="sr-chart-name">'+esc(detail.name)+' · '+esc(day)+'</span>'+(mode==='minute'?'<button id="srSyncMinute" title="补齐所选日分时">补齐分时</button>':'')+'<button id="srAnalyze" title="进入个股研究；也可双击行情图">个股研究 ↗</button>')+
      '<div id="srChartPlot" tabindex="0" role="group" aria-label="行情图；双击或按回车进入个股研究"></div>'+
      '<div class="sr-panel-note" title="'+esc(mode==='daily'?detail.chart_note:'只展示所选交易日的已归档分时行情')+'">'+(mode==='daily'?'区间日K · 成交量':minute?.interval?minute.interval+'分钟采样 · '+esc(minute.source):'所选交易日分时')+'<span id="srChartStatus" role="status"></span></div>';
    drawChart();
    const jump=()=>{deactivate();window.syncMarketDates?.(day);window.jumpToAnalysis?.(selected);};
    $('srAnalyze').addEventListener('click',jump);$('srChartPlot').addEventListener('dblclick',jump);
    $('srChartPlot').addEventListener('keydown',e=>{if(e.key==='Enter')jump();});
    $('srSyncMinute')?.addEventListener('click',async()=>{
      const request=chartSeq,button=$('srSyncMinute');button.disabled=true;$('srChartStatus').textContent='正在补齐所选日分时…';
      try{const next=await api('/api/v1/review/strength/minutes?'+new URLSearchParams({ticker:selected,date:day}),{method:'POST'});if(request!==chartSeq)return;minute=next;renderChart();$('srChartStatus').textContent=next.bars?.length?'分时已更新':next.reason||'分时尚未补齐，可稍后重试。';}
      catch(error){if(request===chartSeq)$('srChartStatus').textContent=error.message;}finally{button.disabled=false;}
    });
  }
  document.addEventListener('click',e=>{
    const stock=e.target.closest('[data-sr-stock]'),date=e.target.closest('[data-sr-day]'),chart=e.target.closest('[data-sr-mode]');
    if(stock)void selectStock(stock.dataset.srStock);
    if(date)void selectDay(date.dataset.srDay);
    if(chart){mode=chart.dataset.srMode;renderChart();}
  });
  function deactivate(){
    active=false;resizeObserver?.disconnect();if($('srExpanded')?.open)$('srExpanded').close();
    requestController?.abort();seq++;detailSeq++;chartSeq++;$('firstboardRefresh').disabled=false;
  }
  function shiftDate(direction){const target=direction<0?data?.previous_date:data?.next_date;if(target){$('firstboardDate').value=target;load();}}
  window.addEventListener?.('resize',()=>{fitViewport();drawChart();});
  window.StrengthReview={load,deactivate,shiftDate};
})();
