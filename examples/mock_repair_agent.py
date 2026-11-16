"""Reproducible offline Sprint 05 integration example; never contacts models.

Run `python examples/mock_repair_agent.py`. It creates its own temporary Git
repository, fails one fake test, repairs with a second patch, and checks that
no original source files were changed. Passing mock tests are *not* verified.
"""
from __future__ import annotations
import json
import subprocess
import tempfile
from pathlib import Path
from decisionbound.code_acceptance import TaskContract, TextPredicate
from decisionbound.code_repair import RepairLimits, ScriptedRepairFactory, run_repair_session
from decisionbound.code_sandbox import ScriptedTestRunner
from decisionbound.identity import init_project
from decisionbound.storage import SQLiteRepository

PATCH = '''diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1 +1 @@
-VALUE = 1
+VALUE = 2
'''


def git(root: Path, *args: str) -> None:
    subprocess.run(['git', '-C', str(root), *args], check=True, capture_output=True)


def main() -> None:
    with tempfile.TemporaryDirectory(prefix='decisionbound-repair-demo-') as directory:
        root = Path(directory) / 'project'; root.mkdir()
        state = Path(directory) / 'state'
        store = SQLiteRepository(state / 'db.sqlite3')
        git(root, 'init', '-q')
        git(root, 'config', 'user.email', 'test@example.invalid')
        git(root, 'config', 'user.name', 'Test')
        (root / 'app.py').write_text('VALUE = 1\n')
        git(root, 'add', 'app.py'); git(root, 'commit', '-qm', 'initial')
        init_project(root, store, 'example', ('python',))
        contract = TaskContract('offline-example', ('app.py',), ('app.py',),
            (('python', '-V'),), (TextPredicate('app.py', contains='VALUE = 2'),))
        result = run_repair_session(idempotency_key='offline-demo-001', project_path=root,
             state_root=state, repository=store, task='Update VALUE to 2', contract=contract,
             proposer_factory=ScriptedRepairFactory((PATCH, PATCH)),
             runner=ScriptedTestRunner([1, 0]),
             limits=RepairLimits(max_attempts=2), source_targets=('app.py',), approved_writes=True)
        assert result.status == 'needs_real_tests'
        assert (root / 'app.py').read_text() == 'VALUE = 1\n'
        print(json.dumps(result.to_dict(), indent=2))
        print('PASS: mock repair completed safely; real verification still pending')


if __name__ == '__main__':
    main()
