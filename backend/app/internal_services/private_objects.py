from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path
import tempfile
from typing import Any
from urllib.parse import urlsplit

import tos

from app.core.config import Settings


class PrivateObjectReader:
    """Streams immutable private TOS objects into a bounded temporary file."""

    def __init__(self, settings: Settings, *, client: Any | None = None) -> None:
        required = (
            settings.tos_access_key,
            settings.tos_secret_key,
            settings.tos_endpoint,
            settings.tos_region,
            settings.tos_bucket,
        )
        if not all(value.strip() for value in required):
            raise ValueError("TOS private object reader configuration is incomplete")
        self._bucket = settings.tos_bucket
        self._client = client or tos.TosClientV2(
            settings.tos_access_key,
            settings.tos_secret_key,
            settings.tos_endpoint,
            settings.tos_region,
        )

    @contextmanager
    def open(
        self,
        *,
        object_ref: str,
        expected_sha256: str,
        maximum_bytes: int,
        prefix: str,
        suffix: str = "",
    ) -> Iterator[Path]:
        if maximum_bytes <= 0:
            raise ValueError("private object byte limit must be positive")
        key = private_object_key(object_ref, bucket=self._bucket)
        output = self._client.get_object(self._bucket, key)
        try:
            with tempfile.NamedTemporaryFile(
                prefix=prefix,
                suffix=suffix,
                delete=True,
            ) as target:
                digest = sha256()
                total = 0
                while chunk := output.read(1024 * 1024):
                    total += len(chunk)
                    if total > maximum_bytes:
                        raise ValueError("private object exceeds processing byte limit")
                    digest.update(chunk)
                    target.write(chunk)
                target.flush()
                if digest.hexdigest() != expected_sha256:
                    raise ValueError(
                        "private object digest does not match immutable upload contract"
                    )
                yield Path(target.name)
        finally:
            close = getattr(output, "close", None)
            if callable(close):
                close()


def private_object_key(value: str, *, bucket: str) -> str:
    parsed = urlsplit(value)
    key = parsed.path.removeprefix("/")
    if (
        parsed.scheme != "tos"
        or parsed.netloc != bucket
        or not key.startswith("private/")
        or parsed.query
        or parsed.fragment
        or any(part in {"", ".", ".."} for part in key.split("/"))
    ):
        raise ValueError("object reference is outside the approved private bucket")
    return key
