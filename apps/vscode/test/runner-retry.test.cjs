'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const {describe, runWithRetry} = require('./host/retry.cjs');

const fast = {delayMs: 0, sleep: async () => {}};

test('a first success returns at once and logs nothing', async () => {
  const logs = [];
  assert.equal(await runWithRetry(async () => 0, {...fast, log: t => logs.push(t)}), 0);
  assert.deepEqual(logs, []);
});

test('thrown setup errors are retried and the later result is returned', async () => {
  const logs = [];
  let calls = 0;
  const result = await runWithRetry(async () => { calls += 1; if (calls < 3) throw new Error('resolve failed'); return 0; }, {...fast, log: t => logs.push(t)});
  assert.equal(result, 0);
  assert.equal(calls, 3);
  assert.equal(logs.length, 2);
  assert.match(logs[0], /DBOUND_HOST_ATTEMPT 1\/3 failed: Error: resolve failed/);
});

test('a resolved failing exit code is returned and never repeated', async () => {
  let calls = 0;
  assert.equal(await runWithRetry(async () => { calls += 1; return 1; }, fast), 1);
  assert.equal(calls, 1);
});

test('after the last attempt the last error is thrown', async () => {
  let calls = 0;
  await assert.rejects(runWithRetry(async () => { calls += 1; throw new Error('attempt ' + calls); }, {...fast, attempts: 3}), /attempt 3/);
  assert.equal(calls, 3);
});

test('waits grow with each attempt', async () => {
  const waits = [];
  await assert.rejects(runWithRetry(async () => { throw new Error('x'); }, {attempts: 3, delayMs: 10, sleep: async ms => waits.push(ms)}));
  assert.deepEqual(waits, [10, 20]);
});

test('an empty message still says what failed and shows the cause chain', () => {
  const cause = Object.assign(new Error('socket hang up'), {code: 'ECONNRESET'});
  const error = new Error('', {cause});
  assert.match(describe(error), /Error \(empty message\) <- Error code=ECONNRESET: socket hang up/);
  assert.match(describe('plain text'), /plain text/);
});
