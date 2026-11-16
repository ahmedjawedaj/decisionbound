#!/usr/bin/env python3
"""CI release gate: build, independently verify and recheck archive determinism."""
from __future__ import annotations

from pathlib import Path
import tempfile

from build_release import release
from verify_release import verify_source_archive, verify_pages_archive


def main() -> None:
    """Fail CI if build or verification fails on any supported OS."""
    with tempfile.TemporaryDirectory(prefix='dbound-release-check-') as directory:
        output = Path(directory)
        report = release(output, epoch=1794614400)
        source = verify_source_archive(report['source']['path'])
        pages = verify_pages_archive(report['pages']['path'])
        if source['version'] != report['version'] or not pages['verified']:
            raise RuntimeError('Release metadata mismatch')
        second = release(output, epoch=1794614400)
        if report['source']['sha256'] != second['source']['sha256'] or report['pages']['sha256'] != second['pages']['sha256']:
            raise RuntimeError('Rebuilt archives are not byte-for-byte reproducible')
        print(f'PASS: verified deterministic release {source["version"]} ({source["files"]} files)')


if __name__ == '__main__':
    main()
