# Package releases

DecisionBound is an experimental alpha. A published package is a distribution
checkpoint, not independent security review or human validation of CarryBench.

The maintainer publishes through `.github/workflows/publish-pypi.yml`, dispatched
from `main` with an explicit existing version tag. The tag must match
`pyproject.toml`, point into `main`, and have successful main runs of runtime CI,
release readiness and platform certification. Missing, running or failed checks
stop publication. The workflow builds wheel and source distributions, checks
their metadata and installs the wheel in a fresh environment before uploading.

PyPI Trusted Publishing must be configured for exactly:

| Setting | Value |
| --- | --- |
| PyPI project | `decisionbound` |
| Repository owner | `ahmedjawedaj` |
| Repository | `decisionbound` |
| Workflow filename | `publish-pypi.yml` |
| GitHub environment | `pypi` |

The `pypi` environment should allow only `main` and require maintainer approval.
Do not store a long-lived PyPI token in this repository. Only the final upload
job receives permission to request the short-lived publishing identity. External
actions in this workflow are pinned to commit hashes.

Review the public file manifest and the actual distribution contents before the
first publication. Keep article drafts, annotation working files, chat handoffs,
private state and raw execution logs outside the source checkout. Publish only
tested source commits; future sprints follow the same pull request and CI gates.
Never overwrite a version tag or reuse an already published package version.
