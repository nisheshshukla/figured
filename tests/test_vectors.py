"""Language-neutral conformance vectors. A port in another language must pass these unchanged."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from figured import trace

VECTOR_DIR = Path(__file__).parent / "vectors"
VECTORS = json.loads((VECTOR_DIR / "core.json").read_text())


@pytest.mark.parametrize("vec", VECTORS, ids=[v["name"] for v in VECTORS])
def test_vector(vec: dict) -> None:  # type: ignore[type-arg]
    report = trace(vec["text"], vec.get("rows"), results=vec.get("results"), **vec.get("policy", {}))
    expect = vec["expect"]
    assert report.ok == expect["ok"], report.explain()
    assert report.ungrounded == expect["ungrounded"], report.explain()
    if "checked" in expect:
        assert report.checked == expect["checked"], report.explain()
    for literal, kind in expect.get("kinds", {}).items():
        hit = next(r for r in report.results if r.literal == literal)
        assert hit.match is not None and hit.match.kind == kind, report.explain()
