'use strict';
/** Runs *inside* the downloaded VS Code Extension Development Host.
 * No remote model, authentication tokens, or production repositories.
 */
const assert = require('node:assert/strict');
const vscode = require('vscode');

async function run() {
  const extension = vscode.extensions.getExtension('decisionbound-local.decisionbound-local');
  assert.ok(extension, 'DecisionBound contribution absent from real VS Code host');
  const api = await extension.activate();
  assert.ok(api && typeof api.refresh === 'function' && typeof api.setupStatus === 'function',
    'Extension did not activate the actual exported runtime');
  const commands = await vscode.commands.getCommands(true);
  for (const command of ['decisionbound.connect','decisionbound.refresh',
                          'decisionbound.setupStatus','decisionbound.inspectSession',
                          'decisionbound.disconnect']) {
    assert.ok(commands.includes(command), `Command not registered in actual VS Code host: ${command}`);
  }
  assert.ok(!commands.includes('decisionbound.autoMerge'), 'Unsafe auto-merge command exposed');
}
module.exports = {run};
