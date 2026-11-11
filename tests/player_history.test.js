const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'..','dashboard.html'),'utf8');
function setup(){
  const elements={
    'player-history-dialog':{open:false,showModal(){this.open=true;}},
    'player-history-title':{},'player-history-content':{}
  };
  const requests=[];
  const context={AbortController,document:{getElementById:id=>elements[id]},
    fetch:(url,options)=>new Promise(resolve=>requests.push({url,options,resolve}))};
  vm.createContext(context);
  vm.runInContext('let playerHistoryController=null;let playerHistoryRequestId=0;',context);
  for(const name of ['escapeHtml','formHistoryButton','playerHistoryHTML','openPlayerHistory']){
    vm.runInContext(source.match(new RegExp(`(?:async )?function ${name}\\([^]*?\\n\\}`))[0],context);
  }
  return {context,elements,requests};
}

test('Every shared Form cell opens history and preserves zero form',()=>{
  const {context}=setup();
  const html=context.formHistoryButton({player_id:7,name:'A "name"',form:0});
  assert.match(html,/data-player-history="7"/);
  assert.match(html,/>0<\/button>/);
  assert.match(html,/&quot;name&quot;/);
  assert.doesNotMatch(context.formHistoryButton({player_id:'bad',form:1}),/<button/);
  assert.match(source,/\<td>\$\{formHistoryButton\(p\)\}<\/td>/);
});

test('History renders zero, negative scores, missing and upcoming gameweeks safely',()=>{
  const {context}=setup();
  const html=context.playerHistoryHTML({season:'2026/27',season_points:5,gameweeks:[
    {gameweek:1,points:0,minutes:0,status:'complete',fixtures:[{opponent:'<img onerror=alert(1)>',venue:'H',points:0,minutes:0}]},
    {gameweek:2,points:5,minutes:120,status:'complete',fixtures:[{opponent:'ONE',points:6,minutes:90},{opponent:'TWO',points:-1,minutes:30}]},
    {gameweek:3,points:null,minutes:null,status:'no_record',fixtures:[]},
    {gameweek:4,points:null,minutes:null,status:'upcoming',fixtures:[]}
  ]});
  assert.match(html,/<strong>0<\/strong>/);
  assert.match(html,/-1 pts, 30 min/);
  assert.match(html,/No record/);assert.match(html,/Upcoming/);
  assert.match(html,/GW 4/);assert.match(html,/<strong>—<\/strong>/);
  assert.doesNotMatch(html,/<img/);assert.match(html,/&lt;img/);
});

test('Rapid player switching aborts the old fetch and ignores its late response',async()=>{
  const {context,elements,requests}=setup();
  const first=context.openPlayerHistory(7),second=context.openPlayerHistory(8);
  assert.equal(requests[0].options.signal.aborted,true);
  requests[1].resolve({ok:true,json:async()=>({name:'Second',gameweeks:[]})});await second;
  requests[0].resolve({ok:true,json:async()=>({name:'First',gameweeks:[]})});await first;
  assert.match(elements['player-history-title'].textContent,/Second/);
  assert.match(elements['player-history-content'].innerHTML,/No current-season/);
});

test('Gateway HTML failures show a useful retry message',async()=>{
  const {context,elements,requests}=setup();
  const loading=context.openPlayerHistory(7);
  requests[0].resolve({ok:false,json:async()=>{throw new Error('Invalid JSON');}});
  await loading;
  assert.match(elements['player-history-content'].innerHTML,/temporarily unavailable/);
  assert.match(elements['player-history-content'].innerHTML,/click Form to try again/);
});
