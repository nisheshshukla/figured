# Contributing

```bash
git clone https://github.com/nisheshshukla/figured
cd figured
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

CI runs ruff, mypy in strict mode, and pytest with a 95 percent coverage gate on Python 3.10 through 3.13.

`python benchmarks/bench.py` prints the time per check at several result-set sizes. A change that makes the typical case slower needs a reason in the pull request.

## Adding a case

Most behavior changes should start as a conformance vector in `tests/vectors/`. Each vector is a text, some rows, an optional policy, and what the report must say. Vectors are language-neutral so that ports in other languages can be held to the same behavior.

## Design rules

- No runtime dependencies. Optional integrations live behind extras.
- The check must stay deterministic: no model calls, no clock, no network inside `trace`.
- A false flag is worse than a miss. When in doubt, add a derivation or a tolerance knob rather than a stricter default.
- Every grounded figure must carry an explanation a person can verify by hand.

## Releasing

Releases are published to PyPI by the `release` workflow through trusted publishing, so no API token is stored anywhere.

One-time setup, by the repository owner:

1. On pypi.org, under your account's Publishing settings, add a pending publisher: project `figured`, owner `nisheshshukla`, repository `figured`, workflow `release.yml`, environment `pypi`.
2. In the GitHub repository settings, create an environment named `pypi`.

Each release:

1. Bump `version` in `pyproject.toml` and add a section to `CHANGELOG.md`.
2. Commit, then tag and push: `git tag v0.1.0 && git push origin main --tags`.

The workflow refuses to publish when the tag and the package version disagree.
