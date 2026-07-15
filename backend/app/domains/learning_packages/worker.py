from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Callable
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.contracts.external_learning.models import OfflinePacketVersion
from app.domains.learning_packages.renderer import PacketRenderer, PacketRendererError
from app.domains.learning_packages.repository import LearningPackageRepository
from app.domains.learning_packages.schemas import PacketRenderRequest
from app.domains.learning_packages.service import LicenseEvaluator
from app.platform.jobs import BackgroundJob, BackgroundJobRepository
from app.platform.jobs import JobContractError
from app.platform.outbox import OutboxRepository


class PacketRenderWorker:
    JOB_TYPES = ("offline_packet_render_v1",)

    def __init__(
        self,
        *,
        renderer: PacketRenderer,
        license_evaluator: LicenseEvaluator,
        repository: LearningPackageRepository | None = None,
        jobs: BackgroundJobRepository | None = None,
        outbox: OutboxRepository | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._renderer = renderer
        self._licenses = license_evaluator
        self._repository = repository or LearningPackageRepository()
        self._jobs = jobs or BackgroundJobRepository()
        self._outbox = outbox or OutboxRepository()
        self._clock = clock or (lambda: datetime.now(UTC))

    def process_next(self, session: Session) -> BackgroundJob | None:
        job = self._jobs.claim_next(session, job_types=self.JOB_TYPES, now=self._clock())
        if job is None:
            session.rollback()
            return None
        session.commit()
        if job.claim_token is None:
            raise JobContractError("claimed packet job has no claim token")
        try:
            completed = self._process(session, job=job)
            session.commit()
            return completed
        except PacketRendererError as exc:
            session.rollback()
            failed = self._jobs.fail(
                session,
                job_id=job.job_id,
                claim_token=job.claim_token,
                error_code=exc.code,
                error_message=str(exc),
                retryable=exc.retryable,
                retry_after=timedelta(seconds=30),
                now=self._clock(),
            )
            if failed.status == "failed":
                self._terminal_failure(session, job=job, error_code=exc.code)
            session.commit()
            return failed
        except Exception as exc:
            session.rollback()
            failed = self._jobs.fail(
                session,
                job_id=job.job_id,
                claim_token=job.claim_token,
                error_code="packet_worker_unexpected",
                error_message=type(exc).__name__,
                retryable=True,
                retry_after=timedelta(seconds=30),
                now=self._clock(),
            )
            if failed.status == "failed":
                self._terminal_failure(
                    session,
                    job=job,
                    error_code="packet_worker_unexpected",
                )
            session.commit()
            raise

    def _process(self, session: Session, *, job: BackgroundJob) -> BackgroundJob:
        packet_version_id = UUID(str(job.payload["packetVersionId"]))
        initial = self._repository.packet_by_version_id(
            session, packet_version_id=packet_version_id
        )
        if initial is None:
            raise PacketRendererError("packet_missing", "packet render input is missing", retryable=False)
        self._repository.lock(session, key=f"offline-packet:{initial.packet_id}")
        current = self._repository.current_packet(session, packet_id=initial.packet_id, for_update=True)
        if current is None:
            raise PacketRendererError("packet_missing", "packet aggregate is missing", retryable=False)
        if current.state == "ready":
            return self._jobs.succeed(
                session,
                job_id=job.job_id,
                claim_token=job.claim_token,
                result={"packetVersionId": str(current.packet_version_id), "artifactRef": current.artifact_ref},
                completed_at=self._clock(),
            )
        if current.state not in {"requested", "generating"}:
            raise PacketRendererError("packet_not_renderable", "packet is no longer renderable", retryable=False)
        now = self._clock()
        if current.expires_at is None or now >= current.expires_at:
            self._append_state(session, current=current, state="expired", artifact_ref=None)
            raise PacketRendererError("packet_expired", "packet expired before rendering", retryable=False)
        for snapshot_ref in current.license_snapshot_refs:
            status = self._licenses.snapshot_status(snapshot_id=UUID(snapshot_ref))
            if not status.valid:
                self._append_state(session, current=current, state="revoked", artifact_ref=None)
                raise PacketRendererError("packet_license_revoked", "packet authorization is no longer valid", retryable=False)
        if current.state == "requested":
            current = self._append_state(session, current=current, state="generating", artifact_ref=None)
        render_request = PacketRenderRequest.model_validate(job.payload["renderRequest"])
        if render_request.manifest_sha256 != current.manifest_sha256:
            raise PacketRendererError("packet_manifest_mismatch", "packet manifest changed", retryable=False)
        result = self._renderer.render(
            request=render_request,
            idempotency_key=f"offline-packet-render:{initial.packet_version_id}",
        )
        if not _private_artifact_ref(result.artifact_ref):
            raise PacketRendererError("packet_artifact_not_private", "renderer returned a public artifact", retryable=False)
        ready = self._append_state(session, current=current, state="ready", artifact_ref=result.artifact_ref)
        self._outbox.append(
            session,
            deduplication_key=f"offline-packet:{ready.packet_version_id}:ready",
            aggregate_type="offline_packet",
            aggregate_id=str(ready.packet_id),
            event_type="offline_packet.ready",
            payload={
                "packetVersionId": str(ready.packet_version_id),
                "manifestSha256": ready.manifest_sha256,
                "artifactSha256": result.artifact_sha256,
                "providerRequestId": result.provider_request_id,
            },
        )
        if job.claim_token is None:
            raise RuntimeError("claimed packet job lost its claim token")
        return self._jobs.succeed(
            session,
            job_id=job.job_id,
            claim_token=job.claim_token,
            result={
                "packetVersionId": str(ready.packet_version_id),
                "artifactRef": ready.artifact_ref,
                "artifactSha256": result.artifact_sha256,
            },
            completed_at=now,
        )

    def _terminal_failure(self, session: Session, *, job: BackgroundJob, error_code: str) -> None:
        initial = self._repository.packet_by_version_id(
            session, packet_version_id=UUID(str(job.payload["packetVersionId"]))
        )
        if initial is None:
            return
        self._repository.lock(session, key=f"offline-packet:{initial.packet_id}")
        current = self._repository.current_packet(session, packet_id=initial.packet_id, for_update=True)
        if current is None or current.state in {"ready", "failed", "expired", "revoked"}:
            return
        failed = self._append_state(session, current=current, state="failed", artifact_ref=None)
        self._outbox.append(
            session,
            deduplication_key=f"offline-packet:{failed.packet_version_id}:failed",
            aggregate_type="offline_packet",
            aggregate_id=str(failed.packet_id),
            event_type="offline_packet.failed",
            payload={"packetVersionId": str(failed.packet_version_id), "errorCode": error_code},
        )

    def _append_state(
        self,
        session: Session,
        *,
        current: OfflinePacketVersion,
        state: str,
        artifact_ref: str | None,
    ) -> OfflinePacketVersion:
        packet = current.model_copy(
            update={
                "packet_version_id": uuid4(),
                "version_no": current.version_no + 1,
                "state": state,
                "artifact_ref": artifact_ref,
                "created_at": self._clock(),
            }
        )
        self._repository.insert_packet(session, packet=packet)
        return packet


def _private_artifact_ref(value: str) -> bool:
    parsed = urlsplit(value)
    return parsed.scheme == "tos" and bool(parsed.netloc) and parsed.path.startswith("/private/")
