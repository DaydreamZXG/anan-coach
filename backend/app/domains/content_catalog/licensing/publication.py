from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.contracts.external_learning.models import (
    Audience,
    ContentComponent,
    ContentLicenseRecord,
    ContentTransform,
    ContentUse,
    DeliveryChannel,
    LicenseGrantCell,
    LicenseGrantMatrix,
    LicenseState,
    LicenseTerm,
    ReviewerReceipt,
    Territory,
    TransferableRights,
    canonical_grant_matrix_hash,
    canonical_record_hash,
)


class CatalogLicensePublicationError(RuntimeError):
    pass


@dataclass(frozen=True)
class CatalogLicensePublicationResult:
    eligible_content_versions: int
    already_active: int
    published: int
    missing_dataset_evidence: int
    manifest_sha256: str


class CatalogLicensePublicationService:
    """Publish pre-reviewed owner-controlled catalog rights in bounded SQL batches.

    The attestation is reviewed once as a source manifest. Every content version gets
    its own immutable license record and reviewer receipt. Closed coverage remains
    default-deny and model training is never granted.
    """

    AUTHOR = "product-owner:daydreamzxg"
    REVIEWER = "rights-reviewer:catalog-release"

    def __init__(self, *, session: Session, clock=None) -> None:
        self._session = session
        self._clock = clock or (lambda: datetime.now(UTC))

    def publish(
        self,
        *,
        manifest_path: Path,
        apply: bool,
        batch_size: int = 500,
    ) -> CatalogLicensePublicationResult:
        if not 1 <= batch_size <= 2_000:
            raise ValueError("catalog license batch size must be between 1 and 2000")
        manifest_bytes = manifest_path.read_bytes()
        manifest = json.loads(manifest_bytes)
        self._validate_manifest(manifest)
        manifest_hash = sha256(manifest_bytes).hexdigest()
        dataset_ids = tuple(sorted(manifest["sourceDatasetIds"]))
        rows = self._eligible_rows(dataset_ids=dataset_ids)
        missing = self._missing_dataset_count(dataset_ids=dataset_ids)
        if missing:
            raise CatalogLicensePublicationError(
                f"{missing} active content versions have no attested source dataset"
            )
        active_ids = self._active_content_ids(
            content_version_ids=tuple(str(row["content_version_id"]) for row in rows)
        )
        pending = [row for row in rows if str(row["content_version_id"]) not in active_ids]
        if not apply:
            return CatalogLicensePublicationResult(
                eligible_content_versions=len(rows),
                already_active=len(active_ids),
                published=0,
                missing_dataset_evidence=missing,
                manifest_sha256=manifest_hash,
            )

        now = self._clock()
        matrix = _owner_catalog_matrix()
        closed_hash = _closed_coverage_hash()
        for offset in range(0, len(pending), batch_size):
            records = [
                _active_record(
                    content_version_id=str(row["content_version_id"]),
                    source_dataset_ids=tuple(row["source_dataset_ids"]),
                    manifest_ref=f"file:{manifest_path.as_posix()}",
                    manifest_hash=manifest_hash,
                    matrix=matrix,
                    closed_coverage_hash=closed_hash,
                    author=self.AUTHOR,
                    reviewer=self.REVIEWER,
                    now=now,
                )
                for row in pending[offset : offset + batch_size]
            ]
            self._insert_batch(records)
            self._session.commit()
        published_active = len(
            self._active_content_ids(
                content_version_ids=tuple(
                    str(row["content_version_id"]) for row in rows
                )
            )
        )
        if published_active != len(rows):
            raise CatalogLicensePublicationError(
                "post-publication active-license coverage count does not match catalog"
            )
        return CatalogLicensePublicationResult(
            eligible_content_versions=len(rows),
            already_active=len(active_ids),
            published=len(pending),
            missing_dataset_evidence=0,
            manifest_sha256=manifest_hash,
        )

    def _validate_manifest(self, manifest: object) -> None:
        if not isinstance(manifest, dict):
            raise CatalogLicensePublicationError("owner attestation must be a JSON object")
        if manifest.get("contractVersion") != "owner-content-attestation@v1":
            raise CatalogLicensePublicationError("owner attestation contract is unsupported")
        if manifest.get("ownerActorId") != self.AUTHOR:
            raise CatalogLicensePublicationError("owner attestation author drifted")
        if manifest.get("reviewerActorId") != self.REVIEWER:
            raise CatalogLicensePublicationError("owner attestation reviewer drifted")
        if self.AUTHOR == self.REVIEWER:
            raise CatalogLicensePublicationError("license author and reviewer must differ")
        datasets = manifest.get("sourceDatasetIds")
        if not isinstance(datasets, list) or not datasets or any(
            not isinstance(item, str) or not item.strip() for item in datasets
        ):
            raise CatalogLicensePublicationError("attested datasets are required")
        if len(datasets) != len(set(datasets)):
            raise CatalogLicensePublicationError("attested datasets must be unique")
        if manifest.get("modelTrainingUse") != "prohibited":
            raise CatalogLicensePublicationError("model training must remain prohibited")
        if manifest.get("defaultDecision") != "deny":
            raise CatalogLicensePublicationError("closed coverage must default deny")

    def _eligible_rows(self, *, dataset_ids: tuple[str, ...]):
        return self._session.execute(
            text(
                """
                with source_evidence as (
                  select occurrence.question_id, occurrence.source_dataset_id
                  from product.question_occurrences occurrence
                  where occurrence.source_dataset_id = any(cast(:dataset_ids as text[]))
                  union
                  select question.question_id, chunk.source_dataset_id
                  from product.questions question
                  join product.knowledge_chunks chunk
                    on chunk.question_id = question.question_id
                  where chunk.source_dataset_id = any(cast(:dataset_ids as text[]))
                  union
                  select question.question_id, chunk.source_dataset_id
                  from product.questions question
                  join product.knowledge_chunks chunk
                    on chunk.source_question_id =
                       (to_jsonb(question) ->> 'representative_source_question_id')
                  where chunk.source_dataset_id = any(cast(:dataset_ids as text[]))
                )
                select head.content_version_id,
                       array_agg(distinct evidence.source_dataset_id order by evidence.source_dataset_id)
                         as source_dataset_ids
                from product.question_content_heads head
                join product.question_content_versions version
                  on version.content_version_id = head.content_version_id
                join source_evidence evidence
                  on evidence.question_id = version.question_id
                group by head.content_version_id
                order by head.content_version_id
                """
            ),
            {"dataset_ids": list(dataset_ids)},
        ).mappings().all()

    def _missing_dataset_count(self, *, dataset_ids: tuple[str, ...]) -> int:
        return int(
            self._session.execute(
                text(
                    """
                    with source_evidence as (
                      select occurrence.question_id
                      from product.question_occurrences occurrence
                      where occurrence.source_dataset_id = any(cast(:dataset_ids as text[]))
                      union
                      select question.question_id
                      from product.questions question
                      join product.knowledge_chunks chunk
                        on chunk.question_id = question.question_id
                      where chunk.source_dataset_id = any(cast(:dataset_ids as text[]))
                      union
                      select question.question_id
                      from product.questions question
                      join product.knowledge_chunks chunk
                        on chunk.source_question_id =
                           (to_jsonb(question) ->> 'representative_source_question_id')
                      where chunk.source_dataset_id = any(cast(:dataset_ids as text[]))
                    )
                    select count(*)
                    from product.question_content_heads head
                    join product.question_content_versions version
                      on version.content_version_id = head.content_version_id
                    where not exists (
                      select 1 from source_evidence evidence
                      where evidence.question_id = version.question_id
                    )
                    """
                ),
                {"dataset_ids": list(dataset_ids)},
            ).scalar_one()
        )

    def _active_content_ids(self, *, content_version_ids: tuple[str, ...]) -> set[str]:
        if not content_version_ids:
            return set()
        return set(
            self._session.execute(
                text(
                    "select distinct content_version_id "
                    "from product.content_license_heads "
                    "where state = 'active' "
                    "and content_version_id = any(cast(:content_version_ids as text[]))"
                ),
                {"content_version_ids": list(content_version_ids)},
            ).scalars()
        )

    def _insert_batch(self, records: list[ContentLicenseRecord]) -> None:
        values = []
        heads = []
        for record in records:
            payload = record.model_dump(by_alias=True, mode="json")
            record_version_id = uuid5(
                NAMESPACE_URL, f"owner-catalog-license-record:{record.content_license_id}:1"
            )
            values.append(
                {
                    "record_version_id": str(record_version_id),
                    "content_license_id": str(record.content_license_id),
                    "content_version_id": record.content_version_id,
                    "revision": 1,
                    "authorization_epoch": 1,
                    "state": "active",
                    "created_by_actor_id": record.created_by_actor_id,
                    "closed_coverage_hash": record.closed_coverage_hash,
                    "evidence_manifest_hash": record.evidence_manifest_hash,
                    "grant_matrix_hash": record.rights.grant_matrix.matrix_hash,
                    "record_canonical_hash": record.record_canonical_hash,
                    "record_payload": json.dumps(payload, ensure_ascii=False, sort_keys=True),
                    "reviewer_receipt": json.dumps(
                        record.reviewer_receipt.model_dump(by_alias=True, mode="json"),
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    "created_at": record.created_at,
                    "updated_at": record.updated_at,
                }
            )
            heads.append({**values[-1]})
        self._session.execute(text(_INSERT_VERSIONS_SQL), values)
        self._session.execute(text(_INSERT_HEADS_SQL), heads)


def _owner_catalog_matrix() -> LicenseGrantMatrix:
    cells: list[LicenseGrantCell] = []
    match_components = (
        ContentComponent.QUESTION_PROMPT,
        ContentComponent.QUESTION_OPTIONS,
        ContentComponent.MATERIAL_TEXT,
        ContentComponent.MATERIAL_TABLE,
        ContentComponent.MATERIAL_IMAGE,
    )
    for component in match_components:
        cells.append(
            LicenseGrantCell(
                component=component,
                use=ContentUse.CROSS_USER_MATCH,
                channel=DeliveryChannel.INTERNAL_RETRIEVAL,
                audience=Audience.AUTHENTICATED_USERS,
                transform=ContentTransform.EMBEDDING,
                territory=Territory.MAINLAND_CHINA,
            )
        )
    for component in (ContentComponent.REFERENCE_ANSWER, ContentComponent.SOLUTION_EXPLANATION):
        cells.append(
            LicenseGrantCell(
                component=component,
                use=ContentUse.IN_APP_PRACTICE,
                channel=DeliveryChannel.WECHAT_MINIPROGRAM,
                audience=Audience.ACCOUNT_OWNER,
                transform=ContentTransform.VERBATIM,
                territory=Territory.MAINLAND_CHINA,
            )
        )
    for component in (*match_components, ContentComponent.REFERENCE_ANSWER, ContentComponent.SOLUTION_EXPLANATION):
        cells.append(
            LicenseGrantCell(
                component=component,
                use=ContentUse.AI_GROUNDING,
                channel=DeliveryChannel.AI_PROCESSOR,
                audience=Audience.ACCOUNT_OWNER,
                transform=ContentTransform.VERBATIM,
                territory=Territory.MAINLAND_CHINA,
            )
        )
    for component in (*match_components, ContentComponent.REFERENCE_ANSWER, ContentComponent.SOLUTION_EXPLANATION):
        for use, channel in (
            (ContentUse.PRIVATE_PRINT, DeliveryChannel.PDF_EXPORT),
            (ContentUse.NOTE_EXPORT, DeliveryChannel.NOTE_APP_FILE),
        ):
            cells.append(
                LicenseGrantCell(
                    component=component,
                    use=use,
                    channel=channel,
                    audience=Audience.ACCOUNT_OWNER,
                    transform=ContentTransform.LAYOUT_REFLOW,
                    territory=Territory.MAINLAND_CHINA,
                )
            )
    payload = {
        "matrixVersionRef": "owner-catalog-grant-matrix@v1",
        "matrixHash": "0" * 64,
        "componentsCoverage": [item.value for item in ContentComponent],
        "usesCoverage": [item.value for item in ContentUse],
        "channelsCoverage": [item.value for item in DeliveryChannel],
        "audiencesCoverage": [item.value for item in Audience],
        "transformsCoverage": [item.value for item in ContentTransform],
        "territoriesCoverage": [item.value for item in Territory],
        "defaultDecision": "deny",
        "modelTrainingDecision": "deny",
        "allowedGrantCells": [item.model_dump(mode="json") for item in cells],
    }
    payload["matrixHash"] = canonical_grant_matrix_hash(payload)
    return LicenseGrantMatrix.model_validate(payload)


def _closed_coverage_hash() -> str:
    payload = {
        "version": "content-license-coverage@v1",
        "components": [item.value for item in ContentComponent],
        "uses": [item.value for item in ContentUse],
        "channels": [item.value for item in DeliveryChannel],
        "audiences": [item.value for item in Audience],
        "transforms": [item.value for item in ContentTransform],
        "territories": [item.value for item in Territory],
    }
    return sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _active_record(
    *,
    content_version_id: str,
    source_dataset_ids: tuple[str, ...],
    manifest_ref: str,
    manifest_hash: str,
    matrix: LicenseGrantMatrix,
    closed_coverage_hash: str,
    author: str,
    reviewer: str,
    now: datetime,
) -> ContentLicenseRecord:
    license_id = uuid5(
        NAMESPACE_URL, f"owner-catalog-license:{content_version_id}:{manifest_hash}"
    )
    rights = TransferableRights(
        sourceId="owner-catalog:" + ",".join(source_dataset_ids),
        term=LicenseTerm(
            termKind="revocable_open_term",
            effectiveAt=now - timedelta(seconds=1),
            expiresAt="not_applicable",
            renewalPolicyVersionRef="owner-catalog-review@v1",
        ),
        grantMatrix=matrix,
        distributionClass="licensed_transferable",
        rightsBasis="verified_agreement",
        agreementRef=manifest_ref,
        agreementHash=manifest_hash,
    )
    draft = ContentLicenseRecord(
        contentLicenseId=license_id,
        contentVersionId=content_version_id,
        revision=1,
        authorizationEpoch=0,
        state=LicenseState.IN_REVIEW,
        createdByActorId=author,
        closedCoverageVersionRef="content-license-coverage@v1",
        closedCoverageHash=closed_coverage_hash,
        rights=rights,
        evidenceManifestRef=manifest_ref,
        evidenceManifestHash=manifest_hash,
        recordCanonicalHash="0" * 64,
        createdAt=now,
        updatedAt=now,
    )
    draft = _seal(draft)
    receipt = ReviewerReceipt(
        reviewerId=reviewer,
        decision="approved",
        reviewedRecordHash=draft.record_canonical_hash,
        evidenceManifestHash=manifest_hash,
        grantMatrixHash=matrix.matrix_hash,
        reviewedAt=now,
    )
    return _seal(
        draft.model_copy(
            update={
                "state": LicenseState.ACTIVE,
                "authorization_epoch": 1,
                "reviewer_receipt": receipt,
            }
        )
    )


def _seal(record: ContentLicenseRecord) -> ContentLicenseRecord:
    candidate = record.model_copy(update={"record_canonical_hash": "0" * 64})
    return ContentLicenseRecord.model_validate(
        candidate.model_copy(
            update={"record_canonical_hash": canonical_record_hash(candidate)}
        ).model_dump(by_alias=True, mode="json")
    )


_INSERT_VERSIONS_SQL = """
insert into product.content_license_record_versions (
  record_version_id, content_license_id, content_version_id, revision,
  authorization_epoch, state, created_by_actor_id, closed_coverage_hash,
  evidence_manifest_hash, grant_matrix_hash, record_canonical_hash,
  record_payload, reviewer_receipt, supersedes_content_license_id,
  supersedes_record_version_id, created_at, updated_at
) values (
  cast(:record_version_id as uuid), cast(:content_license_id as uuid),
  :content_version_id, :revision, :authorization_epoch, :state,
  :created_by_actor_id, :closed_coverage_hash, :evidence_manifest_hash,
  :grant_matrix_hash, :record_canonical_hash, cast(:record_payload as jsonb),
  cast(:reviewer_receipt as jsonb), null, null, :created_at, :updated_at
) on conflict (content_license_id, revision) do nothing
"""


_INSERT_HEADS_SQL = """
insert into product.content_license_heads (
  content_license_id, content_version_id, record_version_id, revision,
  authorization_epoch, state, updated_at
) values (
  cast(:content_license_id as uuid), :content_version_id,
  cast(:record_version_id as uuid), :revision, :authorization_epoch, :state,
  :updated_at
) on conflict (content_license_id) do nothing
"""
