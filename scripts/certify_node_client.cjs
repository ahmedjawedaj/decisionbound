'use strict';
/** End-to-end Node transport probe for the installed Python API. Not a VS Code host test. */
const assert = require('node:assert/strict');
const {LocalControlClient} = require('../apps/vscode/src/client.js');
(async () => {
  const c = new LocalControlClient({
    baseUrl: 'http://127.0.0.1:' + process.env.DBOUND_CERT_PORT,
    token: process.env.DBOUND_CERT_TOKEN,
    timeoutMs: 5000
  });
  const capabilities = await c.capabilities();
  assert.equal(capabilities.auto_merge, false);
  const setup = await c.setupStatus();
  assert.equal(setup.profile, 'local-safe');
  assert.equal(setup.integration_tested, false);
  const id = process.env.DBOUND_CERT_PROJECT;
  const wf = process.env.DBOUND_CERT_WORKFLOW;
  const sessions = await c.sessions(id);
  assert.ok(sessions.sessions.some(x => x.session_id === wf));
  const snapshot = await c.snapshot(id, wf);
  assert.equal(snapshot.task_completed, false);
  const events = await c.events(id, wf);
  assert.ok(events.events.length > 0);
  // Local client cannot approve, merge or execute; it only reads metadata.
  process.stdout.write(JSON.stringify({connected:true,read_only:true,task_completed:false}));
})().catch(error => {
  // Do not print request headers, token, API responses, or private paths.
  process.stderr.write('Node transport certification failed: ' + error.name + '\n');
  process.exitCode = 1;
});
