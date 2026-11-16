# Annotator A notes (AI annotator, blind)

- verify: 83 pointers, 0 mismatches.
- cbh-006: config pointer says line 330 but the pydocstyle convention is at line 343 of pyproject.toml. Text hash is null for config facts so verify cannot catch it. Only D101 is selected in ruff, so the Google convention is declared but barely enforced.
- cbh-014, cbh-020, cbh-023, cbh-026, cbh-004, cbh-031: the pinned target evidence is path_present only. I read nearby files (SECURITY.md, root agent files, contributing guides) to decide. By R5a those pointers alone would be weak structure.
- cbh-030: the pinned lines describe ship/show auto-approval, but lines 95-97 of the same file say fork PRs are never auto-approved. Reading only the pinned lines would point the wrong way.
- Mixed spans (1, 17, 18, 21): I used the listed scope_conditions to pick the atom, since the paraphrase names both. No case marked invalid.
- Guide tension: R4 (scope condition fails, abstain whatever prose says) versus R2 (recording a change is a practice, translate). Case 21 hits both. I followed R4 by the letter.
- Guide gap: R3/R3a cover AI and contributor policy, but security reporting and licence headers fall under Q1 only. I treated same-position-different-channel as translate (11) and unclear as escalate (33, 26).
