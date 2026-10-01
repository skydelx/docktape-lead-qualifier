import pytest
from openpyxl import Workbook, load_workbook

from fakes import make_result
from leadqual.models import (
    CompetitorVerdict,
    Compliance,
    ResearchStatus,
    Route,
    SanctionsVerdict,
)
from leadqual.tracker import HEADERS, ROUTE_COLOURS, TrackerError, record


@pytest.fixture
def path(tmp_path):
    return tmp_path / "leads.xlsx"


def rows(path) -> list[dict]:
    sheet = load_workbook(path)["Leads"]
    header, *body = sheet.iter_rows(values_only=True)
    return [dict(zip(header, row, strict=True)) for row in body]


def test_first_lead_creates_a_spreadsheet_a_sales_rep_can_skim(path):
    row = record(make_result(), path)

    sheet = load_workbook(path)["Leads"]
    assert row == 2
    assert [cell.value for cell in sheet[1]] == HEADERS
    assert sheet.freeze_panes == "A2"
    assert sheet.auto_filter.ref == sheet.dimensions
    assert rows(path) == [
        {
            "Route": "SALES-READY",
            "Why": "Compliance clear; high priority (fit 85)",
            "Company": "Acme Analytics",
            "Website": "acme.io",
            "Fit": 85,
            "Priority": "high",
            "Data": "full",
            "Compliance": "clear",
            "Compliance reasoning": "Unrelated retail analytics company based in Austria.",
            "Summary": "Acme builds retail dashboards on AWS.",
            "Size": "51-1000 (website)",
            "Cloud signal": "medium workload, inferred (website)",
            "HQ": "Austria",
            "Contact": "Ada Example, CTO <ada@acme.io>",
            "Updated": "2026-10-01 14:30",
        }
    ]


def test_fit_stays_a_number_so_the_sheet_can_be_sorted(path):
    record(make_result(), path)

    assert isinstance(rows(path)[0]["Fit"], int)


def test_another_company_is_appended(path):
    record(make_result(), path)
    row = record(make_result(company="Beta Corp", domain="beta.example"), path)

    assert row == 3
    assert [entry["Company"] for entry in rows(path)] == ["Acme Analytics", "Beta Corp"]


def test_the_same_company_updates_its_row_instead_of_adding_one(path):
    record(make_result(), path)
    record(make_result(company="Beta Corp", domain="beta.example"), path)

    row = record(make_result(route=Route.CHECK_FIRST, reason="Possible competitor"), path)

    assert row == 2
    assert len(rows(path)) == 2
    assert rows(path)[0]["Route"] == "CHECK-FIRST"


def test_another_company_on_the_same_domain_cannot_overwrite_a_row(path):
    """Found in review: anyone could replace a blocked company's row by submitting its website."""
    blocked = make_result(
        company="CloudTrim Inc", route=Route.DO_NOT_ENGAGE, reason="Competitor: CloudTrim Inc"
    )
    record(blocked, path)

    record(make_result(company="Victim GmbH"), path)

    assert {entry["Company"]: entry["Route"] for entry in rows(path)} == {
        "Victim GmbH": "SALES-READY",
        "CloudTrim Inc": "DO-NOT-ENGAGE",
    }


def test_the_sheet_is_kept_in_calling_order_with_blocked_leads_at_the_bottom(path):
    blocked = make_result(
        company="CloudTrim Inc", domain="ct.example", route=Route.DO_NOT_ENGAGE, fit=100
    )
    record(make_result(company="Medium Co", domain="medium.example", fit=45), path)
    record(blocked, path)
    record(
        make_result(company="Check Co", domain="c.example", route=Route.CHECK_FIRST, fit=70), path
    )
    row = record(make_result(company="Top Co", domain="top.example", fit=90), path)

    assert row == 2
    assert [(entry["Company"], entry["Fit"]) for entry in rows(path)] == [
        ("Top Co", 90),
        ("Check Co", 70),  # a lead with an open question keeps its place by score
        ("Medium Co", 45),
        ("CloudTrim Inc", 100),  # blocked: still visible, never above a callable lead
    ]


def test_the_same_company_is_recognised_despite_spelling_of_its_legal_form(path):
    record(make_result(company="Acme Analytics"), path)

    assert record(make_result(company="ACME Analytics, Inc."), path) == 2


def test_leads_without_a_website_are_matched_by_company_name(path):
    record(make_result(company="No Site Ltd", domain=None), path)
    record(make_result(company="Other No Site", domain=None), path)

    row = record(make_result(company="no site ltd", domain=None), path)

    assert row == 2
    assert len(rows(path)) == 2


@pytest.mark.parametrize("route", list(Route))
def test_the_route_cell_is_colour_coded(path, route):
    record(make_result(route=route), path)

    cell = load_workbook(path)["Leads"]["A2"]
    assert cell.fill.start_color.rgb.endswith(ROUTE_COLOURS[route])


@pytest.mark.parametrize(
    "payload", ['=HYPERLINK("http://evil.example","x")', "+1", "-1", "@SUM(A1)"]
)
def test_submitted_text_is_never_stored_as_a_formula(path, payload):
    record(make_result(company=payload, summary=payload), path)

    stored = rows(path)[0]
    assert stored["Company"] == f"'{payload}"
    assert stored["Summary"] == f"'{payload}"


def test_the_row_says_when_the_website_itself_was_never_read(path):
    record(make_result(research_status=ResearchStatus.SEARCH_ONLY), path)

    assert rows(path)[0]["Summary"] == (
        "Website could not be read; from web search only. Acme builds retail dashboards on AWS."
    )


def test_a_headquarters_only_the_form_names_is_labelled_as_unverified(path):
    unknown = Compliance(
        competitor=CompetitorVerdict.CLEAR,
        sanctions=SanctionsVerdict.UNKNOWN,
        hq_country="Portugal",  # the agent repeating the form
        reasoning="Nobody has seen where this company is based.",
    )

    record(make_result(compliance=unknown, declared_country="Portugal"), path)

    assert rows(path)[0]["HQ"] == "Portugal (form, unverified)"


def test_cells_a_rep_typed_over_do_not_stop_the_next_lead(path):
    """Found in review: one edited Route or Fit cell crashed every later run before Slack."""
    record(make_result(), path)
    workbook = load_workbook(path)
    sheet = workbook["Leads"]
    sheet["A2"], sheet["E2"] = "CALLED", "85 (hot)"
    sheet.cell(row=1, column=len(HEADERS) + 1, value="Notes")
    sheet.cell(row=2, column=len(HEADERS) + 1, value="Spoke to Ada on Monday")
    workbook.save(path)

    row = record(make_result(company="Beta Corp", domain="beta.example", fit=60), path)

    assert row == 2
    assert [(entry["Company"], entry["Route"]) for entry in rows(path)] == [
        ("Beta Corp", "SALES-READY"),
        ("Acme Analytics", "CALLED"),  # an unreadable fit sorts below the scored leads
    ]
    assert rows(path)[1]["Notes"] == "Spoke to Ada on Monday"


def test_a_note_next_to_a_company_survives_its_resubmission(path):
    record(make_result(), path)
    workbook = load_workbook(path)
    workbook["Leads"].cell(row=2, column=len(HEADERS) + 1, value="Call back in May")
    workbook.save(path)

    record(make_result(fit=70), path)

    sheet = load_workbook(path)["Leads"]
    assert sheet.cell(row=2, column=HEADERS.index("Fit") + 1).value == 70
    assert sheet.cell(row=2, column=len(HEADERS) + 1).value == "Call back in May"


def test_a_file_that_is_not_a_tracker_gives_a_clear_error(path):
    path.write_bytes(b"not a spreadsheet")

    with pytest.raises(TrackerError, match="could not be opened as the lead tracker"):
        record(make_result(), path)


def test_a_spreadsheet_open_in_excel_gives_a_clear_error(path, monkeypatch):
    def locked(_self, _path):
        raise PermissionError("locked")

    monkeypatch.setattr(Workbook, "save", locked)

    with pytest.raises(TrackerError, match="close it and run again"):
        record(make_result(), path)
