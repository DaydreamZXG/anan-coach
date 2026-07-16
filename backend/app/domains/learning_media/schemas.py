from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class LearningMediaModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class StartLearningMediaUploadCommand(LearningMediaModel):
    purpose: Literal["reflection_voice"]
    original_filename: str = Field(alias="originalFilename", min_length=1, max_length=255)
    mime_type: Literal["audio/mpeg", "audio/mp4", "audio/aac"] = Field(alias="mimeType")
    byte_size: int = Field(alias="byteSize", ge=1, le=15 * 1024 * 1024)
    content_sha256: str = Field(alias="contentSha256", pattern=r"^[0-9a-f]{64}$")
    duration_milliseconds: int = Field(
        alias="durationMilliseconds", ge=1, le=180_000
    )

    @model_validator(mode="after")
    def validate_file_contract(self) -> "StartLearningMediaUploadCommand":
        filename = self.original_filename.strip()
        if (
            not filename
            or "/" in filename
            or "\\" in filename
            or any(ord(character) < 32 for character in filename)
        ):
            raise ValueError("originalFilename must be a safe base name")
        extension = filename.rsplit(".", 1)[-1].casefold() if "." in filename else ""
        allowed = {
            "audio/mpeg": {"mp3"},
            "audio/mp4": {"m4a", "mp4"},
            "audio/aac": {"aac"},
        }
        if extension not in allowed[self.mime_type]:
            raise ValueError("audio MIME type and filename extension do not match")
        self.original_filename = filename
        return self


class LearningMediaAuthorization(LearningMediaModel):
    object_ref: str = Field(alias="objectRef")
    url: str
    required_headers: dict[str, str] = Field(alias="requiredHeaders")
    expires_at: datetime = Field(alias="expiresAt")


class LearningMediaUploadSession(LearningMediaModel):
    upload_session_id: UUID = Field(alias="uploadSessionId")
    artifact_id: UUID = Field(alias="artifactId")
    artifact_version_id: UUID = Field(alias="artifactVersionId")
    state: Literal[
        "awaiting_upload", "scanning", "available", "quarantined", "failed", "expired"
    ]
    revision: int = Field(ge=1)
    purpose: Literal["reflection_voice"]
    original_filename: str = Field(alias="originalFilename")
    mime_type: str = Field(alias="mimeType")
    byte_size: int = Field(alias="byteSize", ge=1)
    content_sha256: str = Field(alias="contentSha256")
    duration_milliseconds: int = Field(alias="durationMilliseconds", ge=1)
    failure_code: str | None = Field(default=None, alias="failureCode")
    expires_at: datetime = Field(alias="expiresAt")
    updated_at: datetime = Field(alias="updatedAt")


class LearningMediaArtifact(LearningMediaModel):
    artifact_id: UUID = Field(alias="artifactId")
    artifact_version_id: UUID = Field(alias="artifactVersionId")
    version_no: int = Field(alias="versionNo", ge=1)
    purpose: Literal["reflection_voice"]
    state: Literal["available", "deleted"]
    mime_type: str = Field(alias="mimeType")
    byte_size: int = Field(alias="byteSize", ge=1)
    content_sha256: str = Field(alias="contentSha256")
    duration_milliseconds: int = Field(alias="durationMilliseconds", ge=1)
    retention_policy_version: str = Field(alias="retentionPolicyVersion")
    created_at: datetime = Field(alias="createdAt")


class LearningMediaUploadProjection(LearningMediaModel):
    contract_version: Literal["learning-media-upload@v1"] = Field(
        default="learning-media-upload@v1", alias="contractVersion"
    )
    session: LearningMediaUploadSession
    authorization: LearningMediaAuthorization | None = None
    artifact: LearningMediaArtifact | None = None
    replayed: bool
