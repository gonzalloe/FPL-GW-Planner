const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '..', 'dashboard.html'), 'utf8');
const elements = {'overview-deadline':{}, 'myteam-deadline':{}};
const context = {Date, document:{getElementById:id => elements[id]},
  DATA:{gameweek:10}, MY_TEAM_DATA:{info:{planning_gw:11}},
  GAMEWEEK_DEADLINES:[
    {id:10, deadline_time:'2026-11-07T11:00:00Z'},
    {id:11, deadline_time:'2026-11-21T11:00:00Z'}
  ]};
vm.createContext(context);
for (const name of ['gameweekDeadlineText', 'renderGameweekDeadlines']) {
  const match = source.match(new RegExp(`function ${name}\\([^]*?\\n\\}`));
  assert.ok(match, `Missing ${name}`);
  vm.runInContext(match[0], context);
}

test('Uses the requested GW official timestamp and viewer local timezone', () => {
  const original = process.env.TZ;
  process.env.TZ = 'Asia/Kuala_Lumpur';
  try {
    const label = context.gameweekDeadlineText(context.GAMEWEEK_DEADLINES, 11, 0);
    assert.match(label, /GW 11 transfer deadline:/);
    assert.match(label, /21/);
    assert.match(label, /07:00 PM|19:00/);
    assert.match(label, /GMT\+8|MYT/);
    assert.doesNotMatch(label, /passed/);
  } finally {
    if (original === undefined) delete process.env.TZ;
    else process.env.TZ = original;
  }
});

test('Missing and invalid deadlines never invent a date', () => {
  for (const deadline_time of [null, '', 'bad-date', 123]) {
    assert.equal(context.gameweekDeadlineText([{id:1, deadline_time}], 1), 'Transfer deadline unavailable');
  }
  assert.equal(context.gameweekDeadlineText([], 1), 'Transfer deadline unavailable');
});

test('A passed deadline is explicitly identified', () => {
  assert.match(context.gameweekDeadlineText(context.GAMEWEEK_DEADLINES, 10, Date.parse('2026-11-08')), /\(passed\)$/);
});

test('Overview and imported team use their own gameweek context', () => {
  context.renderGameweekDeadlines();
  assert.match(elements['overview-deadline'].textContent, /GW 10 transfer deadline:/);
  assert.match(elements['myteam-deadline'].textContent, /GW 11 transfer deadline:/);
});
