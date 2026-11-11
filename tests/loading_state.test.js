const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'..','dashboard.html'),'utf8');

function setup() {
  const nodes=[],timers=new Map(),intervals=new Map(),requests=[];
  let timerId=0,renders=0;
  const element=()=>({style:{},innerHTML:'',querySelector:()=>({textContent:''}),
    remove(){const index=nodes.indexOf(this);if(index>=0)nodes.splice(index,1);},
    prepend(node){nodes.unshift(node);}});
  const overview=element(),main=element();
  const context={AUTH_TOKEN:'same-token',AUTH_USER:{plan:'premium'},DATA:null,USER_PLAN:null,ALL_PREDICTIONS:[],
    window:{},console:{warn:()=>{},error:()=>{}},location:{reload:()=>assert.fail('Unexpected reload')},
    document:{createElement:element,querySelector:()=>main,
      getElementById:id=>id==='page-overview'?overview:nodes.find(node=>node.id===id)||null,
      querySelectorAll:()=>nodes.filter(node=>['loading-overlay','data-waiting-card'].includes(node.id))},
    sessionStorage:{getItem:()=>null,setItem:()=>{}},predictionSnapshotOwner:()=> 'owner',
    authHeaders:()=>({}),loadGameweekDeadlines:()=>{},prewarmSecondaryData:()=>{},render:()=>renders++,
    fetch:()=>new Promise(resolve=>requests.push(resolve)),
    setTimeout:(callback)=>{const id=++timerId;timers.set(id,callback);return id;},clearTimeout:id=>timers.delete(id),
    setInterval:(callback)=>{const id=++timerId;intervals.set(id,callback);return id;},clearInterval:id=>intervals.delete(id)};
  vm.createContext(context);
  vm.runInContext('let predictionLoadId=0;let predictionRetryTimer=null;',context);
  for(const name of ['clearPredictionLoading','loadData']) {
    vm.runInContext(source.match(new RegExp(`(?:async )?function ${name}\\([^]*?\\n\\}`))[0],context);
  }
  const respond=(index,data)=>requests[index]({ok:true,json:async()=>data});
  return {context,nodes,timers,intervals,requests,respond,renders:()=>renders};
}

test('Overlapping startup/auth loads show one loader and ignore stale responses',async()=>{
  const state=setup();
  const first=state.context.loadData(),second=state.context.loadData();
  assert.equal(state.nodes.filter(node=>node.id==='loading-overlay').length,1);
  assert.equal(state.intervals.size,1);
  state.respond(1,{gameweek:11,predictions:[{name:'Latest'}]});await second;
  assert.equal(state.nodes.length,0);
  assert.equal(state.timers.size,0);
  assert.equal(state.intervals.size,0);
  state.respond(0,{gameweek:10,predictions:[{name:'Old'}]});await first;
  assert.equal(state.context.DATA.gameweek,11);
  assert.equal(state.renders(),1);
});

test('Cold start retains one waiting card/retry and removes both on success',async()=>{
  const state=setup();
  const first=state.context.loadData(),second=state.context.loadData();
  state.respond(1,{status:'preparing'});await second;
  state.respond(0,{status:'preparing'});await first;
  assert.equal(state.nodes.length,1);
  assert.equal(state.nodes[0].id,'data-waiting-card');
  assert.equal(state.timers.size,1);
  assert.equal(state.intervals.size,0);
  const [id,retry]=state.timers.entries().next().value;state.timers.delete(id);
  const retrying=retry();
  state.respond(2,{gameweek:11,predictions:[]});await retrying;
  assert.equal(state.nodes.length,0);
  assert.equal(state.timers.size,0);
  assert.equal(state.renders(),1);
});

test('A previous retry cannot overwrite a newer load with the same auth token',async()=>{
  const state=setup();const first=state.context.loadData();
  state.respond(0,{status:'preparing'});await first;
  const [id,retry]=state.timers.entries().next().value;state.timers.delete(id);
  const retrying=retry();
  const latest=state.context.loadData();
  state.respond(1,{gameweek:10,predictions:[]});await retrying;
  assert.equal(state.nodes.length,1);
  assert.equal(state.nodes[0].id,'loading-overlay');
  assert.equal(state.renders(),0);
  state.respond(2,{gameweek:11,predictions:[]});await latest;
  assert.equal(state.nodes.length,0);
  assert.equal(state.context.DATA.gameweek,11);
});
