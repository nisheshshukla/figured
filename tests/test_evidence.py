from __future__ import annotations

from decimal import Decimal

from figured import build_evidence
from figured.evidence import to_number


class FakeFrame:
    columns = ["state", "pop"]

    def to_dict(self, orient: str) -> list[dict[str, object]]:
        assert orient == "records"
        return [{"state": "CA", "pop": 39346023}, {"state": "TX", "pop": 28635442}]


class FakeCursor:
    description = [("STATE", None), ("POP", None)]

    def fetchall(self) -> list[tuple[object, ...]]:
        return [("CA", Decimal("39346023")), ("TX", Decimal("28635442"))]


def test_list_of_dicts_keeps_column_names_and_labels() -> None:
    ev = build_evidence([{"state": "CA", "pop": 39346023}])
    cell = ev.cells[0]
    assert cell.column == "pop" and cell.label == "CA" and cell.value == 39346023


def test_list_of_lists_gets_generated_columns() -> None:
    ev = build_evidence([["CA", 1.0, 2.0]])
    assert [c.column for c in ev.cells] == ["c1", "c2"]


def test_columns_and_rows_mapping() -> None:
    ev = build_evidence({"columns": ["state", "pop"], "rows": [["CA", 1.0]]})
    assert ev.cells[0].column == "pop"


def test_dataframe_duck_typing() -> None:
    ev = build_evidence(FakeFrame())
    assert [c.value for c in ev.cells] == [39346023.0, 28635442.0]


def test_cursor_duck_typing_with_decimals() -> None:
    ev = build_evidence(FakeCursor())
    assert ev.results[0].columns == ["STATE", "POP"] and ev.cells[1].value == 28635442.0
    assert ev.results[0].rows[1][0].label == "TX"


def test_scalars_and_multiple_result_sets() -> None:
    ev = build_evidence([1.0, 2.0], results=[[["x", 3.0]]])
    assert [c.value for c in ev.cells] == [1.0, 2.0, 3.0]
    assert {c.result for c in ev.cells} == {0, 1}


def test_to_number_rules() -> None:
    assert to_number(True) is None
    assert to_number(None) is None
    assert to_number(float("nan")) is None
    assert to_number("12%") == 12.0
    assert to_number("$1,234.5") == 1234.5
    assert to_number("1e3") == 1000.0
    assert to_number("Harris County") is None
    assert to_number("1,234", parse_strings=False) is None
    assert to_number(Decimal("2.5")) == 2.5


def test_empty_inputs() -> None:
    assert build_evidence(None).empty
    assert build_evidence([]).empty
