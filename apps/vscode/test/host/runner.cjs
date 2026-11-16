'use strict';
/** Connected CI entry. Downloads a genuine VS Code host via @vscode/test-electron.
 * Not used by npm test; npm test always remains offline and deterministic.
 */
const path = require('node:path');
const {runTests} = require('@vscode/test-electron');
(async () => {
  const extensionDevelopmentPath = path.resolve(__dirname, '../..');
  const extensionTestsPath = path.resolve(__dirname, 'smoke.cjs');
  const args = ['--disable-workspace-trust', '--skip-welcome', '--skip-release-notes'];
  await runTests({extensionDevelopmentPath, extensionTestsPath, launchArgs:args});
})().catch(error=>{process.stderr.write('VS Code host smoke failed: '+error.message+'\n');process.exitCode=1;});
