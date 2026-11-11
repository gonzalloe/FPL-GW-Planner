const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'..','dashboard.html'),'utf8');
test('Admin actions pass malicious emails as data without executing them',()=>{
  const malicious="');globalThis.injected=true;//@example.test";
  const calls=[],buttons=['upgrade','free','delete'].map(action=>({
    dataset:{userAction:action,email:malicious},addEventListener(name,callback){this.click=callback;}
  }));
  const context={adminQuickUpgrade:email=>calls.push(['upgrade',email]),
    adminQuickFree:email=>calls.push(['free',email]),adminQuickDelete:email=>calls.push(['delete',email])};
  vm.createContext(context);
  vm.runInContext(source.match(/function bindAdminUserActions\([^]*?\n\}/)[0],context);
  context.bindAdminUserActions({querySelectorAll:()=>buttons});
  buttons.forEach(button=>button.click());
  assert.equal(context.injected,undefined);
  assert.deepEqual(calls,[['upgrade',malicious],['free',malicious],['delete',malicious]]);
  const render=source.match(/async function loadAdminUsers\([^]*?\n\}/)[0];
  assert.doesNotMatch(render,/onclick=/);
  assert.match(render,/data-email="\$\{escapeHtml\(u.email\)\}"/);
});

test('Prediction snapshots are separated by verified account and plan',()=>{
  const context={AUTH_TOKEN:'',AUTH_USER:null};vm.createContext(context);
  vm.runInContext(source.match(/function predictionSnapshotOwner\([^]*?\n\}/)[0],context);
  assert.equal(context.predictionSnapshotOwner(),'guest');
  context.AUTH_TOKEN='pending-token';
  assert.equal(context.predictionSnapshotOwner(),null);
  context.AUTH_USER={email:'one@example.test',plan:'premium'};
  assert.equal(context.predictionSnapshotOwner(),'one@example.test:premium');
  context.AUTH_USER.plan='free';
  assert.equal(context.predictionSnapshotOwner(),'one@example.test:free');
  context.AUTH_USER.email='two@example.test';
  assert.equal(context.predictionSnapshotOwner(),'two@example.test:free');
});

test('Free team import renders locked prediction fields without simulator initialization',async()=>{
  const elements=new Map();
  const get=id=>{
    if(!elements.has(id))elements.set(id,{value:id==='team-id-input'?'101':'',innerHTML:'',style:{}});
    return elements.get(id);
  };
  const player={player_id:1,name:'Public Player',team:'ABC',position:'MID',price:5,
    predicted_points:'🔒',raw_xpts:'🔒',team_season_wr:'🔒',starter_quality:{tier:'🔒',p_start:'🔒'},
    availability:{status:'available'},fixtures:[{opponent:'XYZ',venue:'H',fdr:3,win_probability:'🔒'}]};
  const context={document:{getElementById:get},console:{log:()=>{}},authHeaders:()=>({}),
    renderGameweekDeadlines:()=>{},updateSimulatorFreeTransferDisplay:()=>{},isPreseasonTeam:()=>false,
    renderTeamL5:()=>'',renderFixtures:()=>'',confBar:()=>'',_playersSortKey:'',_playersSortDir:'desc',
    fetch:async()=>({json:async()=>({team_id:101,info:{name:'Public team'},gw_summary:{bank:2,value:100},
      starters:[player],bench:[],transfer_suggestions:[],free_transfers:1,squad_value:100,predicted_points:'🔒'})})};
  vm.createContext(context);
  for(const name of ['escapeHtml','playerTableHTML','loadMyTeam']){
    const match=source.match(new RegExp(`(?:async )?function ${name}\\([^]*?\\n\\}`));
    vm.runInContext(match[0],context);
  }
  await context.loadMyTeam();
  assert.match(get('myteam-xi').innerHTML,/Public Player/);
  assert.match(get('myteam-xi').innerHTML,/🔒/);
  assert.equal(get('myteam-loaded').style.display,'');
  assert.doesNotMatch(get('myteam-content').innerHTML,/Error:/);
});
