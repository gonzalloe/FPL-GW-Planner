const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'..','dashboard.html'),'utf8');
function setup(){
  const element={};
  const context={SIM_BASE_BANK:3.1,MY_TEAM_DATA:{gw_summary:{bank:3.0000000000000001}},TRANSFER_QUEUE:[],
    document:{getElementById:()=>element}};
  vm.createContext(context);
  for(const name of ['getSimulatorBank','updateSimulatorBankDisplay']){
    vm.runInContext(source.match(new RegExp(`function ${name}\\([^]*?\\n\\}`))[0],context);
  }
  return {context,element};
}

test('Bank is calculated in integer tenths and displayed with exactly one decimal',()=>{
  const {context,element}=setup();
  context.TRANSFER_QUEUE=[{out_sell_price:5.2,in_price:5.3}];
  assert.equal(context.getSimulatorBank(),3);
  context.updateSimulatorBankDisplay();
  assert.equal(element.textContent,'Bank: £3.0m');
  context.TRANSFER_QUEUE.push({out_sell_price:4.1,in_price:4.3});
  assert.equal(context.getSimulatorBank(),2.8);
  context.updateSimulatorBankDisplay();
  assert.equal(element.textContent,'Bank: £2.8m');
  context.TRANSFER_QUEUE=[];
  context.updateSimulatorBankDisplay();
  assert.equal(element.textContent,'Bank: £3.1m');
});

test('Selling-price fallback, exact zero, negative bank and stored float tails format correctly',()=>{
  const {context,element}=setup();
  context.SIM_BASE_BANK=0.3;
  context.TRANSFER_QUEUE=[{out_price:5.1,in_price:5.4}];
  context.updateSimulatorBankDisplay();assert.equal(element.textContent,'Bank: £0.0m');
  context.TRANSFER_QUEUE.push({out_price:4,in_price:4.1});
  context.updateSimulatorBankDisplay();assert.equal(element.textContent,'Bank: £-0.1m');
  context.SIM_BASE_BANK=3.0000000000000001;context.TRANSFER_QUEUE=[];
  context.updateSimulatorBankDisplay();assert.equal(element.textContent,'Bank: £3.0m');
});

test('Saved Hall to Vuskovic plan is repriced from the imported cost basis',()=>{
  const saved={transfers:[{out_id:449,out_price:5.2,out_sell_price:5.2,in_id:504,in_name:'Vuskovic',
    in_team:'TOT',in_price:5.3,in_player_full:{player_id:504,position_id:2,price:5.3}}]};
  const context={SIM_BASE_BANK:3.1,TRANSFER_QUEUE:[],MY_TEAM_DATA:{
    starters:[{player_id:449,price:5.2,purchase_price:5.1,selling_price:5.1,selling_price_source:'purchase_history',position_id:2}],
    bench:[],gw_summary:{bank:3.1},market_prices:{449:5.2,504:5.3}},
    simulatorStorageKey:()=> 'test',localStorage:{getItem:()=>JSON.stringify(saved),removeItem:()=>{}},
    document:{getElementById:()=>null},renderSimPitch:()=>{},renderTransferQueue:()=>{},console};
  vm.createContext(context);
  for(const name of ['simulatorTransferPrices','getSimulatorBank','applyTransferToSquadHelper','loadSimState']){
    vm.runInContext(source.match(new RegExp(`function ${name}\\([^]*?\\n\\}`))[0],context);
  }
  context.loadSimState();
  assert.equal(context.TRANSFER_QUEUE[0].out_sell_price,5.1);
  assert.equal(context.getSimulatorBank(),2.9);
  assert.equal(context.MY_TEAM_DATA.gw_summary.bank,2.9);
  assert.equal(context.MY_TEAM_DATA.starters[0].purchase_price,5.3);
  assert.equal(context.MY_TEAM_DATA.starters[0].position_id,2);
  // Once the real transfer is reflected by FPL, the old saved plan is stale.
  context.SIM_BASE_BANK=2.9;context.TRANSFER_QUEUE=[];let removed=false;
  context.localStorage.removeItem=()=>removed=true;
  context.loadSimState();
  assert.equal(removed,true);
  assert.equal(context.TRANSFER_QUEUE.length,0);
  assert.equal(context.getSimulatorBank(),2.9);
});

test('Unknown selling prices are explicitly labeled as estimates',()=>{
  const {context,element}=setup();
  context.TRANSFER_QUEUE=[{out_sell_price:5.2,in_price:5.3,out_sell_price_source:'market_estimate'}];
  context.updateSimulatorBankDisplay();
  assert.equal(element.textContent,'Bank (estimated): £3.0m');
});
