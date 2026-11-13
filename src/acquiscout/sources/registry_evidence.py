"""Compatibility facade for public registry evidence sources.

The source adapters live under ``acquiscout.sources.registry``. This module keeps
the original import path stable for pipeline and test callers.
"""

from __future__ import annotations

from acquiscout.sources.registry import (
    DBPREvidenceSource,
    DFSEvidenceSource,
    RegistryEvidence,
    RegistryEvidenceResolver,
    RegistrySource,
    SunbizSource,
)

__all__ = [
    "DBPREvidenceSource",
    "DFSEvidenceSource",
    "RegistryEvidence",
    "RegistryEvidenceResolver",
    "RegistrySource",
    "SunbizSource",
]
