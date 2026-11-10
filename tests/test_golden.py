from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from acquiscout.eval.golden import GoldenRecord, parse_sheet_row


def sheet_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "company_id": "test-001",
        "legal_name": "TEST HOLDINGS LLC",
        "dba": "NOT_FOUND",
        "website": "https://example.com",
        "primary_county": "hillsborough",
        "also_serves_counties": "",
        "sector": "property_management",
        "true_entity_id": "test_holdings",
        "revenue_low_usd": 144000,
        "revenue_high_usd": 180000,
        "revenue_confidence": "low",
        "revenue_method": "units_under_management",
        "revenue_source_url": "https://example.com/revenue",
        "ebitda_low_usd": None,
        "ebitda_high_usd": None,
        "ebitda_confidence": "low",
        "ebitda_method": "benchmark_ratio",
        "ebitda_source_url": "NOT_FOUND",
        "contact_kind": "phone",
        "contact_value": "813-000-0000",
        "contact_label": "Office line",
        "contact_truth": "stale",
        "contact_source_url": "https://example.com/contact",
        "contact_checked_at": "2026-11-03",
        "contact_check_method": "website_only",
        "principal_name": "Jane Doe",
        "principal_role": "Owner",
        "principal_source_url": "https://example.com/principal",
        "management_evidence_url": "https://example.com/team",
        "human_score_total": 47,
        "human_verdict": "rejected",
        "rejection_reason": "Revenue and EBITDA below thresholds.",
        "primary_source_url": "https://example.com",
        "researched_at": "2026-11-03",
        "score_financial": 0,
        "score_management": 10,
        "score_ownership": 15,
        "score_contactability": 7,
        "score_market": 10,
        "score_data_confidence": 5,
    }
    row.update(overrides)
    return row


def test_valid_row_derives_total_and_clears_sentinels() -> None:
    record = parse_sheet_row(sheet_row())

    assert record.human_score_total == 47
    assert record.score_market == 10
    assert record.dba is None
    assert record.ebitda_source_url is None
    assert record.also_serves_counties is None
    assert record.contact_checked_at == date(2026, 11, 3)


def test_rejected_requires_reason() -> None:
    with pytest.raises(ValidationError):
        parse_sheet_row(sheet_row(rejection_reason=None))


def test_qualified_may_omit_reason() -> None:
    record = parse_sheet_row(sheet_row(human_verdict="qualified", rejection_reason=""))

    assert record.human_verdict.value == "qualified"
    assert record.rejection_reason is None


def test_semi_qualified_may_omit_reason() -> None:
    record = parse_sheet_row(sheet_row(human_verdict="semi_qualified", rejection_reason=""))

    assert record.human_verdict.value == "semi_qualified"
    assert record.rejection_reason is None


def test_score_above_cap_is_rejected() -> None:
    with pytest.raises(ValidationError):
        parse_sheet_row(sheet_row(score_market=11, human_score_total=53))


def test_stored_total_must_match_derived() -> None:
    with pytest.raises(ValueError, match="derived 47"):
        parse_sheet_row(sheet_row(human_score_total=99))


def test_unknown_column_is_rejected() -> None:
    with pytest.raises(ValidationError):
        parse_sheet_row(sheet_row(unexpected_column="value"))


def test_v2_context_fields_are_accepted() -> None:
    record = parse_sheet_row(sheet_row(employee_headcount="15-50", founded_year=1985))

    assert record.employee_headcount == "15-50"
    assert record.founded_year == 1985


def test_v2_context_sentinels_become_none() -> None:
    record = parse_sheet_row(sheet_row(employee_headcount="NOT_FOUND", founded_year="NOT_FOUND"))

    assert record.employee_headcount is None
    assert record.founded_year is None


def test_record_serializes_computed_total() -> None:
    payload = parse_sheet_row(sheet_row()).model_dump(mode="json")
    assert payload["human_score_total"] == 47
    payload.pop("human_score_total")

    assert payload["primary_county"] == "hillsborough"
    assert GoldenRecord.model_validate(payload).human_score_total == 47
