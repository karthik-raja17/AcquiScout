from __future__ import annotations

import httpx
import pytest

from acquiscout.config import Settings
from acquiscout.sources.govfiles import GovFilesSearchProvider


def make_settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "_env_file": None,
        "govfiles_api_key": "test-key",
        "govfiles_base_url": "https://api.govfiles.dev",
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_govfiles_detail_parses_registry_profile() -> None:
    payload = {
        "entity_number": "H30572",
        "jurisdiction_code": "us_fl",
        "legal_name": "HILLSBOROUGH TITLE, INC.",
        "status": "active",
        "status_raw": "Active",
        "legal_form": "corporation",
        "legal_form_raw": "Domestic Profit Corporation",
        "formed_on": "1984-11-20",
        "addresses": {
            "registered": {"raw": "1605 SOUTH ALEXANDER STREET, PLANT CITY, FL 33563"},
            "mailing": {"raw": "1605 SOUTH ALEXANDER STREET, PLANT CITY, FL 33563"},
        },
        "names": [{"name": "TAMPA BAY TITLE"}],
        "parties": [
            {
                "name": "Amy L Gregory",
                "roles": [{"kind": "officer", "title": "President"}],
                "address": {"raw": "1605 S ALEXANDER STREET"},
            }
        ],
        "filings": [{"id": "H30572.00001", "filed_on": "1998-10-16"}],
        "identifiers": [{"scheme": "us_fein", "value": "592465719"}],
        "entity_url": "https://search.sunbiz.org/Inquiry/CorporationSearch/SearchResults/EntityName/x/Page1",
    }
    seen: dict[str, str | None] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["api_key"] = request.headers.get("x-api-key")
        return httpx.Response(200, json=payload)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = GovFilesSearchProvider(make_settings(), http=client)

    profile = await provider.get_company("us_fl", "H30572")
    await provider.aclose()

    assert seen["api_key"] == "test-key"
    assert profile.status == "active"
    assert profile.legal_form == "corporation"
    assert profile.formed_on is not None
    assert profile.formed_on.year == 1984
    assert profile.officers[0].name == "Amy L Gregory"
    assert profile.officers[0].title == "President"
    assert profile.public_entity_url is not None
    assert profile.govfiles_detail_url == "https://api.govfiles.dev/v2/companies/us_fl/H30572"
    assert profile.evidence_status == "none"
