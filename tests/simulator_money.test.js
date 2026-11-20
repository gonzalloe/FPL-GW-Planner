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
