from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import subprocess

from pydantic import BaseModel, ConfigDict, Field
from pypdf import PdfReader

from app.core.config import Settings
from app.internal_services.private_objects import PrivateObjectReader


class ScanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    contract_version: str = Field(alias="contractVersion", pattern="^private-object-scan@v1$")
    object_ref: str = Field(alias="objectRef", min_length=1, max_length=500)
    expected_sha256: str = Field(alias="expectedSha256", pattern=r"^[0-9a-f]{64}$")
    provider_route: str = Field(alias="providerRoute", min_length=1, max_length=160)


class InternalObjectScanner:
    MAXIMUM_BYTES = 50 * 1024 * 1024

    def __init__(
        self,
        settings: Settings,
        *,
        reader: PrivateObjectReader | None = None,
    ) -> None:
        self._settings = settings
        self._reader = reader or PrivateObjectReader(settings)

    def scan(self, request: ScanRequest, *, scan_id: str) -> dict[str, object]:
        with self._reader.open(
            object_ref=request.object_ref,
            expected_sha256=request.expected_sha256,
            maximum_bytes=self.MAXIMUM_BYTES,
            prefix="anan-scan-",
        ) as target:
            mime_type = subprocess.run(
                ["/usr/bin/file", "--brief", "--mime-type", str(target)],
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            ).stdout.strip()
            if mime_type not in {"image/jpeg", "image/png", "application/pdf"}:
                raise ValueError("private object type is not accepted")
            scan = subprocess.run(
                ["/usr/bin/clamdscan", "--fdpass", "--no-summary", str(target)],
                capture_output=True,
                text=True,
                timeout=90,
            )
            if scan.returncode == 0:
                verdict = "clean"
            elif scan.returncode == 1:
                verdict = "malicious"
            else:
                raise RuntimeError("ClamAV did not return a conclusive verdict")
            page_count = (
                len(PdfReader(str(target), strict=True).pages)
                if mime_type == "application/pdf"
                else None
            )
        definition_path = _definition_path()
        definitions_updated_at = datetime.fromtimestamp(
            definition_path.stat().st_mtime, tz=UTC
        )
        version = subprocess.run(
            ["/usr/bin/clamdscan", "--version"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()
        now = datetime.now(UTC)
        return {
            "contractVersion": "private-object-scan-result@v1",
            "scanId": scan_id,
            "verdict": verdict,
            "detectedMimeType": mime_type,
            "definitionVersion": version,
            "definitionsUpdatedAt": definitions_updated_at.isoformat(),
            "scannedAt": now.isoformat(),
            "contentSha256": request.expected_sha256,
            "detectedPageCount": page_count,
        }

def _definition_path() -> Path:
    candidates = sorted(
        (*Path("/var/lib/clamav").glob("*.cld"), *Path("/var/lib/clamav").glob("*.cvd")),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        raise RuntimeError("ClamAV definitions are unavailable")
    return candidates[0]
