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
  run({key: 'Enter', shiftKey: true});
  run({key: 'Enter', isComposing: true});
  assert.equal(sent, 0);
  assert.equal(prevented, 0);
  run({key: 'Enter'});
  assert.equal(sent, 1);
  assert.equal(prevented, 1);
});

test('chat normalizes escaped markdown safely', () => {
  const source = html.match(/function renderMarkdown\(text\)\{[\s\S]*?\n\}/)[0];
  const escapeHtml = str => String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#x27;');
  const render = vm.runInNewContext('(' + source + ')', {escapeHtml});
  const output = render('Hello\\\n\\*   **Reason:** test\n&#x20;  \u003cscript\u003e');
  assert.match(output, /<li><strong>Reason:<\/strong> test<\/li>/);
  assert.ok(!output.includes('&#x20;'));
  assert.ok(output.includes('&lt;script&gt;'));
});
