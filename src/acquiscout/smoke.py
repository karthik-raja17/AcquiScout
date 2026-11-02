"""Offline wiring check: config loads, weights sum, schema accepts a minimal prospect."""

from __future__ import annotations

from acquiscout import __version__
from acquiscout.config import get_settings
from acquiscout.schemas import County, Prospect, Sector


def main() -> int:
    settings = get_settings()
    weights_total = sum(settings.weights.model_dump().values())
    funnel = " -> ".join(
        str(part)
        for part in (
            settings.weekly_raw,
            settings.weekly_deduped,
            settings.weekly_scraped,
            settings.weekly_qualified,
            settings.weekly_verified,
        )
    )

    Prospect(
        prospect_id="smoke-1",
        legal_name="Smoke Test Title LLC",
        counties=[County.HILLSBOROUGH],
        sector=Sector.TITLE_COMPANY,
    )

    print(f"acquiscout {__version__} env={settings.app_env}")
    print(f"counties={[county.value for county in settings.counties]}")
    print(f"sectors={[sector.value for sector in settings.sectors]}")
    print(f"weights_total={weights_total}")
    print(f"funnel={funnel}")
    print("schema check=ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
