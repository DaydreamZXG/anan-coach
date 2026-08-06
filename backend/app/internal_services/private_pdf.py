from __future__ import annotations

from pypdf import PdfReader

from app.core.config import Settings
from app.internal_services.private_objects import PrivateObjectReader


class PrivatePdfPageCounter:
    """Derive PDF metadata from the immutable private object, never from client claims."""

    def __init__(
        self,
        settings: Settings,
        *,
        reader: PrivateObjectReader | None = None,
    ) -> None:
        self._reader = reader or PrivateObjectReader(settings)

    def count_pages(
        self,
        *,
        object_ref: str,
        expected_sha256: str,
        maximum_bytes: int,
    ) -> int:
        with self._reader.open(
            object_ref=object_ref,
            expected_sha256=expected_sha256,
            maximum_bytes=maximum_bytes,
            prefix="private-upload-pdf-",
            suffix=".pdf",
        ) as path:
            document = PdfReader(str(path), strict=False)
            if document.is_encrypted:
                raise ValueError("encrypted PDF is not supported")
            page_count = len(document.pages)
        if page_count <= 0:
            raise ValueError("PDF has no readable pages")
        return page_count
