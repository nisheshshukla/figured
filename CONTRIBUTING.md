# Contributing

```bash
git clone https://github.com/nisheshshukla/figured
cd figured
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

CI runs ruff, mypy in strict mode, and pytest with a 95 percent coverage gate on Python 3.10 through 3.13.

## Adding a case

Most behavior changes should start as a conformance vector in `tests/vectors/`. Each vector is a text, some rows, an optional policy, and what the report must say. Vectors are language-neutral so that ports in other languages can be held to the same behavior.

## Design rules

- No runtime dependencies. Optional integrations live behind extras.
- The check must stay deterministic: no model calls, no clock, no network inside `trace`.
- A false flag is worse than a miss. When in doubt, add a derivation or a tolerance knob rather than a stricter default.
- Every grounded figure must carry an explanation a person can verify by hand.
