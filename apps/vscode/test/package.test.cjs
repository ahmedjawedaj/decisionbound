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
