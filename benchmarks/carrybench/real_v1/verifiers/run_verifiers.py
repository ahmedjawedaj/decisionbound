#!/usr/bin/env python3
"""Run the CarryBench-Real executable end-state checks and print a table.

Exit status is nonzero when any gold state fails, any wrong state passes, or a
do-nothing state passes a check that requires work. See carry_verifiers.py for
what these checks do and do not show.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import carry_verifiers as V  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    results = V.run()
    for r in results:
        flag = "ok " if r.ok else "BAD"
        print(f"{flag} {r.case_id:8} {r.label:9} {r.scenario:12} gold_pass={r.gold_pass} wrong_rejected={r.wrong_rejected} "
              f"noop_rejected={r.noop_rejected} extra_wrong={r.extra_wrong_rejected}/{r.extra_wrong_total} alt_gold={r.alt_gold_accepted}/{r.alt_gold_total}")
        if not r.ok:
            print("    gold problems:", r.gold_problems, "| wrong problems:", r.wrong_problems, "|", r.failures)
    cases = {r.case_id for r in results}
    noop_ok = sorted({f"{r.case_id}/{r.scenario}" for r in results if r.noop_rejected is None})
    print("scenarios that accept a do-nothing state by design:", ", ".join(noop_ok))
    print(json.dumps({"scenarios": len(results), "cases": len(cases), "extra_wrong_states": sum(r.extra_wrong_total for r in results),
                      "alternative_gold_states": sum(r.alt_gold_total for r in results), "all_ok": all(r.ok for r in results)}))
    return 0 if all(r.ok for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
