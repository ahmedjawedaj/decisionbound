#!/usr/bin/env python3
"""Copy canonical skills into Claude and Codex project directories."""
from pathlib import Path
import shutil
ROOT=Path(__file__).resolve().parents[1]
for source in (ROOT/'skills').iterdir():
    if not source.is_dir() or not (source/'SKILL.md').exists():
        continue
    for agent in ('.claude/skills','.agents/skills'):
        dest=ROOT/agent/source.name
        if dest.exists(): shutil.rmtree(dest)
        shutil.copytree(source,dest)
print('Synchronized canonical skills to Claude and Codex.')
