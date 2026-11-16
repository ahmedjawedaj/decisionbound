'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const {createExtension} = require('../src/extension');
const PROJECT = 'prj_' + 'a'.repeat(32);
const NEXT = 'prj_' + 'b'.repeat(32);
const SESSION = 'wf_' + 'c'.repeat(32);
const TOKEN = 'secret-token-in-extension-storage';

function harness() {
  let trusted = true;
  let project = PROJECT;
  const commands = {};
  const secrets = new Map();
  const messages = [];
  const output = [];
  const disposables = [];
  const context = {subscriptions:disposables,secrets:{
    get:async k=>secrets.get(k),store:async(k,v)=>secrets.set(k,v),delete:async k=>secrets.delete(k)}};
  class EventEmitter {constructor(){this.event=()=>{};}fire(){}dispose(){}}
  class TreeItem {constructor(label){this.label=label;}}
  const vscode = {
    EventEmitter,TreeItem, TreeItemCollapsibleState:{None:0},StatusBarAlignment:{Left:1},
    Uri:{joinPath:(root,...parts)=>({scheme:'file',path:root.path + '/' + parts.join('/')})},
    workspace:{get isTrusted(){return trusted}, workspaceFolders:[{uri:{scheme:'file',path:'/tmp/repo'}}],
      fs:{readFile:async()=>Buffer.from('schema_version: 1\nproject_id: ' + project + '\n')},
      getConfiguration:()=>({get:(key, fallback)=> key==='localUrl'?'http://127.0.0.1:8765':fallback}),
      onDidChangeWorkspaceFolders:fn=>{vscode.workspace.onSwitch=fn;return {dispose(){}}},
      onDidGrantWorkspaceTrust:()=>({dispose(){}})},
    window:{showInputBox:async()=>TOKEN,showWarningMessage:s=>messages.push(s),
      showInformationMessage:s=>messages.push(s),
      createStatusBarItem:()=>({show(){},dispose(){},set text(v){this.current=v},get text(){return this.current}}),
      createOutputChannel:()=>({appendLine:s=>output.push(s),clear(){output.length=0},show(){},dispose(){}}),
      registerTreeDataProvider:(_, p)=>{vscode.provider=p;return {dispose(){}}}},
    commands:{registerCommand:(name,fn)=>{commands[name]=fn;return {dispose(){}}}},
  };
  return {vscode,context,commands,secrets,messages,output,setTrusted:v=>trusted=v,
    setProject:v=>{project=v;vscode.workspace.onSwitch();}};
}

function fetcher(url, options) {
  assert.equal(options.redirect,'error');
  assert.equal(options.headers.Authorization,'Bearer ' + TOKEN);
  if (url.includes('capabilities')) return Promise.resolve({ok:true,json:async()=>({
    protocol:'decisionbound-local-client',version:1,transport:'authenticated-loopback-http'})});
  if (url.includes('/events')) return Promise.resolve({ok:true,json:async()=>({
    events:[{id:1,task_id:null,event:'created'}],next_after:1,has_more:false})});
  if (url.includes('/review?')) return Promise.resolve({ok:true,json:async()=>({approval:{decision:'pending',revision:0},available:false})});
  if (url.includes('/sessions/')) return Promise.resolve({ok:true,json:async()=>({
    workflow_id:SESSION,state:'running',tasks:[{task_id:'inspect',state:'pending',role:'researcher'}]})});
  if (url.includes('/sessions?')) return Promise.resolve({ok:true,json:async()=>({
    sessions:[{session_id:SESSION,state:'running',task_count:1}]})});
  throw new Error('Unexpected URL');
}

test('real extension entry wiring uses SecretStorage and renders shared sessions', async () => {
  const h = harness();
  const priorFetch = globalThis.fetch;
  globalThis.fetch=fetcher;
  let ext;
  try {
    ext=createExtension(h.vscode,h.context);
    await h.commands['decisionbound.connect']();
    assert.equal(h.secrets.get('decisionbound.localPairingToken'),TOKEN);
    assert.equal(ext.activeProject, PROJECT);
    assert.equal(h.vscode.provider.getChildren().length,1);
    assert.equal(h.vscode.provider.getChildren()[0].label,SESSION);
    await h.commands['decisionbound.inspectSession'](SESSION);
    assert.ok(h.output.join('\n').includes('Task completion: NOT CERTIFIED'));
    assert.ok(h.output.join('\n').includes('created'));
    assert.ok(!h.output.join('\n').includes(TOKEN));
    await h.commands['decisionbound.disconnect']();
    assert.equal(h.secrets.size,0);
    assert.equal(h.vscode.provider.getChildren().length,0);
  } finally {ext?.dispose();globalThis.fetch=priorFetch;}
});

test('untrusted workspace cannot read local project or sessions', async () => {
  const h=harness();
  h.setTrusted(false);
  h.secrets.set('decisionbound.localPairingToken',TOKEN);
  const ext=createExtension(h.vscode,h.context);
  try {
    await ext.refresh();
    assert.equal(ext.activeProject,null);
    assert.ok(h.messages.some(message=>message.includes('Trust this VS Code workspace')));
  } finally {ext.dispose();}
});

test('workspace switches invalidate selected project and cached sessions', async () => {
  const h=harness();
  h.secrets.set('decisionbound.localPairingToken',TOKEN);
  const priorFetch=globalThis.fetch;globalThis.fetch=fetcher;
  const ext=createExtension(h.vscode,h.context);
  try {
    await ext.refresh();
    assert.equal(ext.activeProject, PROJECT);
    h.setProject(NEXT);
    assert.equal(ext.activeProject,null);
    assert.equal(h.vscode.provider.getChildren().length,0);
    await ext.refresh();
    assert.equal(ext.activeProject,NEXT);
  } finally {ext.dispose();globalThis.fetch=priorFetch;}
});

test('approval change is displayed from canonical event and review revision', async () => {
  const h = harness();
  h.secrets.set('decisionbound.localPairingToken',TOKEN);
  const priorFetch = globalThis.fetch;
  globalThis.fetch = async (url,opts) => {
    if (url.includes('capabilities')) return {ok:true,json:async()=>({
      protocol:'decisionbound-local-client',version:1,transport:'authenticated-loopback-http'})};
    if (url.includes('/events')) return {ok:true,json:async()=>({events:[
      {id:4,task_id:null,event:'review_decision'}],next_after:4,has_more:false})};
    if (url.includes('/review?')) return {ok:true,json:async()=>({
      approval:{decision:'approve_export',revision:1},available:true})};
    if (url.includes('/sessions/')) return {ok:true,json:async()=>({
      workflow_id:SESSION,state:'blocked',tasks:[]})};
    return {ok:true,json:async()=>({sessions:[{session_id:SESSION,state:'blocked',task_count:1}]})};
  };
  const ext=createExtension(h.vscode,h.context);
  try {
    await ext.refresh();
    await ext.inspect(SESSION);
    const text=h.output.join('\n');
    assert.ok(text.includes('Updated export review: approve_export (revision 1)'));
    assert.ok(!text.includes(TOKEN));
  } finally {ext.dispose();globalThis.fetch=priorFetch;}
});

test('setup readiness reports prerequisites but never credentials or certification', async () => {
  const h=harness();
  h.secrets.set('decisionbound.localPairingToken',TOKEN);
  const original=globalThis.fetch;
  globalThis.fetch=async url => {
    if(url.includes('capabilities'))return {ok:true,json:async()=>({
      protocol:'decisionbound-local-client',version:1,transport:'authenticated-loopback-http'})};
    if(url.includes('/setup/v1/status'))return {ok:true,json:async()=>({
      profile:'openrouter',ready:false,integration_tested:false,
      next_action:'Set OPENROUTER_API_KEY in your environment',checks:[]})};
    throw Error('unexpected');
  };
  const ext=createExtension(h.vscode,h.context);
  try{
    await h.commands['decisionbound.setupStatus']();
    assert.ok(h.messages.some(m=>m.includes('Setup incomplete')));
    assert.ok(h.messages.every(m=>!m.includes(TOKEN)));
  }finally{ext.dispose();globalThis.fetch=original;}
});
