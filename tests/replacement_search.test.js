const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'..','dashboard.html'),'utf8');
function setup(){
  const elements={},timers=new Map(),requests=[],rendered=[],queued=[];let timerId=0;
  const get=id=>elements[id]??=( {value:id==='sim-search'?'hecke':id==='sim-pos-filter'?'DEF':id==='sim-gw-select'?'11':'',style:{},innerHTML:''});
  const context={AbortController,window:{},MY_TEAM_DATA:{starters:[{player_id:1,price:5}],bench:[],gw_summary:{bank:3}},
    SIM_SELL_ID:1,SIM_BUY_ID:null,authHeaders:()=>({'Authorization':'Bearer test-session','Content-Type':'application/json'}),
    document:{getElementById:get,querySelectorAll:()=>[],querySelector:()=>null},
    renderBuyList:players=>rendered.push(players),renderFixtures:()=>'',addToTransferQueue:data=>queued.push(data),
    fetch:async(url,options)=>{requests.push({url,options});return {ok:true,json:async()=>({players:[{player_id:80,name:'Van Hecke'}],impact:{},out_player:{selling_price:5,selling_price_source:'fpl_pick'},in_player:{name:'Van Hecke',purchase_price:5}})};},
    setTimeout:cb=>{const id=++timerId;timers.set(id,cb);return id;},clearTimeout:id=>timers.delete(id)};
  vm.createContext(context);vm.runInContext('let _searchTimeout=null;let replacementSearchId=0;let replacementSearchController=null;',context);
  for(const name of ['escapeHtml','simulatorTransferPrices','fetchSimulatorJSON','showReplacementError','searchReplacements','searchReplacementById','selectBuyPlayer','quickBuyPlayer']){
    vm.runInContext(source.match(new RegExp(`(?:async )?function ${name}\\([^]*?\\n\\}`))[0],context);
  }
  const run=()=>{const [id,callback]=Array.from(timers.entries()).pop();timers.delete(id);return callback();};
  return {context,elements,get,requests,rendered,queued,run};
}

test('Replacement names and position searches include the session token',async()=>{
  const state=setup();state.context.searchReplacements();await state.run();
  assert.match(state.requests[0].url,/q=hecke&pos=DEF/);
  assert.equal(state.requests[0].options.headers.Authorization,'Bearer test-session');
  assert.equal(state.rendered[0][0].name,'Van Hecke');
});

test('Authentication and gateway errors are not displayed as empty search results',async()=>{
  const state=setup();
  state.context.fetch=async()=>({ok:false,status:401,json:async()=>({error:'Sign in to use this feature.'})});
  state.context.searchReplacements();await state.run();
  assert.match(state.get('sim-buy-list').innerHTML,/Sign in/);
  assert.equal(state.rendered.length,0);
  state.context.fetch=async()=>({ok:false,status:503,json:async()=>{throw new Error('HTML response');}});
  state.context.searchReplacements();await state.run();
  assert.match(state.get('sim-buy-list').innerHTML,/HTTP 503/);
  assert.doesNotMatch(state.get('sim-buy-list').innerHTML,/No matching/);
});

test('A stale search cannot overwrite results for a newer search',async()=>{
  const state=setup(),pending=[];
  state.context.fetch=(url,options)=>new Promise(resolve=>pending.push({resolve,options}));
  state.get('sim-search').value='he';state.context.searchReplacements();const old=state.run();
  state.get('sim-search').value='hecke';state.context.searchReplacements();const latest=state.run();
  assert.equal(pending[0].options.signal.aborted,true);
  pending[1].resolve({ok:true,json:async()=>({players:[{name:'Latest'}]})});await latest;
  pending[0].resolve({ok:true,json:async()=>({players:[{name:'Old'}]})});await old;
  assert.equal(state.rendered.length,1);assert.equal(state.rendered[0][0].name,'Latest');
});

test('Suggested players use exact ID lookup and authenticated preview/buy requests',async()=>{
  const state=setup();await state.context.searchReplacementById(80);
  assert.equal(state.requests[0].url,'/api/search-players?id=80');
  assert.equal(state.rendered[0][0].player_id,80);
  assert.equal(state.requests[1].url,'/api/simulate-transfer');
  assert.equal(state.requests[1].options.method,'POST');
  assert.equal(state.requests[1].options.headers.Authorization,'Bearer test-session');
  await state.context.quickBuyPlayer(80);
  assert.equal(state.requests[2].options.headers.Authorization,'Bearer test-session');
  assert.equal(state.queued.length,1);
});
