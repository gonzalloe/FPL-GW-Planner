const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '..', 'dashboard.html'), 'utf8');
const context = {_playersSortKey:'start_prob', _playersSortDir:'desc'};
vm.createContext(context);
for (const name of ['normalizedProbability','confBar','playerTotalPoints','sortPlayersTable']) {
  const match = source.match(new RegExp(`function ${name}\\([^]*?\\n\\}`));
  assert.ok(match, `Missing ${name}`);
  vm.runInContext(match[0], context);
}

test('Start % uses decimal probabilities once and preserves actual zero', () => {
  for (const [value, label] of [[0,'0%'], [.25,'25%'], [.75,'75%'], [.85,'85%'], [1,'100%']]) {
    const html = context.confBar(value);
    assert.ok(html.includes(`>${label}</span>`), html);
    assert.ok(html.includes(`width:${label}`), html);
  }
});

test('Missing or invalid probabilities render unknown, never zero or 100%', () => {
  for (const value of [null, undefined, '', false, '85', 85, -1, 1.01, NaN, Infinity]) {
    const html = context.confBar(value);
    assert.ok(html.includes('--'), `${String(value)}: ${html}`);
    assert.ok(!html.includes('width:'), html);
  }
});

test('Start % sorting keeps missing values last in both directions and zero is real', () => {
  const players = [
    {id:'missing',starter_quality:{p_start:null}},
    {id:'starter',starter_quality:{p_start:.95}},
    {id:'out',starter_quality:{p_start:0}},
    {id:'rotation',starter_quality:{p_start:.5}},
  ];
  assert.deepEqual(Array.from(context.sortPlayersTable(players),p=>p.id), ['starter','rotation','out','missing']);
  context._playersSortDir='asc';
  assert.deepEqual(Array.from(context.sortPlayersTable(players),p=>p.id), ['out','rotation','starter','missing']);
});


test('Season total points preserve zero/negative values and sort unknown values last', () => {
  const players = [
    {id:'unknown',total_points:null}, {id:'zero',total_points:0},
    {id:'high',total_points:105}, {id:'negative',total_points:-1},
    {id:'string',total_points:'12'}
  ];
  context._playersSortKey='total_points';context._playersSortDir='desc';
  assert.deepEqual(Array.from(context.sortPlayersTable(players),p=>p.id),['high','string','zero','negative','unknown']);
  context._playersSortDir='asc';
  assert.deepEqual(Array.from(context.sortPlayersTable(players),p=>p.id),['negative','zero','string','high','unknown']);
  for (const value of [undefined,'',false,NaN,Infinity,'bad']) {
    assert.equal(context.playerTotalPoints({total_points:value}),null);
  }
});
