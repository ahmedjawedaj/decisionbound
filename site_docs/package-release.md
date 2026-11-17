# Package releases

DecisionBound is an experimental alpha. A published package is a distribution
checkpoint, not independent security review or human validation of CarryBench.

Publishing a GitHub release automatically starts the package release pipeline.
Both stable releases and prereleases are supported; saving a draft or pushing
ordinary commits does not publish a package.

1. Update `pyproject.toml`, `src/decisionbound/__init__.py` and `CITATION.cff` to
   the same new version, such as `0.13.0a2`, through a tested pull request.
2. Publish a GitHub release with the matching tag, such as `v0.13.0a2`, on `main`.
3. `.github/workflows/release-published.yml` automatically dispatches
   `publish-pypi.yml` from protected `main` with that tag. No second manual
   upload or approval is required.
4. The trusted main workflow checks the tag is on main, all three version
   declarations match, and the version is absent from PyPI. It waits up to two
   hours for successful runtime CI, release readiness and platform certification
   on the exact source commit. Failed checks or a timeout stop publication.
5. It checks out that certified commit, builds wheel and source distributions,
   checks package metadata and smoke-tests a clean wheel installation. The
   final job uploads both distributions using PyPI Trusted Publishing.

Each distinct version has its own concurrency group, so releasing another
version does not cancel a queued release. A version already on PyPI is rejected;
never overwrite a tag or reuse a version. This pipeline does not invent version
numbers or publish every source push. The GitHub release is the publication
decision. Fix a failed release's cause and rerun its publishing workflow; use a
new version if that version has already reached PyPI.

The manual recovery path remains available: dispatch `publish-pypi.yml` from
`main` with the existing version tag. It performs the same checks.
If a future Actions workflow creates releases using `GITHUB_TOKEN`, that workflow
must dispatch the publisher itself: GitHub suppresses release events created by
that token. The `workflow_dispatch` event used here is an explicit exception.

PyPI Trusted Publishing must be configured for exactly:

| Setting | Value |
| --- | --- |
| PyPI project | `decisionbound` |
| Repository owner | `ahmedjawedaj` |
| Repository | `decisionbound` |
| Workflow filename | `publish-pypi.yml` |
| GitHub environment | `pypi` |

The `pypi` environment allows only `main`, without a second reviewer prompt.
Release-event jobs run at tags, so the dispatcher has no PyPI identity permission
and requests the upload through the protected main workflow. The guard itself
is read from trusted main before any tagged build code executes. Only the final
upload job receives permission to request the short-lived publishing identity.
Do not store a long-lived PyPI token in this repository. External actions in the
upload workflow are pinned to commit hashes.

See the official [GitHub release event documentation](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#release),
[workflow dispatch behavior](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/trigger-a-workflow#triggering-a-workflow-from-a-workflow)
and [PyPI Trusted Publishing security model](https://docs.pypi.org/trusted-publishers/security-model/).

Review the public file manifest and the actual distribution contents before the
first publication. Keep article drafts, annotation working files, chat handoffs,
private state and raw execution logs outside the source checkout. Publish only
tested source commits; future sprints follow the same pull request and CI gates.
