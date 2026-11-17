'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {spawnSync} = require('node:child_process');
const {describe, runWithRetry} = require('./host/retry.cjs');

const fast = {delayMs: 0, sleep: async () => {}};

test('a first success returns at once and logs nothing', async () => {
  const logs = [];
  assert.equal(await runWithRetry(async () => 'host', {...fast, log: t => logs.push(t)}), 'host');
  assert.deepEqual(logs, []);
});

test('thrown errors are retried and the later result is returned', async () => {
  const logs = [];
  let calls = 0;
  const result = await runWithRetry(async () => { calls += 1; if (calls < 3) throw new Error('resolve failed'); return 'host'; }, {...fast, log: t => logs.push(t)});
  assert.equal(result, 'host');
  assert.equal(calls, 3);
  assert.equal(logs.length, 2);
  assert.match(logs[0], /DBOUND_HOST_ATTEMPT 1\/3 failed: Error: resolve failed/);
});

test('after the last attempt the last error is thrown', async () => {
  let calls = 0;
  await assert.rejects(runWithRetry(async () => { calls += 1; throw new Error('attempt ' + calls); }, {...fast, attempts: 3}), /attempt 3/);
  assert.equal(calls, 3);
});

test('waits grow with each attempt and the defaults are 5 and 10 seconds', async () => {
  const waits = [];
  await assert.rejects(runWithRetry(async () => { throw new Error('x'); }, {attempts: 3, delayMs: 10, sleep: async ms => waits.push(ms)}));
  assert.deepEqual(waits, [10, 20]);
  const defaults = [];
  await assert.rejects(runWithRetry(async () => { throw new Error('x'); }, {sleep: async ms => defaults.push(ms)}));
  assert.deepEqual(defaults, [5000, 10000]);
});

test('an empty message still says what failed and shows the cause chain', () => {
  const cause = Object.assign(new Error('socket hang up'), {code: 'ECONNRESET'});
  assert.match(describe(new Error('', {cause})), /Error \(empty message\) <- Error code=ECONNRESET: socket hang up/);
});

test('an AggregateError with an empty message lists each address error', () => {
  const first = Object.assign(new Error('connect ECONNREFUSED 1.2.3.4:443'), {code: 'ECONNREFUSED'});
  const second = Object.assign(new Error('connect ENETUNREACH ::1:443'), {code: 'ENETUNREACH'});
  const text = describe(Object.assign(new AggregateError([first, second], ''), {code: 'ECONNREFUSED'}));
  assert.match(text, /AggregateError code=ECONNREFUSED \(empty message\)/);
  assert.match(text, /ECONNREFUSED 1\.2\.3\.4:443/);
  assert.match(text, /ENETUNREACH ::1:443/);
});

test('strings, objects, null and hostile values never throw or hide the error', () => {
  assert.equal(describe('Failed to get JSON'), 'Failed to get JSON');
  assert.equal(describe(null), 'null');
  assert.match(describe({message: 'm'}), /Error: m/);
  assert.match(describe(Object.create(null)), /empty message/);
  const loop = new Error('loop');
  loop.cause = loop;
  assert.equal(describe(loop), 'Error: loop');
  const hostile = {get message() { throw new Error('boom'); }};
  assert.match(describe(hostile), /unprintable error/);
});

// Wiring: run the real runner.cjs against a fake @vscode/test-electron that records its calls.
function runRunner(fake) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'dbound-runner-'));
  try {
    fs.mkdirSync(path.join(dir, 'host'));
    for (const name of ['runner.cjs', 'retry.cjs']) fs.copyFileSync(path.join(__dirname, 'host', name), path.join(dir, 'host', name));
    const pkg = path.join(dir, 'node_modules', '@vscode', 'test-electron');
    fs.mkdirSync(pkg, {recursive: true});
    const calls = path.join(dir, 'calls.json');
    fs.writeFileSync(calls, JSON.stringify({download: 0, run: 0}));
    fs.writeFileSync(path.join(pkg, 'index.js'), `
const fs = require('node:fs');
const calls = ${JSON.stringify(calls)};
const bump = key => { const d = JSON.parse(fs.readFileSync(calls)); d[key] += 1; fs.writeFileSync(calls, JSON.stringify(d)); return d[key]; };
exports.downloadAndUnzipVSCode = async () => { const n = bump('download'); ${fake.download} };
exports.runTests = async options => { bump('run'); if (!options.vscodeExecutablePath) throw new Error('no host path'); ${fake.run} };
`);
    const result = spawnSync(process.execPath, [path.join(dir, 'host', 'runner.cjs')], {encoding: 'utf8', timeout: 60000});
    return {...result, calls: JSON.parse(fs.readFileSync(calls))};
  } finally {
    fs.rmSync(dir, {recursive: true, force: true});
  }
}

test('runner: a failed download is retried and then the tests run once', {timeout: 60000}, () => {
  const result = runRunner({download: "if (n < 2) throw new Error('lookup failed'); return '/fake/code';", run: 'return 0;'});
  assert.equal(result.status, 0, result.stderr);
  assert.deepEqual(result.calls, {download: 2, run: 1});
  assert.match(result.stdout, /DBOUND_HOST_RUNNER_EXIT code=0/);
  assert.match(result.stderr, /DBOUND_HOST_ATTEMPT 1\/3 failed: Error: lookup failed/);
});

test('runner: a failing test run is not retried and fails the run with no exit marker', {timeout: 60000}, () => {
  const result = runRunner({download: "return '/fake/code';", run: "throw Object.assign(new Error('Test run failed with code 1'), {code: 1});"});
  assert.equal(result.status, 1);
  assert.deepEqual(result.calls, {download: 1, run: 1});
  assert.doesNotMatch(result.stdout, /DBOUND_HOST_RUNNER_EXIT/);
  assert.match(result.stderr, /VS Code host smoke failed: Error code=1: Test run failed with code 1/);
});
