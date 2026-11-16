"""Run the Sprint 04 coding workflow without network, Docker, or user files.

The fake test demonstrates lifecycle and integrity checks but **never** proves
real test success or completion. Execute using a wheel or editable install:
``python examples/mock_patch_agent.py``.
"""
from __future__ import annotations
import json
import subprocess
import tempfile
from pathlib import Path
from decisionbound.code_execution import PatchFileProposer, run_coding_task
from decisionbound.code_sandbox import ScriptedTestRunner
from decisionbound.identity import init_project
from decisionbound.storage import SQLiteRepository

PATCH = '''diff --git a/answer.py b/answer.py
--- a/answer.py
+++ b/answer.py
@@ -1 +1 @@
-ANSWER = 41
+ANSWER = 42
'''


def main() -> None:
    """Create a temporary Git repository and demonstrate isolated mock testing."""
    with tempfile.TemporaryDirectory(prefix='dbound-code-demo-') as scratch:
        base = Path(scratch)
        project = base / 'project'
        project.mkdir()
        def git(*args: str) -> None:
            subprocess.run(['git', '-C', str(project), *args], check=True,
                           capture_output=True, text=True)
        git('init', '-q')
        git('config', 'user.name', 'Demo')
        git('config', 'user.email', 'demo@example.invalid')
        (project / 'answer.py').write_text('ANSWER = 41\n', encoding='utf-8')
        git('add', 'answer.py')
        git('commit', '-qm', 'baseline')
        state = base / 'state'
        store = SQLiteRepository(state / 'state.sqlite3')
        init_project(project, store, technologies=('python',))
        def inspect(workspace, command):
            return 0 if (Path(workspace.path) / 'answer.py').read_text() == 'ANSWER = 42\n' else 1
        result = run_coding_task(project_path=project, state_root=state, repository=store,
                                 task='Change the answer to 42', proposer=PatchFileProposer(PATCH),
                                 runner=ScriptedTestRunner(checker=inspect),
                                 test_commands=(('mock', 'check-answer'),), approved_writes=True)
        print(json.dumps(result.to_dict(), indent=2))
        assert (project / 'answer.py').read_text() == 'ANSWER = 41\n'
        assert result.acceptance_verified is False
        print('Original checkout untouched. Mock evidence is not real acceptance.')


if __name__ == '__main__':
    main()
