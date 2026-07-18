from __future__ import annotations

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.domains.external_ingestion.recognition.models import ArtifactInput


class EssayHandwritingRepository:
    def bound_answer_artifact(
        self,
        session: Session,
        *,
        user_id: UUID,
        artifact_version_id: UUID,
    ) -> ArtifactInput | None:
        row = session.execute(
            text(
                """
                select distinct artifact.artifact_version_id,
                       artifact.owner_user_id, artifact.state, artifact.source_kind,
                       artifact.object_ref, artifact.mime_type,
                       artifact.content_sha256, artifact.page_count
                from learning.essay_answer_versions answer
                join learning.source_artifact_versions artifact
                  on artifact.artifact_version_id = answer.source_artifact_version_id
                join learning.recognition_revisions recognition
                  on recognition.recognition_revision_id = answer.recognition_revision_id
                 and recognition.artifact_version_id = artifact.artifact_version_id
                 and recognition.owner_user_id = answer.user_id
                where answer.user_id = :user_id
                  and answer.source_type = 'ocr_confirmed'
                  and answer.source_artifact_version_id = :artifact_version_id
                  and artifact.owner_user_id = :user_id
                  and artifact.state = 'available'
                  and artifact.source_kind in ('camera_image', 'image_import')
                  and recognition.recognition_kind = 'handwriting_ocr'
                  and recognition.state = 'confirmed'
                limit 1
                """
            ),
            {
                "user_id": str(user_id),
                "artifact_version_id": str(artifact_version_id),
            },
        ).mappings().one_or_none()
        return ArtifactInput.model_validate(dict(row)) if row is not None else None
