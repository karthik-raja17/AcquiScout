"""Dispatch public registry evidence URLs to source adapters."""

from __future__ import annotations

from collections.abc import Sequence

from acquiscout.config import Settings
from acquiscout.sources.registry.base import RegistryEvidence, RegistrySource
from acquiscout.sources.registry.license import DBPREvidenceSource, DFSEvidenceSource
from acquiscout.sources.registry.sunbiz import SunbizSource
from acquiscout.sources.scrape import Scraper


class RegistryEvidenceResolver:
    def __init__(
        self,
        settings: Settings,
        scraper: Scraper | None = None,
        sources: Sequence[RegistrySource] | None = None,
    ) -> None:
        self._settings = settings
        self._scraper = scraper or Scraper(settings)
        self._owns_scraper = scraper is None
        self._sources = tuple(
            sources or (SunbizSource(), DBPREvidenceSource(), DFSEvidenceSource())
        )
        self.last_text: str = ""

    async def resolve(
        self,
        public_entity_url: str | None,
        entity_number: str,
    ) -> RegistryEvidence:
        if not public_entity_url:
            return RegistryEvidence(
                evidence_status="failed",
                reason="missing_public_entity_url",
            )
        source = next(
            (candidate for candidate in self._sources if candidate.supports(public_entity_url)),
            None,
        )
        if source is None:
            return RegistryEvidence(
                evidence_status="unsupported_source",
                reason="unapproved_registry_url",
            )
        try:
            evidence = await source.resolve(public_entity_url, entity_number, self._scraper)
        except Exception as exc:
            evidence = RegistryEvidence(
                evidence_status="failed",
                reason=f"{type(exc).__name__}: {exc}",
                source=source.name,
            )
        self.last_text = source.last_text
        return evidence

    async def aclose(self) -> None:
        if self._owns_scraper:
            await self._scraper.aclose()


__all__ = ["RegistryEvidenceResolver"]
