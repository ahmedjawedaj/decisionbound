'use strict';
/** Connected CI entry. Downloads a genuine VS Code host via @vscode/test-electron.
 * Not used by npm test; npm test always remains offline and deterministic.
 */
const path = require('node:path');
const {runTests} = require('@vscode/test-electron');
const {describe, runWithRetry} = require('./retry.cjs');
(async () => {
  const extensionDevelopmentPath = path.resolve(__dirname, '../..');
  const extensionTestsPath = path.resolve(__dirname, 'smoke.cjs');
  const args = ['--disable-workspace-trust', '--skip-welcome', '--skip-release-notes'];
  // Looking up and downloading the host depends on an external service, so a throw (not a failing test run) is retried.
  const exitCode = await runWithRetry(() => runTests({extensionDevelopmentPath, extensionTestsPath, launchArgs:args}), {log: text => process.stderr.write(text)});
  // Our own evidence line. CI used to depend on a line that VS Code itself prints, which only appears
  // under some environments, so the gate could not be checked outside CI. This one is written whenever runTests returns. If runTests throws, nothing is written and the gate fails on the missing marker.
  process.stdout.write(`DBOUND_HOST_RUNNER_EXIT code=${exitCode}\n`);
  if (exitCode !== 0) process.exitCode = 1;
})().catch(error=>{process.stderr.write('VS Code host smoke failed: '+describe(error)+'\n');process.exitCode=1;});
