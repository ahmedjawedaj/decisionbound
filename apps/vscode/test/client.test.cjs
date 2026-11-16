'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const {LocalControlClient, normalizeLoopbackUrl, parseProjectId} = require('../src/client');

const PROJECT = 'prj_' + 'a'.repeat(32);
const SESSION = 'wf_' + 'b'.repeat(32);
const TOKEN = 'some-secret-pairing-token-that-must-never-leak';
const capability = {protocol:'decisionbound-local-client', version:1, transport:'authenticated-loopback-http'};

test('only explicit loopback transports can receive bearer credentials', () => {
  for (const bad of ['https://127.0.0.1:8765', 'http://example.com:8765',
    'http://localhost.attacker.org', 'http://user:pass@localhost:8765',
    'http://localhost:8765/evil', 'http://localhost:8765/?token=1',
    'file:///tmp/secret', 'http://192.168.0.4:8765', 'http://[::2]:8765']) {
    assert.throws(() => normalizeLoopbackUrl(bad), /Only plain HTTP loopback/);
  }
  assert.equal(normalizeLoopbackUrl('http://127.0.0.1:8765'), 'http://127.0.0.1:8765');
  assert.equal(normalizeLoopbackUrl('http://[::1]:8765'), 'http://[::1]:8765');
});

test('the token is sent only to the fixed local endpoint with redirects forbidden', async () => {
  const calls = [];
  const client = new LocalControlClient({baseUrl:'http://127.0.0.1:8765', token:TOKEN,
    fetchImpl:async (url, options) => {
      calls.push({url, options});
      return {ok:true, json:async()=>capability};
    }});
  assert.equal((await client.capabilities()).version, 1);
  assert.equal(calls[0].options.headers.Authorization, 'Bearer ' + TOKEN);
  assert.equal(calls[0].options.redirect, 'error');
  assert.equal(calls[0].options.cache, 'no-store');
  assert.equal(calls[0].url, 'http://127.0.0.1:8765/api/client/v1/capabilities');
  await assert.rejects(client.request('http://evil.test/api/client/v1/changes'), /Unsafe/);
});

test('project ids and cursors are validated locally before HTTP requests', async () => {
  const urls = [];
  const client = new LocalControlClient({baseUrl:'http://localhost:8765', token:TOKEN,
    fetchImpl:async u=>{urls.push(u);return {ok:true,json:async()=>({sessions:[],events:[]})};}});
  await client.sessions(PROJECT, 5, 10);
  await client.snapshot(PROJECT, SESSION);
  await client.events(PROJECT, SESSION, 2);
  await client.changes(PROJECT, 6);
  assert.equal(urls.length, 4);
  assert.ok(urls[0].includes('before=10'));
  for (const call of [()=>client.sessions('prj_/../../bad'),
                      ()=>client.events(PROJECT, '../evil'),
                      ()=>client.changes(PROJECT, -1),
                      ()=>client.sessions(PROJECT, 101)]) {
    assert.throws(call);
  }
  assert.equal(urls.length, 4);
});

test('incompatible protocols fail closed and HTTP errors never include a secret', async () => {
  const badVersion = new LocalControlClient({baseUrl:'http://localhost:8765', token:TOKEN,
    fetchImpl: async()=>({ok:true,json:async()=>({...capability,version:2})})});
  await assert.rejects(badVersion.capabilities(), /Incompatible/);
  const failed = new LocalControlClient({baseUrl:'http://localhost:8765',token:TOKEN,
    fetchImpl: async()=>({ok:false,status:401,json:async()=>({secret:TOKEN})})});
  await assert.rejects(failed.capabilities(), err => !err.message.includes(TOKEN) && /401/.test(err.message));
  const thrown = new LocalControlClient({baseUrl:'http://localhost:8765',token:TOKEN,
    fetchImpl: async()=>{throw Error('Provider failed with token ' + TOKEN);}});
  await assert.rejects(thrown.capabilities(), err => !err.message.includes(TOKEN) && /unavailable/.test(err.message));
});

test('project manifest parser rejects duplication and malformed IDs', () => {
  assert.equal(parseProjectId('schema_version: 1\nproject_id: ' + PROJECT + '\nowner: local\n'), PROJECT);
  assert.throws(()=>parseProjectId('project_id: ../../tmp'), /Invalid/);
  assert.throws(()=>parseProjectId('project_id: ' + PROJECT + '\nproject_id: ' + PROJECT), /duplicated/);
});

test('setup diagnostics are an authenticated read-only call and validate shape', async () => {
  const urls=[];
  const report={profile:'local-safe',ready:true,integration_tested:false,next_action:'Run offline',checks:[]};
  const client=new LocalControlClient({baseUrl:'http://localhost:8765',token:TOKEN,
    fetchImpl:async (url,opts)=>{urls.push({url,opts});return {ok:true,json:async()=>report};}});
  assert.deepEqual(await client.setupStatus(),report);
  assert.equal(urls[0].url,'http://localhost:8765/api/setup/v1/status');
  assert.equal(urls[0].opts.method,'GET');
  assert.equal(urls[0].opts.redirect,'error');
  const invalid=new LocalControlClient({baseUrl:'http://localhost:8765',token:TOKEN,
    fetchImpl:async()=>({ok:true,json:async()=>({ready:true})})});
  await assert.rejects(invalid.setupStatus(),/Invalid local setup report/);
});
