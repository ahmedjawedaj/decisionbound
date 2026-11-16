'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const manifest = JSON.parse(fs.readFileSync(path.join(root, 'package.json'), 'utf8'));

test('installable manifest has a real entry point and no write authority', () => {
  assert.ok(fs.existsSync(path.join(root, manifest.main)));
  const pyproject = fs.readFileSync(path.resolve(__dirname, '../../../pyproject.toml'), 'utf8');
  const match = pyproject.match(/^version\s*=\s*"(\d+\.\d+\.\d+)a(\d+)"/m);
  assert.ok(match, 'Python distribution must have an alpha version');
  assert.equal(manifest.version, match[1] + '-alpha.' + match[2],
    'VS Code extension and installed Python distribution versions differ');
  assert.ok(manifest.engines.vscode);
  assert.ok(manifest.contributes.views.explorer.some(v => v.id === 'decisionbound.sessions'));
  const ids = manifest.contributes.commands.map(x => x.command);
  assert.ok(ids.includes('decisionbound.setupStatus'));
  assert.ok(!ids.some(x => /merge|deploy|executeCode|approveExport/i.test(x)));
});

test('real host smoke harness is deliberately separate from mock host tests', () => {
  assert.ok(fs.existsSync(path.join(root,'test/host/runner.cjs')));
  assert.ok(fs.existsSync(path.join(root,'test/host/smoke.cjs')));
  assert.ok(manifest.scripts['test:host'].includes('host/runner'));
});

test('VSIX ignore rules keep dependencies, host downloads and old builds out of the package', () => {
  const rules = fs.readFileSync(path.join(root, '.vscodeignore'), 'utf8').split(/\r?\n/).map(s => s.trim());
  for (const needed of ['node_modules/**', '.vscode-test/**', 'dist/**', 'test/**']) {
    assert.ok(rules.includes(needed), '.vscodeignore must list ' + needed);
  }
  assert.equal(Object.keys(manifest.dependencies || {}).length, 0,
    'the extension must stay dependency free, so nothing from node_modules needs to ship');
});
