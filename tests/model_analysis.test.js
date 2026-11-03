const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync('dashboard.html', 'utf8');
const source = html.slice(html.indexOf('let MODEL_ANALYSIS_DATA = null;'), html.indexOf('function showWeightDetails()'));

function harness(responses) {
  const nodes = new Map();
  const requests = [];
  const context = vm.createContext({
    AbortController,
    authHeaders: () => ({}),
    escapeHtml: text => String(text).replaceAll('<', '&lt;'),
    document: {getElementById: id => {
      if (!nodes.has(id)) nodes.set(id, {style: {}, textContent: '', disabled: false});
      return nodes.get(id);
    }},
    setTimeout: (callback, delay) => { if (delay === 1500) queueMicrotask(callback); return 1; },
    clearTimeout: () => {},
    fetch: async (url, options) => {
      requests.push(options.method);
      const entry = responses.shift();
      if (entry instanceof Error) throw entry;
      return {ok: entry.status < 400, status: entry.status,
              text: async () => typeof entry.body === 'string' ? entry.body : JSON.stringify(entry.body)};
    }
  });
  vm.runInContext(source, context);
  return {context, nodes, requests, run: () => vm.runInContext('analyzeModelPerformance()', context)};
}

test('analysis starts once, polls, and renders completed metrics', async () => {
  const h = harness([
    {status: 202, body: {status: 'running', job_id: 'job'}},
    {status: 200, body: {status: 'completed', job_id: 'job', result: {averages: {mae: 2, rmse: 3, correlation: 0.5}, performance_grade: 'B', suggestions: []}}}
  ]);
  const first = h.run();
  await h.run(); // A second click while busy must not create another job.
  await first;
  assert.deepEqual(h.requests, ['POST', 'GET']);
  assert.equal(h.nodes.get('model-mae').textContent, 2);
  assert.equal(h.nodes.get('model-analysis-result').style.display, 'block');
  assert.equal(h.nodes.get('model-analysis-btn').disabled, false);
});

test('HTML gateway errors show a helpful message and clear the spinner', async () => {
  const h = harness([{status: 504, body: '<html>Gateway Timeout</html>'}]);
  await h.run();
  assert.match(h.nodes.get('model-analysis-error').textContent, /HTTP 504/);
  assert.equal(h.nodes.get('model-analysis-loading').style.display, 'none');
  assert.equal(h.nodes.get('model-analysis-btn').disabled, false);
  assert.equal(vm.runInContext('MODEL_ANALYSIS_DATA', h.context), null);
});

test('no eligible forecasts displays the backend explanation', async () => {
  const h = harness([{status: 202, body: {status: 'running', job_id: 'job'}},
    {status: 200, body: {status: 'failed', job_id: 'job', error: 'No forecasts', result: {suggestion: 'Keep pre-deadline snapshots'}}}]);
  await h.run();
  assert.equal(h.nodes.get('model-analysis-error').textContent, 'No forecasts Keep pre-deadline snapshots');
  assert.equal(h.nodes.get('model-analysis-loading').style.display, 'none');
});

test('server restart stops polling with a recoverable error', async () => {
  const h = harness([{status: 202, body: {status: 'running', job_id: 'job'}},
    {status: 200, body: {status: 'idle', job_id: null}}]);
  await h.run();
  assert.match(h.nodes.get('model-analysis-error').textContent, /server restart/);
  assert.equal(h.nodes.get('model-analysis-btn').disabled, false);
});
