# Public roadmap

DecisionBound is an alpha. It has local decision records, bounded source inspection, SQLite workflows, consent-bound coding and review, and local developer clients. This page lists what is open, grouped by how well understood each item is. No dates are promised and nothing here is a production-readiness claim.

The issue tracker is the source of truth. Each item below is an issue, and the milestones on GitHub use the same three headings. Issue titles below are copied from the tracker and may have changed since this page was last edited.

## Goals

These are the aims behind the list below. They are aims, not forecasts.

- Keep DecisionBound open source under Apache-2.0, with the tracker, tests and docs public.
- Do not make a research claim until human annotators have labelled the benchmark. All labels so far come from AI annotators of one model family (see #26).
- Keep a small, honest alpha. Prefer closing verified gaps over adding features.
- Place the work against related research and say plainly what it does and does not show. See the related work section on the research page.

## Near term

Small items that are well understood.

- [#19](https://github.com/ahmedjawedaj/decisionbound/issues/19) Decide whether DockerTestRunner should refuse a root caller. Needs a maintainer decision.
- [#20](https://github.com/ahmedjawedaj/decisionbound/issues/20) Docker mount probe cannot detect a bind of a tmpfs-backed host directory.
- [#21](https://github.com/ahmedjawedaj/decisionbound/issues/21) CarryBench config pointers can have no text hash, so a wrong line (cbh-006) passes verify.

## Next

Verification and design work that needs real effort.

- [#22](https://github.com/ahmedjawedaj/decisionbound/issues/22) Make CodingRepairWorker killable like contained preparation.
- [#23](https://github.com/ahmedjawedaj/decisionbound/issues/23) Run the model provider adapters against a real provider. Today the adapters are only tested with mocked HTTP.
- [#24](https://github.com/ahmedjawedaj/decisionbound/issues/24) Test annotation guide v1.2 on a fresh unlabelled case set.
- [#25](https://github.com/ahmedjawedaj/decisionbound/issues/25) Run and record a 24 to 72 hour soak of the durable service.
- [#33](https://github.com/ahmedjawedaj/decisionbound/issues/33) Write down the fixed permission limits and the test behind each one.

## Needs outside help

Blocked on a human, another vendor or an external reviewer.

- [#26](https://github.com/ahmedjawedaj/decisionbound/issues/26) Get human annotations for the 35 CarryBench holdout cases. No human has labelled any case so far.
- [#27](https://github.com/ahmedjawedaj/decisionbound/issues/27) Score CarryBench with a model from another vendor.
- [#28](https://github.com/ahmedjawedaj/decisionbound/issues/28) Independent security review of worker isolation and the Docker sandbox.

## Further out

These are not started and have no issue yet. Distributed coordination, multi-host recovery, remote multi-tenant authentication, and operation at fleet scale. The current queue is SQLite on one machine and is not a distributed system.

## How to help

Items labelled `help wanted` on the tracker are the best place to start. The one item labelled `good first issue` (#21) still needs the pinned clones and the verifier in `benchmarks/carrybench`. For anything that touches security, use the private route in `SECURITY.md`. See `CONTRIBUTING.md` for how to open a pull request.
