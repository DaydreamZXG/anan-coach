from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import subprocess
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.contracts.external_learning.models import (
    AccountPrivateRights,
    AnswerIngestionVersion,
    Audience,
    CandidateState,
    ContentComponent,
    ContentLicenseRecord,
    ContentMatchVersion,
    ContentTransform,
    ContentUse,
    DeliveryChannel,
    LicenseGrantCell,
    LicenseGrantMatrix,
    LicenseState,
    LicenseTerm,
    LearningTrack,
    MatchCandidate,
    RecognitionKind,
    RecognitionRevision,
    SourceArtifactState,
    SourceArtifactVersion,
    SourceKind,
    Territory,
    canonical_grant_matrix_hash,
    external_learning_contract_catalog,
)


ROOT = Path(__file__).resolve().parents[4]
NOW = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)


def _grant_matrix(*cells: LicenseGrantCell) -> LicenseGrantMatrix:
    payload: dict[str, object] = {
        "matrixVersionRef": "license-grant-matrix@v1",
        "matrixHash": "0" * 64,
        "componentsCoverage": [item.value for item in ContentComponent],
        "usesCoverage": [item.value for item in ContentUse],
        "channelsCoverage": [item.value for item in DeliveryChannel],
        "audiencesCoverage": [item.value for item in Audience],
        "transformsCoverage": [item.value for item in ContentTransform],
        "territoriesCoverage": [item.value for item in Territory],
        "defaultDecision": "deny",
        "modelTrainingDecision": "deny",
        "allowedGrantCells": [
            cell.model_dump(by_alias=True, mode="json") for cell in cells
        ],
    }
    payload["matrixHash"] = canonical_grant_matrix_hash(payload)
    return LicenseGrantMatrix.model_validate(payload)


def _private_rights(*cells: LicenseGrantCell) -> AccountPrivateRights:
    return AccountPrivateRights(
        sourceId="private-upload:artifact-1",
        term=LicenseTerm(
            termKind="revocable_open_term",
            effectiveAt=NOW,
            expiresAt="not_applicable",
            renewalPolicyVersionRef="private-source-retention@v1",
        ),
        grantMatrix=_grant_matrix(*cells),
        distributionClass="account_private",
        rightsBasis="user_private_purpose_limited",
        subjectScopeRef="account:user-1",
        userAuthorizationReceiptRef="receipt:user-upload-1",
        userAuthorizationReceiptHash="1" * 64,
    )


def test_contract_models_reject_unknown_fields_and_wrong_versions() -> None:
    artifact = SourceArtifactVersion(
        artifactVersionId=uuid4(),
        artifactId=uuid4(),
        ownerUserId=uuid4(),
        versionNo=1,
        sourceKind=SourceKind.CAMERA_IMAGE,
        state=SourceArtifactState.UPLOADED,
        objectRef="private/user/object.jpg",
        mimeType="image/jpeg",
        contentSha256="a" * 64,
        byteSize=128,
        createdAt=NOW,
    )
    payload = artifact.model_dump(by_alias=True, mode="json")
    payload["unexpected"] = True
    with pytest.raises(ValidationError):
        SourceArtifactVersion.model_validate(payload)

    payload.pop("unexpected")
    payload["contractVersion"] = "source-artifact@v2"
    with pytest.raises(ValidationError):
        SourceArtifactVersion.model_validate(payload)


def test_unconfirmed_candidates_cannot_claim_confirmed_bindings() -> None:
    with pytest.raises(ValidationError):
        RecognitionRevision(
            recognitionRevisionId=uuid4(),
            artifactVersionId=uuid4(),
            ownerUserId=uuid4(),
            revisionNo=1,
            recognitionKind=RecognitionKind.PRINTED_OCR,
            state=CandidateState.CANDIDATE,
            processorPolicyVersion="printed-ocr@v1",
            processorRoute="unapproved:block",
            inputSha256="a" * 64,
            outputSha256="b" * 64,
            candidatePayload={"text": "候选文本"},
            expiresAt=NOW + timedelta(days=7),
            confirmedAt=NOW,
            createdAt=NOW,
        )

    candidate = MatchCandidate(
        contentVersionId="question-version-1",
        matchKind="exact",
        score=1,
        evidenceRefs=("fingerprint:1",),
    )
    with pytest.raises(ValidationError):
        ContentMatchVersion(
            contentMatchVersionId=uuid4(),
            contentMatchId=uuid4(),
            artifactVersionId=uuid4(),
            recognitionRevisionId=uuid4(),
            ownerUserId=uuid4(),
            versionNo=1,
            state="candidate",
            candidates=(candidate,),
            confirmedContentVersionId="question-version-1",
            matchingPolicyVersion="hybrid-match@v1",
            inputFingerprint="c" * 64,
            createdAt=NOW,
        )


def test_answer_ingestion_returns_to_the_track_owner_only_after_confirmation() -> None:
    with pytest.raises(ValidationError):
        AnswerIngestionVersion(
            ingestionVersionId=uuid4(),
            ingestionId=uuid4(),
            ownerUserId=uuid4(),
            versionNo=1,
            sourceArtifactVersionIds=(uuid4(),),
            track=LearningTrack.XINGCE,
            inputMode="answer_string",
            state="candidate",
            candidatePayload={"answers": "ABCD"},
            targetWriter="essay_learning",
            writerContractVersion="essay-external-learning-open@v1",
            sourceLineageRefs=("packet:item:1",),
            createdAt=NOW,
        )

    with pytest.raises(ValidationError):
        AnswerIngestionVersion(
            ingestionVersionId=uuid4(),
            ingestionId=uuid4(),
            ownerUserId=uuid4(),
            versionNo=1,
            sourceArtifactVersionIds=(uuid4(),),
            track=LearningTrack.XINGCE,
            inputMode="answer_string",
            state="committed",
            candidatePayload={"answers": "ABCD"},
            targetWriter="xingce_learning",
            writerContractVersion="xingce-external-learning-open@v1",
            sourceLineageRefs=("packet:item:1",),
            confirmedAt=NOW,
            createdAt=NOW,
        )


def test_private_content_cannot_grant_forwarding_cross_user_or_training() -> None:
    forbidden = (
        LicenseGrantCell(
            component=ContentComponent.QUESTION_PROMPT,
            use=ContentUse.SHARE_FORWARD,
            channel=DeliveryChannel.IMAGE_EXPORT,
            audience=Audience.AUTHORIZED_RECIPIENT,
            transform=ContentTransform.VERBATIM,
            territory=Territory.MAINLAND_CHINA,
        ),
        LicenseGrantCell(
            component=ContentComponent.USER_SUBMISSION,
            use=ContentUse.MODEL_TRAINING,
            channel=DeliveryChannel.AI_PROCESSOR,
            audience=Audience.ACCOUNT_OWNER,
            transform=ContentTransform.MODEL_TRAINING,
            territory=Territory.MAINLAND_CHINA,
        ),
    )
    for cell in forbidden:
        with pytest.raises(ValidationError):
            _private_rights(cell)


def test_license_record_requires_explicit_authority_for_active_state() -> None:
    with pytest.raises(ValidationError):
        ContentLicenseRecord(
            contentLicenseId=uuid4(),
            contentVersionId="question-version-1",
            revision=1,
            authorizationEpoch=0,
            state=LicenseState.ACTIVE,
            createdByActorId="author-1",
            closedCoverageVersionRef="coverage@v1",
            closedCoverageHash="2" * 64,
            rights=_private_rights(),
            evidenceManifestRef="evidence:1",
            evidenceManifestHash="3" * 64,
            recordCanonicalHash="4" * 64,
            createdAt=NOW,
            updatedAt=NOW,
        )


def test_wechat_catalog_matches_backend_contract_catalog() -> None:
    script = (
        "import('./apps/wechat/miniprogram/api/external-learning-contracts.ts')"
        ".then(m => process.stdout.write(JSON.stringify("
        "m.EXTERNAL_LEARNING_CONTRACT_CATALOG)))"
    )
    result = subprocess.run(
        ["node", "--no-warnings", "-e", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(result.stdout) == external_learning_contract_catalog()
