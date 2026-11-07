/* The compact editor must preserve fields and structured rules while hiding technical inputs. */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source=fs.readFileSync('shilun/static/trade-journal.js','utf8');
const editor=source.slice(source.indexOf('  function editDecision('),source.indexOf('  function formValues('));
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const decode=v=>v.replace(/&quot;/g,'"').replace(/&#39;/g,"'").replace(/&lt;/g,'<').replace(/&gt;/g,'>').replace(/&amp;/g,'&');
function render(d={},fill=''){
  const root={innerHTML:'',querySelector(){return {focus(){}};}};
  const context={state:{definitions:[{id:'action.buy'},{id:'action.sell'}],definitionsError:false,detail:{decisions:d.decision_id?[d]:[],fills:[{fill_id:'actual-buy',side:'buy'},{fill_id:'actual-sell',side:'sell'}]}},
    array:v=>Array.isArray(v)?v:[],definition:id=>['action.buy','action.sell'].includes(id)?{id}:null,
    recordDefinitions:v=>v.definition_snapshots||[],esc,text:v=>typeof v==='object'?JSON.stringify(v):String(v??''),
    narrative:(v,fallback='待补')=>typeof v==='string'?v:v?.text||v?.raw||v?.description||fallback||'已保存结构化内容，展开原始字段查看',
    field:(title,value)=>`<div><dt>${esc(title)}</dt><dd>${esc(value)}</dd></div>`,
    options:(category,selected)=>`<option value="${esc(selected||'')}">${esc(selected||'保留原文')}</option>`,
    input:(name,title,value='')=>`<label>${esc(title)}<input name="${name}" value="${esc(value)}"></label>`,
    area:(name,title,value='')=>`<label>${esc(title)}<textarea name="${name}">${esc(value==null?'':typeof value==='object'?JSON.stringify(value,null,2):value)}</textarea></label>`,
    label:v=>v,$:()=>root,status(){throw Error('unexpected blocked editor')}};
  vm.createContext(context);vm.runInContext(editor,context);context.editDecision(d.decision_id||'',fill);return root.innerHTML;
}
function textarea(html,name){const match=html.match(new RegExp('<textarea name="'+name+'"[^>]*>([\\s\\S]*?)<\\/textarea>'));assert(match,'missing original field '+name);return decode(match[1]);}

test('collapsed technical fields preserve exact structured rule and immutable version references',()=>{
  const d={decision_id:'d-old',revision:3,action_id:'action.buy',action:'抱团买入',mode_id:'mode.user.old',mode:'用户原文',fill_ids:['one','two'],reason:'当时的理由',
    expectation:{metric:'close_return_pct',operator:'>=',target:2,baseline:'entry_price',target_date:'2026-10-26',text:'原始预期<&>'},
    invalidation:{text:'原始失效条件',rule:{price:12}},risk_constraints:{max_shares:1000,notes:'用户原始边界'},
    definition_snapshots:[{id:'mode.user.old',rule_version:'mode-card-v1'}],frozen_plan:{plan_id:'keep-plan',raw_reference:'v3/source-original'}};
  const original=JSON.stringify(d),html=render(d),main=html.split('<details class="tj-decision-more">')[0],advanced=html.slice(main.length);
  assert.equal(JSON.stringify(d),original);
  for(const key of ['expectation','invalidation','risk_constraints']){assert.deepEqual(JSON.parse(textarea(html,key)),d[key]);assert.equal((html.match(new RegExp('name="'+key+'"','g'))||[]).length,1);assert(!main.includes('name="'+key+'"'));assert(advanced.includes('name="'+key+'"'));}
  assert(main.includes('收盘涨幅 &gt;= 2%'));assert(!main.includes('&quot;metric&quot;'));
  assert(html.includes('mode-card-v1'));assert(html.includes('keep-plan'));assert(html.includes('v3/source-original'));assert(html.includes('data-revision="3"'));
  assert(!main.includes('name="revision_reason"'));assert(advanced.includes('变更说明（可选）'));
  assert(!html.includes('原始预期<&>'));
});
test('new fill defaults only the operation fact while leaving the rationale blank',()=>{
  for(const [fill,word,id] of [['actual-buy','买入','action.buy'],['actual-sell','卖出','action.sell']]){
    const html=render({},fill);assert.equal(textarea(html,'action'),word);assert(html.includes('value="'+id+'"'));assert.equal(textarea(html,'reason'),'');assert.equal(textarea(html,'expectation'),'');assert(html.includes('data-fills="[&quot;'+fill+'&quot;]"'));
  }
});
test('plain-language notes stay editable in the primary form; technical inputs occur only once',()=>{
  const html=render({decision_id:'d',revision:1,action:'买入',reason:'理由',expectation:'次日反弹',risk_constraints:'1000股上限',invalidation:'原信号失效退出',observed_at:'2026-10-23T09:36:00+08:00'}),main=html.split('<details class="tj-decision-more">')[0];
  for(const key of ['reason','expectation','risk_constraints','invalidation'])assert(main.includes('name="'+key+'"'));
  for(const key of ['observed_at','available_at','verification_at','reference_tickers','trigger_id','trigger','holding_horizon','plan_id','plan_reference','evidence','mode']){assert.equal((html.match(new RegExp('name="'+key+'"','g'))||[]).length,1);assert(!main.includes('name="'+key+'"'));}
});

test('wrapped narrative expectation stays editable while internal action types remain advanced',()=>{
  for(const key of ['text','raw']){
    const original={decision_id:'wrapped',revision:1,action_id:'action.sell',action:'卖出',reason:'理由',expectation:{[key]:'次日红盘—+2/+3出货',source:'原补记'}};
    const html=render(original),main=html.split('<details class="tj-decision-more">')[0];
    assert.equal(textarea(main,'expectation_note'),original.expectation[key]);
    assert(!main.includes('name="action_id"'));assert(html.includes('name="action_id"'));assert.equal((html.match(/name="action_id"/g)||[]).length,1);
    assert.deepEqual(JSON.parse(textarea(html,'expectation')),original.expectation);
    assert(html.includes('data-expectation-note-key="'+key+'"'));
  }
});

async function saveExpectation(original,noteValue,advanced=original,revisionReason='补充原意',reason='原理由',action='卖出') {
  const note={textContent:'',dataset:{}};let saved=null;
  const fields={reason,action,action_id:'action.sell',mode_id:'',trigger_id:'',mode:'',trigger:'',expectation:JSON.stringify(advanced),expectation_note:noteValue,invalidation:'',holding_horizon:'',risk_constraints:'',reference_tickers:'',evidence:'',observed_at:'',available_at:'',verification_at:'',plan_id:'',plan_reference:'',revision_reason:revisionReason};
  const elements=Object.entries(fields).map(([name,value])=>({name,value,setAttribute(){}}));elements.namedItem=name=>elements.find(e=>e.name===name);
  const key=Object.hasOwn(original,'text')?'text':'raw';
  const form={dataset:{decision:'old',revision:'2',fills:'["sell"]',expectationNoteKey:key},elements};
  const context={state:{saving:false,selected:'ep',detail:{decisions:[{decision_id:'old',revision:2,expectation:original}]}},
    array:v=>Array.isArray(v)?v:[],$:()=>note,normalizeModeSelection:()=>null,definitionRefs:()=>[],
    saveForm:async(form,note,request)=>request(),api:async(path,options)=>{saved=JSON.parse(options.body);},console};
  vm.createContext(context);
  vm.runInContext(source.slice(source.indexOf('  function formValues('),source.indexOf('  async function saveForm(')),context);
  await context.saveDecision(form);return {body:saved,note};
}

test('editing narrative merges only its text/raw key and never transmits helper fields',async()=>{
  for(const key of ['text','raw']){
    const original={[key]:'原始预期',source_ref:{source_id:'original'},metadata:{reason:'留存'}},before=JSON.stringify(original);
    const result=await saveExpectation(original,'新的预期');
    assert(result.body);assert.deepEqual(result.body.expectation,{...original,[key]:'新的预期'});assert(!('expectation_note' in result.body));
    assert.equal(JSON.stringify(original),before);assert.equal(result.body.expected_revision,2);
    const unchanged=await saveExpectation(original,'原始预期',{...original,metadata:{reason:'高级信息已补充'}});
    assert.deepEqual(unchanged.body.expectation,{...original,metadata:{reason:'高级信息已补充'}});
  }
});
test('untouched narrative preserves whitespace and conflicting numeric-rule edits are not overwritten',async()=>{
  const original={text:' 原文含空格 ',source:'保留'};
  const unchanged=await saveExpectation(original,original.text);assert.deepEqual(unchanged.body.expectation,original);
  const numeric={text:original.text,metric:'close_price',operator:'>=',target:20};
  const conflict=await saveExpectation(original,'改写文本',numeric);assert.equal(conflict.body,null);assert.match(conflict.note.textContent,/格式已.*改变/);
  const html=render({decision_id:'numeric',revision:1,action:'买入',reason:'理由',expectation:numeric});
  assert(!html.includes('name="expectation_note"'));assert.deepEqual(JSON.parse(textarea(html,'expectation')),numeric);
});

test('revision audit note is optional while the original trading rationale stays required',async()=>{
  const original={text:'次日反弹',source:'原补记'};
  const automatic=await saveExpectation(original,original.text,original,'');
  assert(automatic.body);assert.equal(automatic.body.revision_reason,'补充交易复盘内容');assert.equal(automatic.body.reason,'原理由');
  const explicit=await saveExpectation(original,original.text,original,'更正仓位上限');
  assert.equal(explicit.body.revision_reason,'更正仓位上限');
  const missingRationale=await saveExpectation(original,original.text,original,'','');
  assert.equal(missingRationale.body,null);assert.match(missingRationale.note.textContent,/决策理由/);
});


test('operation facts and rationale have distinct prompts and preserve multiple lines through save and reopen',async()=>{
  const action='买入大业股份1000股，17.48元\n实际分两笔成交';
  const reason='板块走强，回踩有承接\n这是一条判断依据，不是实际动作';
  const expectation='次日等待反弹\n没有反弹则重新评估退出';
  const result=await saveExpectation({text:'原预期'},expectation,{text:'原预期'},'',reason,action);
  assert.equal(result.body.action,action);assert.equal(result.body.reason,reason);assert.equal(result.body.expectation.text,expectation);
  const html=render({...result.body,decision_id:'old',revision:3});
  assert.equal(textarea(html,'action'),action);assert.equal(textarea(html,'reason'),reason);assert.equal(textarea(html,'expectation_note'),expectation);
  assert(html.includes('操作摘要（买入／加仓／减仓／卖出）'));assert(html.includes('只写实际动作'));assert(html.includes('这里写判断依据'));assert(html.includes('Enter 换行'));
  assert(!html.includes('<input name="action"'));
  const whitespace=await saveExpectation({text:'原预期'},'原预期',{text:'原预期'},'',' \n ');
  assert.equal(whitespace.body,null);assert.match(whitespace.note.textContent,/决策理由/);
});
