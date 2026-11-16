'use strict';
/**
 * Thin, deliberately read-only VS Code client. No agent code runs in the
 * extension host, and no model key, token or patch text enters extension logs.
 * Use vscode.SecretStorage for pairing and the core local runtime for state.
 */
const {LocalControlClient, parseProjectId} = require('./client');
const SECRET_KEY = 'decisionbound.localPairingToken';

/**
 * @param {any} vscode Injected extension host or deterministic test double.
 * @param {any} context VS Code extension context with SecretStorage.
 */
function createExtension(vscode, context) {
  const emitter = new vscode.EventEmitter();
  const output = vscode.window.createOutputChannel('DecisionBound');
  const status = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 10);
  status.text = '$(circle-outline) DecisionBound';
  status.command = 'decisionbound.refresh';
  status.show();

  let cached = [];
  let activeProject = null;
  let selectedSession = null;
  let lastEvent = 0;
  let selectionVersion = 0;
  let pollTimer = null;
  let eventPollVersion = null;
  let disposed = false;

  const provider = {
    onDidChangeTreeData: emitter.event,
    getTreeItem(item) { return item; },
    getChildren(element) {
      if (element) return [];
      return cached.map(session => {
        const item = new vscode.TreeItem(session.session_id, vscode.TreeItemCollapsibleState.None);
        item.description = session.state + ' · ' + session.task_count + ' tasks';
        item.tooltip = 'Read-only workflow ' + session.session_id;
        item.command = {command:'decisionbound.inspectSession', title:'Inspect session', arguments:[session.session_id]};
        return item;
      });
    }
  };

  function clearSelection() {
    selectionVersion++;
    selectedSession = null;
    lastEvent = 0;
    if (pollTimer !== null) clearInterval(pollTimer);
    pollTimer = null;
  }
  function report(error) {
    // Transport error messages are sanitized by LocalControlClient. Avoid raw
    // API bodies, task instructions, exceptions with request headers, and keys.
    vscode.window.showWarningMessage(error.message || 'DecisionBound operation failed');
  }
  async function client() {
    const token = await context.secrets.get(SECRET_KEY);
    if (!token) throw new Error('Pair your local DecisionBound runtime first');
    const url = vscode.workspace.getConfiguration('decisionbound').get('localUrl', 'http://127.0.0.1:8765');
    return new LocalControlClient({baseUrl: url, token});
  }
  async function workspaceProject() {
    if (!vscode.workspace.isTrusted) throw new Error('Trust this VS Code workspace before reading DecisionBound state');
    const folders = vscode.workspace.workspaceFolders || [];
    if (folders.length !== 1 || folders[0].uri.scheme !== 'file') {
      throw new Error('Open one local initialized repository as the workspace');
    }
    const manifest = vscode.Uri.joinPath(folders[0].uri, '.decisionbound', 'project.yaml');
    const data = await vscode.workspace.fs.readFile(manifest);
    return parseProjectId(Buffer.from(data).toString('utf8'));
  }
  async function refresh() {
    if (disposed) return;
    const version = selectionVersion;
    try {
      const project = await workspaceProject();
      const c = await client();
      await c.capabilities();
      const result = await c.sessions(project);
      if (disposed || version !== selectionVersion) return;
      if (activeProject !== project) clearSelection();
      activeProject = project;
      cached = result.sessions;
      status.text = '$(check) DecisionBound · ' + cached.length + ' sessions';
      emitter.fire();
    } catch (error) {
      if (disposed || version !== selectionVersion) return;
      cached = [];
      activeProject = null;
      clearSelection();
      status.text = '$(warning) DecisionBound';
      emitter.fire();
      report(error);
    }
  }
  async function inspect(sessionId) {
    const project = await workspaceProject();
    if (project !== activeProject) { await refresh(); throw new Error('Workspace changed; select a session again'); }
    clearSelection();
    const version = selectionVersion;
    const c = await client();
    const snapshot = await c.snapshot(project, sessionId);
    if (disposed || version !== selectionVersion) return;
    selectedSession = sessionId;
    output.clear();
    output.appendLine('Session ' + snapshot.workflow_id + ' · ' + snapshot.state);
    for (const task of snapshot.tasks) output.appendLine(task.task_id + ' · ' + task.role + ' · ' + task.state);
    output.appendLine('Task completion: NOT CERTIFIED');
    // Export review is independent of task verification and may be stale.
    const review = await c.review(project, sessionId);
    if (disposed || version !== selectionVersion) return;
    output.appendLine('Export review: ' + review.approval.decision +
      ' (revision ' + review.approval.revision + ') · evidence ' + (review.available ? 'inspectable' : 'unavailable'));
    output.show(true);
    await readEvents(project, sessionId, version);
    const intervalSeconds = Number(vscode.workspace.getConfiguration('decisionbound').get('pollSeconds', 5));
    const ms = Math.max(2000, Math.min(30000, intervalSeconds * 1000 || 5000));
    if (!disposed && version === selectionVersion) {
      pollTimer = setInterval(() => {
        if (disposed || version !== selectionVersion || selectedSession !== sessionId) return;
        readEvents(project, sessionId, version).catch(report);
      }, ms);
    }
  }
  async function readEvents(project, sessionId, version) {
    // Timer ticks never overlap for the same selected session. A new
    // workspace/selection may read immediately without waiting for an old tick.
    if (eventPollVersion === version) return;
    eventPollVersion = version;
    try {
      const c = await client();
      let pages = 0;
      let reviewChanged = false;
      let result;
      do {
        result = await c.events(project, sessionId, lastEvent);
        if (disposed || version !== selectionVersion || activeProject !== project) return;
        for (const event of result.events) {
          output.appendLine('#' + event.id + ' · ' + (event.task_id || 'workflow') + ' · ' + event.event);
          if (event.event === 'review_decision' || event.event === 'review_exported') reviewChanged = true;
        }
        lastEvent = result.next_after;
        pages++;
      } while (result.has_more && pages < 10);
      if (reviewChanged && !disposed && version === selectionVersion && activeProject === project) {
        const review = await c.review(project, sessionId);
        if (!disposed && version === selectionVersion && activeProject === project) {
          output.appendLine('Updated export review: ' + review.approval.decision +
            ' (revision ' + review.approval.revision + ') · evidence ' + (review.available ? 'inspectable' : 'unavailable'));
        }
      }
    } finally {
      if (eventPollVersion === version) eventPollVersion = null;
    }
  }
  async function setupStatus() {
    // Report only redacted prerequisite labels. A configured provider has not
    // been contacted, so never label it authenticated or certified.
    const c = await client();
    await c.capabilities();
    const result = await c.setupStatus();
    if (disposed) return;
    const state = result.ready ? 'Prerequisites present' : 'Setup incomplete';
    vscode.window.showInformationMessage('DecisionBound ' + result.profile + ': ' +
      state + '. ' + result.next_action);
  }
  async function pair() {
    const token = await vscode.window.showInputBox({
      title: 'DecisionBound local pairing token', password: true, ignoreFocusOut: true,
      prompt: 'Get your token with: dbound client pair --show-token. Stored in VS Code SecretStorage.'
    });
    if (token === undefined) return;
    if (token.length < 16 || /\s/.test(token)) throw new Error('Invalid pairing token');
    const url = vscode.workspace.getConfiguration('decisionbound').get('localUrl', 'http://127.0.0.1:8765');
    const c = new LocalControlClient({baseUrl:url, token});
    await c.capabilities();
    await context.secrets.store(SECRET_KEY, token);
    vscode.window.showInformationMessage('Paired with local DecisionBound runtime');
    await refresh();
  }
  async function disconnect() {
    clearSelection();
    await context.secrets.delete(SECRET_KEY);
    cached = [];
    activeProject = null;
    status.text = '$(circle-outline) DecisionBound';
    emitter.fire();
  }
  const registrations = [
    vscode.window.registerTreeDataProvider('decisionbound.sessions', provider),
    vscode.commands.registerCommand('decisionbound.connect', () => pair().catch(report)),
    vscode.commands.registerCommand('decisionbound.refresh', () => refresh()),
    vscode.commands.registerCommand('decisionbound.setupStatus', () => setupStatus().catch(report)),
    vscode.commands.registerCommand('decisionbound.inspectSession', id => inspect(id).catch(report)),
    vscode.commands.registerCommand('decisionbound.showChanges', () => { if (selectedSession) output.show(true); }),
    vscode.commands.registerCommand('decisionbound.disconnect', () => disconnect().catch(report)),
    vscode.workspace.onDidChangeWorkspaceFolders(() => { clearSelection(); cached=[]; activeProject=null; emitter.fire(); }),
    vscode.workspace.onDidGrantWorkspaceTrust(() => refresh())
  ];
  context.subscriptions.push(...registrations, status, output, emitter);
  const extension = {refresh, inspect, pair, disconnect, setupStatus, provider, get activeProject(){return activeProject;},
    dispose() {disposed=true;clearSelection();}};
  context.subscriptions.push(extension);
  return extension;
}

/** Production entry point for VS Code Extension Development Host. */
function activate(context) {
  const vscode = require('vscode');
  const extension = createExtension(vscode, context);
  extension.refresh();
  return extension;
}

function deactivate() {}
module.exports = {activate, deactivate, createExtension};
