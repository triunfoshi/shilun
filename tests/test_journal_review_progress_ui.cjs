const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('shilun/static/trade-journal.js','utf8');
const functions = source.slice(source.indexOf('  function reviewProgressBadge('), source.indexOf('  async function selectEpisode('));
function render(items) {
  const nodes={tradeJournalList:{},tradeJournalPagination:{}};
  const context={state:{items,total:items.length,selected:'',offset:0,limit:30},$:(id)=>nodes[id],
    esc:(v)=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])),
    num:(v)=>v==null?'—':String(v)};
  vm.runInNewContext(source.slice(source.indexOf('  const table ='), source.indexOf('\n\n  async function api('))+functions+'\nrenderList();',context);
  return nodes.tradeJournalList.innerHTML;
}
test('list shows review state independently of holding status and keeps navigation names stable',()=>{
  const html=render(['reviewed','in_progress','needs_update','not_started'].map((status,i)=>({episode_id:'e'+i,ticker:'600000.SH',name:'证券'+i,status:'closed',review_progress:{status,note:'当前事实核对'}})));
  for(const label of ['✓ 已复盘','复盘中','待更新','未复盘'])assert(html.includes(label));
  assert.equal((html.match(/class="tj-review-progress"/g)||[]).length,4);
  for(const body of html.matchAll(/<button[^>]*class="tj-episode[^>]*>([\s\S]*?)<\/button>/g))assert(!body[1].includes('tj-review-progress'));
  assert(html.includes('已清仓'));
});
test('unknown or absent review status cannot claim completion or inject markup',()=>{
  const html=render([{episode_id:'x',ticker:'600000.SH',name:'<script>',review_progress:{status:'<img>',note:'<img src=x>'}}, {episode_id:'y',ticker:'600001.SH',name:'另一个'}]);
  assert.equal((html.match(/data-review-status="not_started"/g)||[]).length,2);
  assert(!html.includes('已复盘'));
  assert(!html.includes('<img'));
  assert(html.includes('&lt;img'));
});

test('saved operation notes visibly count as filled without claiming a completed result check',()=>{
 const html=render([{episode_id:'jiuyang',ticker:'002242.SZ',name:'九阳股份',review_progress:{status:'in_progress',has_saved_notes:true,saved_note_count:1,note:'已保存操作理由和预期，尚未确认整轮'}}]);
 assert(html.includes('已填写 · 待确认'));
 assert(!html.includes('未复盘'));assert(!html.includes('✓ 已复盘'));
 const stale=render([{episode_id:'changed',ticker:'002242.SZ',name:'九阳股份',review_progress:{status:'needs_update',has_saved_notes:true}}]);
 assert(stale.includes('待更新'));assert(!stale.includes('已填写 · 待确认'));
});
