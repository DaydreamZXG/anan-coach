from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Mapping

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.contracts.guokao_xingce import GUOKAO_XINGCE_MODULES


_CANONICAL_MODULE_COUNT = 6
_EXPECTED_CANONICAL_NAME_BY_CODE = {
    module.code: module.display_name for module in GUOKAO_XINGCE_MODULES
}


@dataclass(frozen=True, slots=True)
class ReleasedModuleVocabulary:
    """Immutable module-name vocabulary frozen by a knowledge-map release."""

    release_id: str
    release_version: str
    source_data_version: str
    vocab_version: str
    fresh_until: datetime
    _canonical_by_raw_name: Mapping[str, str]
    _raw_names_by_canonical: Mapping[str, tuple[str, ...]]

    def canonicalize(self, raw_name: str) -> str | None:
        return self._canonical_by_raw_name.get(raw_name.strip())

    def equivalent_raw_names(self, raw_name: str) -> tuple[str, ...]:
        canonical = self.canonicalize(raw_name)
        if canonical is None:
            return ()
        return self._raw_names_by_canonical[canonical]

    @property
    def canonical_names(self) -> tuple[str, ...]:
        return tuple(sorted(self._raw_names_by_canonical))


def load_current_module_vocabulary(
    session: Session,
    *,
    exam_scope: str,
    track: str,
    now: datetime | None = None,
) -> ReleasedModuleVocabulary | None:
    """Load the sole fresh active release and its exact published vocabulary.

    Passing ``now`` freezes business-time selection for a larger operation.
    Omitting it evaluates freshness at statement execution time, which is
    required for long-running transactions that can cross an expiry boundary.
    Any active-release or vocabulary ambiguity fails closed.
    """

    params: dict[str, object] = {
        "exam_scope": exam_scope,
        "track": track,
        "business_now": now,
    }
    rows = session.execute(
        text(
            """
            with active_releases as (
              select
                release.release_id,
                release.release_version,
                release.source_data_version,
                release.vocab_version,
                release.fresh_until,
                count(*) over () as active_release_count
              from product.knowledge_map_releases release
              where release.exam_scope = :exam_scope
                and release.track = :track
                and release.status = 'active'
            ), current_release as (
              select
                release_id,
                release_version,
                source_data_version,
                vocab_version,
                fresh_until
              from active_releases
              where active_release_count = 1
                and (
                  (
                    cast(:business_now as timestamptz) is null
                    and fresh_until > statement_timestamp()
                  )
                  or fresh_until > cast(:business_now as timestamptz)
                )
            )
            select
              current_release.release_id,
              current_release.release_version,
              current_release.source_data_version,
              current_release.vocab_version,
              current_release.fresh_until,
              alias.raw_module_name,
              alias.action,
              alias.canonical_code
            from current_release
            join product.vocab_module_alias_map alias
              on alias.version = current_release.vocab_version
             and alias.action in ('canonical', 'map')
            order by alias.canonical_code, alias.action, alias.raw_module_name
            """
        ),
        params,
    ).mappings().all()
    return _build_vocabulary(rows)


def _build_vocabulary(rows: list[Mapping[str, object]]) -> ReleasedModuleVocabulary | None:
    if not rows:
        return None

    canonical_names_by_code: dict[str, list[str]] = {}
    for row in rows:
        if row["action"] == "canonical":
            canonical_names_by_code.setdefault(str(row["canonical_code"]), []).append(
                str(row["raw_module_name"])
            )
    if (
        len(canonical_names_by_code) != _CANONICAL_MODULE_COUNT
        or any(len(names) != 1 for names in canonical_names_by_code.values())
        or {
            code: names[0]
            for code, names in canonical_names_by_code.items()
        }
        != _EXPECTED_CANONICAL_NAME_BY_CODE
    ):
        return None

    canonical_by_raw_name: dict[str, str] = {}
    raw_names_by_canonical: dict[str, list[str]] = {
        names[0]: [] for names in canonical_names_by_code.values()
    }
    for row in rows:
        code = str(row["canonical_code"])
        canonical_names = canonical_names_by_code.get(code)
        if canonical_names is None:
            return None
        raw_name = str(row["raw_module_name"])
        canonical_name = canonical_names[0]
        canonical_by_raw_name[raw_name] = canonical_name
        raw_names_by_canonical[canonical_name].append(raw_name)

    canonical_raw_names = {
        names[0] for names in canonical_names_by_code.values()
    }
    ordered_raw_names = {
        canonical: (
            canonical,
            *sorted(
                raw_name
                for raw_name in raw_names
                if raw_name not in canonical_raw_names
            ),
        )
        for canonical, raw_names in raw_names_by_canonical.items()
    }
    first = rows[0]
    fresh_until = first["fresh_until"]
    if not isinstance(fresh_until, datetime):
        return None
    return ReleasedModuleVocabulary(
        release_id=str(first["release_id"]),
        release_version=str(first["release_version"]),
        source_data_version=str(first["source_data_version"]),
        vocab_version=str(first["vocab_version"]),
        fresh_until=fresh_until,
        _canonical_by_raw_name=MappingProxyType(canonical_by_raw_name),
        _raw_names_by_canonical=MappingProxyType(ordered_raw_names),
    )
