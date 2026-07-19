from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
import json
from typing import Any
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.contracts.external_learning.models import LearningTrack
from app.domains.external_ingestion.matching.fingerprint import normalize_text
from app.domains.external_ingestion.matching.models import (
    IndexedContent,
    MatchQuery,
)
from app.domains.xingce_learning.content_version import question_content_version_id


class ContentVersionIntegrityError(RuntimeError):
    pass


class ContentPublicationError(RuntimeError):
    pass


class SQLQuestionContentCatalog:
    """Durable question-version authority and current-version search adapter.

    Published snapshots are append-only. Search reads only the head selected by the
    publication transaction; direct reads by contentVersionId retain old provenance.
    Visual fingerprints are never synthesized from URLs or object keys.
    """

    def __init__(
        self,
        session: Session,
        *,
        clock: Callable[[], datetime] | None = None,
        sql_prefilter_threshold: float = 0.05,
        prefetch_factor: int = 8,
        maximum_prefetch: int = 500,
    ) -> None:
        if not 0 <= sql_prefilter_threshold <= 1:
            raise ValueError("SQL matching prefilter threshold must be between zero and one")
        if prefetch_factor < 1 or maximum_prefetch < 1:
            raise ValueError("matching prefetch limits must be positive")
        self._session = session
        self._clock = clock or (lambda: datetime.now(UTC))
        self._threshold = sql_prefilter_threshold
        self._prefetch_factor = prefetch_factor
        self._maximum_prefetch = maximum_prefetch

    def publish_current(self, *, question_id: str) -> IndexedContent:
        """Publish the canonical current question snapshot and atomically move its head."""

        self._session.execute(
            text("select pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"question-content-publish:{question_id}"},
        )
        source = self._canonical_question(question_id=question_id)
        if source is None:
            raise ContentPublicationError(
                "only an active canonical question can be published"
            )
        track = _track(str(source.pop("domain")))
        snapshot = {key: source.get(key) for key in _VERSION_FIELDS}
        if not str(snapshot.get("stem") or "").strip():
            raise ContentPublicationError("question content requires a non-empty stem")
        content_version_id = question_content_version_id(snapshot)
        normalized_search_text = _snapshot_search_text(snapshot)
        if not normalized_search_text:
            raise ContentPublicationError(
                "question content has no searchable normalized text"
            )
        existing = self._session.execute(
            text(
                """
                select question_id, track, snapshot_payload, normalized_search_text
                from product.question_content_versions
                where content_version_id = :content_version_id
                """
            ),
            {"content_version_id": content_version_id},
        ).mappings().one_or_none()
        if existing is None:
            self._session.execute(
                text(
                    """
                    insert into product.question_content_versions (
                      content_version_id, question_id, track, snapshot_payload,
                      normalized_search_text, created_at
                    ) values (
                      :content_version_id, :question_id, :track,
                      cast(:snapshot_payload as jsonb), :normalized_search_text,
                      :created_at
                    )
                    """
                ),
                {
                    "content_version_id": content_version_id,
                    "question_id": question_id,
                    "track": track.value,
                    "snapshot_payload": json.dumps(
                        snapshot, ensure_ascii=False, sort_keys=True
                    ),
                    "normalized_search_text": normalized_search_text,
                    "created_at": self._clock(),
                },
            )
        else:
            if (
                str(existing["question_id"]) != question_id
                or str(existing["track"]) != track.value
                or dict(existing["snapshot_payload"]) != snapshot
                or str(existing["normalized_search_text"]) != normalized_search_text
            ):
                raise ContentVersionIntegrityError(
                    "content-version hash collides with different stored authority"
                )
        self._session.execute(
            text(
                """
                insert into product.question_content_heads (
                  question_id, content_version_id, updated_at
                ) values (:question_id, :content_version_id, :updated_at)
                on conflict (question_id) do update
                set content_version_id = excluded.content_version_id,
                    updated_at = excluded.updated_at
                where product.question_content_heads.content_version_id
                      is distinct from excluded.content_version_id
                """
            ),
            {
                "question_id": question_id,
                "content_version_id": content_version_id,
                "updated_at": self._clock(),
            },
        )
        result = self.get(content_version_id=content_version_id)
        if result is None:
            raise ContentVersionIntegrityError("published content version is unreadable")
        return result

    def search(
        self, *, query: MatchQuery, maximum_candidates: int
    ) -> tuple[IndexedContent, ...]:
        normalized = normalize_text(
            "\n".join((query.prompt_text, *query.option_texts, *query.material_texts))
        )
        if not normalized or maximum_candidates < 1:
            return ()
        prefetch = min(
            self._maximum_prefetch,
            max(maximum_candidates, maximum_candidates * self._prefetch_factor),
        )
        visual = list(dict.fromkeys(query.visual_fingerprints))
        search_grams = _search_grams(normalized)
        rows = self._session.execute(
            text(
                """
                select version.content_version_id
                from product.question_content_heads head
                join product.question_content_versions version
                  on version.question_id = head.question_id
                 and version.content_version_id = head.content_version_id
                where version.track = :track
                  and (
                    (
                      select count(*)::double precision
                      from unnest(cast(:search_grams as text[])) gram
                      where strpos(version.normalized_search_text, gram) > 0
                    ) / cardinality(cast(:search_grams as text[])) >= :threshold
                    or (
                      cardinality(cast(:visual as text[])) > 0
                      and exists (
                        select 1
                        from product.question_visual_fingerprints visual
                        where visual.question_id = version.question_id
                          and visual.content_version_id = version.content_version_id
                          and visual.fingerprint_sha256 = any(cast(:visual as text[]))
                      )
                    )
                  )
                order by
                  exists (
                    select 1
                    from product.question_visual_fingerprints visual
                    where visual.question_id = version.question_id
                      and visual.content_version_id = version.content_version_id
                      and visual.fingerprint_sha256 = any(cast(:visual as text[]))
                  ) desc,
                  (
                    select count(*)::double precision
                    from unnest(cast(:search_grams as text[])) gram
                    where strpos(version.normalized_search_text, gram) > 0
                  ) / cardinality(cast(:search_grams as text[])) desc,
                  version.content_version_id
                limit :prefetch
                """
            ),
            {
                "track": query.track.value,
                "normalized": normalized,
                "search_grams": search_grams,
                "threshold": self._threshold,
                "visual": visual,
                "prefetch": prefetch,
            },
        ).scalars()
        results: list[IndexedContent] = []
        for content_version_id in rows:
            content = self.get(content_version_id=str(content_version_id))
            if content is not None:
                results.append(content)
        return tuple(results)

    def get(self, *, content_version_id: str) -> IndexedContent | None:
        row = self._session.execute(
            text(
                """
                select content_version_id, question_id, track, snapshot_payload,
                       normalized_search_text
                from product.question_content_versions
                where content_version_id = :content_version_id
                """
            ),
            {"content_version_id": content_version_id},
        ).mappings().one_or_none()
        if row is None:
            return None
        snapshot = dict(row["snapshot_payload"])
        _verify_version(
            expected_content_version_id=str(row["content_version_id"]),
            snapshot=snapshot,
            expected_normalized_search_text=str(row["normalized_search_text"]),
        )
        fingerprints = tuple(
            str(value)
            for value in self._session.execute(
                text(
                    """
                    select fingerprint_sha256
                    from product.question_visual_fingerprints
                    where question_id = :question_id
                      and content_version_id = :content_version_id
                    order by fingerprint_sha256
                    """
                ),
                {
                    "question_id": str(row["question_id"]),
                    "content_version_id": content_version_id,
                },
            ).scalars()
        )
        options = tuple(
            str(item.get("option_text") or "").strip()
            for item in _objects(snapshot.get("options"))
            if str(item.get("option_text") or "").strip()
        )
        material = str(snapshot.get("material_text") or "").strip()
        answer = str(snapshot.get("answer") or "").strip() or None
        explanation = str(snapshot.get("preferred_analysis") or "").strip() or None
        return IndexedContent(
            contentVersionId=content_version_id,
            track=str(row["track"]),
            promptText=str(snapshot.get("stem") or ""),
            optionTexts=options,
            materialTexts=(material,) if material else (),
            visualFingerprints=fingerprints,
            referenceAnswer=answer,
            solutionExplanation=explanation,
            distributionClass="licensed_transferable",
            crossUserMatchAllowed=True,
        )

    def _canonical_question(self, *, question_id: str) -> dict[str, Any] | None:
        row = self._session.execute(
            text(
                """
                select question.question_id, question.domain, question.module_name,
                       question.question_type, question.stem, question.stem_html,
                       question.answer, question.material_text, question.material_html,
                       explanation.preferred_analysis, explanation.analysis_source,
                       explanation.analysis_status, explanation.prefer_ai_analysis,
                       explanation.analysis_needs_review
                from product.questions question
                left join product.question_explanations explanation
                  on explanation.question_id = question.question_id
                where question.question_id = :question_id
                  and question.status in ('active', 'canonical')
                """
            ),
            {"question_id": question_id},
        ).mappings().one_or_none()
        if row is None:
            return None
        source = dict(row)
        source["options"] = [
            dict(item)
            for item in self._session.execute(
                text(
                    """
                    select label, option_text, option_html
                    from product.question_options
                    where question_id = :question_id
                    order by option_index
                    """
                ),
                {"question_id": question_id},
            ).mappings()
        ]
        source["assets"] = [
            dict(item)
            for item in self._session.execute(
                text(
                    """
                    select asset_id, part_kind, part_index, image_ordinal,
                           image_src, object_key, is_representative_asset
                    from product.question_assets
                    where question_id = :question_id
                    order by part_kind nulls last, part_index nulls last,
                             image_ordinal nulls last, asset_id
                    """
                ),
                {"question_id": question_id},
            ).mappings()
        ]
        return source


_VERSION_FIELDS = (
    "question_id",
    "module_name",
    "question_type",
    "stem",
    "stem_html",
    "answer",
    "material_text",
    "material_html",
    "preferred_analysis",
    "analysis_source",
    "analysis_status",
    "prefer_ai_analysis",
    "analysis_needs_review",
    "options",
    "assets",
)


def _track(domain: str) -> LearningTrack:
    mapping = {
        "objective": LearningTrack.XINGCE,
        "xingce": LearningTrack.XINGCE,
        "essay": LearningTrack.ESSAY,
        "shenlun": LearningTrack.ESSAY,
    }
    try:
        return mapping[domain]
    except KeyError as exc:
        raise ContentPublicationError(
            f"question domain {domain!r} has no approved learning-track mapping"
        ) from exc


def _snapshot_search_text(snapshot: Mapping[str, Any]) -> str:
    options = [
        str(item.get("option_text") or "") for item in _objects(snapshot.get("options"))
    ]
    return normalize_text(
        "\n".join(
            (
                str(snapshot.get("stem") or ""),
                *options,
                str(snapshot.get("material_text") or ""),
            )
        )
    )


def _search_grams(normalized: str) -> list[str]:
    if len(normalized) == 1:
        return [normalized]
    return list(
        dict.fromkeys(
            normalized[index : index + 2]
            for index in range(len(normalized) - 1)
        )
    )


def _verify_version(
    *,
    expected_content_version_id: str,
    snapshot: Mapping[str, Any],
    expected_normalized_search_text: str,
) -> None:
    if question_content_version_id(snapshot) != expected_content_version_id:
        raise ContentVersionIntegrityError(
            "stored question snapshot does not match its contentVersionId"
        )
    if _snapshot_search_text(snapshot) != expected_normalized_search_text:
        raise ContentVersionIntegrityError(
            "stored question search text does not match its immutable snapshot"
        )


def _objects(value: object) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, list):
        return ()
    return tuple(dict(item) for item in value if isinstance(item, dict))
