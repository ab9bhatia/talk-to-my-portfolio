"""Audit workbook for transactions, lots, cash flows, performance, and reconciliation."""

from __future__ import annotations

from io import BytesIO
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Font


def build_performance_audit_workbook(
    *,
    transactions: list[dict[str, Any]],
    lot_result: dict[str, Any],
    performance: dict[str, Any],
    reconciliation: dict[str, Any],
) -> BytesIO:
    workbook = Workbook()
    default = workbook.active
    workbook.remove(default)
    _sheet(workbook, "Transactions", transactions)
    _sheet(workbook, "Tax Lots", lot_result.get("lots") or [])
    _sheet(workbook, "Disposals", lot_result.get("disposals") or [])
    _sheet(workbook, "Cash Flows", performance.get("cashflows") or [])
    _sheet(workbook, "Performance", [_flatten(performance, exclude={"cashflows", "return_bridge"})])
    _sheet(workbook, "Return Bridge", [performance.get("return_bridge") or {}])
    _sheet(workbook, "Reconciliation", reconciliation.get("by_security") or [])
    output = BytesIO()
    workbook.save(output)
    output.seek(0)
    return output


def _sheet(workbook: Workbook, title: str, rows: list[dict[str, Any]]) -> None:
    sheet = workbook.create_sheet(title)
    if not rows:
        sheet.append(["No data"])
        return
    flattened = [_flatten(row) for row in rows]
    columns = sorted({key for row in flattened for key in row})
    sheet.append(columns)
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    for row in flattened:
        sheet.append([row.get(column) for column in columns])
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions


def _flatten(row: dict[str, Any], *, exclude: set[str] | None = None) -> dict[str, Any]:
    excluded = exclude or set()
    result: dict[str, Any] = {}
    for key, value in row.items():
        if key in excluded:
            continue
        if isinstance(value, (dict, list)):
            result[key] = str(value)
        elif isinstance(value, bool):
            result[key] = "Yes" if value else "No"
        else:
            result[key] = value
    return result
