from __future__ import annotations

import json
from pathlib import Path

from figured.__main__ import main


def test_cli_passes_and_fails(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    rows = tmp_path / "rows.json"
    rows.write_text(json.dumps([{"state": "CA", "pop": 39346023}]))
    assert main(["California has 39.3 million people.", "--rows", str(rows)]) == 0
    assert "✓" in capsys.readouterr().out
    assert main(["California has 50 million people.", "--rows", str(rows), "--json"]) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["ungrounded"] == ["50 million"]


def test_cli_flags(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    rows = tmp_path / "rows.json"
    rows.write_text(json.dumps([{"pop": 1000000}]))
    assert main(["About 12% of people.", "--rows", str(rows)]) == 1
    assert main(["About 12% of people.", "--rows", str(rows), "--allow-unmatched-percent"]) == 0
    assert main(["1,000,000 and 3,000,000 people.", "--rows", str(rows), "--derivations", "all"]) == 1
    capsys.readouterr()
    assert main(["1,010,000 people.", "--rows", str(rows), "--tolerance", "0.02"]) == 0
