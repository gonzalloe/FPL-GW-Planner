const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync('dashboard.html', 'utf8');
const textarea = html.match(/<textarea\b[^>]*id="chat-input"[^>]*><\/textarea>/)?.[0];

test('chat input supports newlines and explicit keyboard send', () => {
  assert.ok(textarea, 'multiline textarea exists');
  const handler = textarea.match(/onkeydown="([^"]+)"/)[1].replaceAll('&amp;', '&');
  let sent = 0, prevented = 0;
  const run = event => vm.runInNewContext(handler, {event: {...event, preventDefault() { prevented++; }}, sendChat() { sent++; }});
  run({key: 'Enter'});
  run({key: 'Enter', shiftKey: true});
  assert.equal(sent, 0);
  assert.equal(prevented, 0);
  run({key: 'Enter', ctrlKey: true});
  run({key: 'Enter', metaKey: true});
  run({key: 'Enter', ctrlKey: true, isComposing: true});
  assert.equal(sent, 2);
  assert.equal(prevented, 2);
});
