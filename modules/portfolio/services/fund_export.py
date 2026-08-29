"""Fund intelligence audit workbook."""

from __future__ import annotations

from io import BytesIO
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Font


def build_fund_workbook(*, schemes: list[dict[str, Any]], constituents: list[dict[str, Any]], overlaps: list[dict[str, Any]]) -> BytesIO:
    workbook = Workbook()
    workbook.remove(workbook.active)
    _sheet(workbook, "Schemes", schemes)
    _sheet(workbook, "Constituents", constituents)
    _sheet(workbook, "Overlap", overlaps)
    output = BytesIO()
    workbook.save(output)
    output.seek(0)
    return output


def _sheet(workbook: Workbook, title: str, rows: list[dict[str, Any]]) -> None:
    sheet = workbook.create_sheet(title)
    if not rows:
        sheet.append(["No data"])
        return
    columns = sorted({key for row in rows for key in row})
    sheet.append(columns)
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    for row in rows:
        sheet.append([str(row.get(column)) if isinstance(row.get(column), (dict, list)) else row.get(column) for column in columns])
    sheet.freeze_panes = "A2"
