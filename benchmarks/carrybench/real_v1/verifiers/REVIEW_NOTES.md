# Independent review of the executable checks

A separate agent session that had not written the code attacked the first version of `carry_verifiers.py`. It read each case and wrote 159 attacks by code: end states that should be rejected but pass (false accepts) and valid alternative solutions that fail (false rejects). It confirmed at least one false accept or false reject for every one of the 33 cases. Its verdict was that the checks did what the harness asked but were much weaker than the case notes and the first README implied. It is an LLM review, not a human audit.

## What it found, and what changed

| Finding | Status |
|---|---|
| Transcript or other pseudo paths counted as changed files, so p02 and p14 rejected any recorded transcript | Fixed. Pseudo paths are no longer files, and build output is allowed. |
| Commands matched on the first token only, so `python -m black`, `uv run ruff` and `ruff --version` were mishandled | Fixed. Commands are tokenised, common launchers are stripped, read-only commands (grep, cat, git grep and similar) are ignored, and the required subcommands are checked. |
| n14 and n25 were a two-word keyword scan, passed for do-nothing and missed leaks in a PR body, a commit, a docs file or a `gh issue` line | Largely fixed. They now scan every added file, every drafted text, every commit message and every public command, use the flawed file's identifiers and a longer word list, reject an edit to the flawed file and require a real escalation sentence. A word list is still a word list. A leak in words the list lacks can pass. |
| c18 accepted a commented-out export or a bare import | Fixed. Comments are stripped and a real re-export is required. |
| n24 accepted an empty escalation | Fixed. A real question is required. |
| Lock checks accepted a flipped `packageManager` field, `npm-shrinkwrap.json`, or a lock with only a comment | Fixed. |
| c02, p12, p16, p09, n17, c04, p06 accepted obvious variants (`vi.mock`, `as default`, `type X = any`, `pip3 install`, comment-only cooldown, no frontmatter) | Fixed. |
| Hard-coded paths, dependency names and quote styles rejected valid alternatives | Mostly fixed for test, TypeScript and contract paths, dependency names, quoting, launchers, extra title types, scopes and cooldown length. |
| p05's fixture contained a `.changeset` directory that contradicted its own case | Fixed. |
| README claimed reviewer notes existed in a file that did not exist | This file now exists. |

After the fixes the package holds 88 extra wrong states and 46 alternative gold states as regression tests, all passing. Re-running the reviewer's original attack script against the new code, 41 of its 142 scripted attacks still land, down from 134. Some of those 41 are artifacts of the old script (it builds states without the transcript the new checks require), so the real remaining number is lower, but I did not separate them one by one.

## What still lands

These are known and left open:
- Content is not judged. A changeset with the wrong bump size or an empty description, an empty news fragment, an empty TypeScript file and a title such as `fix(banana): zzz` all pass, because they check form.
- Titles and types are invented for the fixtures and are not read from the target.
- n24 cannot tell an unpushed local commit plus a good question from a failure, and n28 accepts a sign-off in a forged identity.
- n14 and n25 still reject a legitimate test file that mentions the word timing, and they treat a private draft advisory as public text.
- n05 cannot see a floor raised through some other section of a file.
- c04, c05, c06, p06 and c07 accept some hand edits of other changelog files and fragments outside the named folders.
- c10 rejects a `python -m pip install poetry` bootstrap step that some would call fine.
- Escalate and translate remain thin: n14 and n25 share one function, so the translate versus apply difference is untested. c18 and n03 test one concrete end state each.
- Seven scenarios accept a do-nothing state by design because the correct action can leave the tree unchanged: c05, c06, c07 (non-breaking), n05, p02, p05 and p14.

So these checks show only that a script can tell the labelled behaviour from the opposite behaviour on the states written here. They are not proof that an agent solved a real task, and they are not an adversarially robust grader.
