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
  const expected = ['decisionbound.connect','decisionbound.refresh',
                    'decisionbound.setupStatus','decisionbound.inspectSession',
                    'decisionbound.disconnect'];
  for (const command of expected) {
    assert.ok(commands.includes(command), `Command not registered in actual VS Code host: ${command}`);
  }
  assert.ok(!commands.includes('decisionbound.autoMerge'), 'Unsafe auto-merge command exposed');
  // Unique evidence line printed only after every assertion above passed. CI greps for it so a host run
  // that never reached the assertions cannot be reported as certified.
  process.stdout.write(`DBOUND_HOST_SMOKE_OK commands=${expected.length}\n`);
}
module.exports = {run};
