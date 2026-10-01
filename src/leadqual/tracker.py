"""Step 5: write the result to the Excel tracker a sales rep opens and works down."""

from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from leadqual.models import Result, Route, normalise_name

SHEET_NAME = "Leads"
# (header, column width)
COLUMNS: tuple[tuple[str, int], ...] = (
    ("Route", 16),
    ("Why", 42),
    ("Company", 26),
    ("Website", 24),
    ("Fit", 6),
    ("Priority", 10),
    ("Data", 9),
    ("Compliance", 34),
    ("Compliance reasoning", 60),
    ("Summary", 60),
    ("Size", 32),
    ("Cloud signal", 38),
    ("HQ", 18),
    ("Contact", 38),
    ("Updated", 17),
)
HEADERS = [header for header, _ in COLUMNS]
ROUTE = HEADERS.index("Route")
COMPANY = HEADERS.index("Company")
WEBSITE = HEADERS.index("Website")
FIT = HEADERS.index("Fit")
ROUTE_COLOURS = {
    Route.SALES_READY: "C6EFCE",
    Route.CHECK_FIRST: "FFEB9C",
    Route.DO_NOT_ENGAGE: "FFC7CE",
}

Row = list[str | int]


class TrackerError(RuntimeError):
    """The spreadsheet could not be written."""


def record(result: Result, path: Path) -> int:
    """Add or update the company's row and keep the sheet in calling order. Returns the row."""
    workbook = load_workbook(path) if path.exists() else _new_workbook()
    sheet = workbook[SHEET_NAME]
    rows: list[Row] = [
        list(row) for row in sheet.iter_rows(min_row=2, values_only=True) if row[COMPANY]
    ]
    new_row = _row_values(result)
    existing = _existing_index(rows, result)
    if existing is None:
        rows.append(new_row)
    else:
        rows[existing] = new_row
    rows.sort(key=_calling_order)
    _rewrite(sheet, rows)
    try:
        workbook.save(path)
    except PermissionError as error:
        raise TrackerError(f"{path} is open in another program; close it and run again") from error
    return rows.index(new_row) + 2


def _calling_order(row: Row) -> tuple[bool, int]:
    """Best fit first; blocked leads stay visible, at the bottom."""
    return row[ROUTE] == Route.DO_NOT_ENGAGE.value, -int(row[FIT])


def _new_workbook() -> Workbook:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = SHEET_NAME
    for column, (header, width) in enumerate(COLUMNS, start=1):
        sheet.cell(row=1, column=column, value=header).font = Font(bold=True)
        sheet.column_dimensions[get_column_letter(column)].width = width
    sheet.freeze_panes = "A2"
    return workbook


def _rewrite(sheet: Worksheet, rows: list[Row]) -> None:
    if sheet.max_row > 1:
        sheet.delete_rows(2, sheet.max_row - 1)
    for row_number, values in enumerate(rows, start=2):
        for column, value in enumerate(values, start=1):
            cell = sheet.cell(row=row_number, column=column, value=value)
            cell.alignment = Alignment(wrap_text=True, vertical="top")
        colour = ROUTE_COLOURS[Route(values[ROUTE])]
        sheet.cell(row=row_number, column=ROUTE + 1).fill = PatternFill("solid", start_color=colour)
    sheet.auto_filter.ref = sheet.dimensions


def _existing_index(rows: list[Row], result: Result) -> int | None:
    """One row per company, matched by website domain and company name together.

    The domain alone is not enough: anyone can submit someone else's website, and a
    later submission must not overwrite another company's row (or its block).
    """
    domain = result.company.domain or ""
    name = normalise_name(result.company.name)
    for index, row in enumerate(rows):
        if (row[WEBSITE] or "") == domain and normalise_name(str(row[COMPANY])) == name:
            return index
    return None


def _row_values(result: Result) -> Row:
    contact, research, compliance, fit = (
        result.contact,
        result.research,
        result.compliance,
        result.fit,
    )
    title = f", {contact.job_title}" if contact.job_title else ""
    values: Row = [
        result.decision.route.value,
        result.decision.reason,
        result.company.name,
        result.company.domain or "",
        fit.total,
        fit.priority.value,
        fit.data,
        compliance.flag,
        compliance.reasoning,
        research.summary,
        fit.size_basis,
        fit.cloud_basis,
        compliance.hq_country or research.hq_country or result.company.declared_country or "",
        f"{contact.name}{title} <{contact.email}>",
        result.processed_at.strftime("%Y-%m-%d %H:%M"),
    ]
    return [_safe(value) for value in values]


def _safe(value: str | int) -> str | int:
    """Text from a form or a website must never be run by Excel as a formula."""
    if isinstance(value, str) and value.startswith(("=", "+", "-", "@")):
        return f"'{value}"
    return value
