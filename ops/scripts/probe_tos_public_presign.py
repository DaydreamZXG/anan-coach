#!/usr/bin/env python3
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

from app.core.config import get_settings
from app.domains.external_ingestion.upload.tos_sdk import VolcengineTosSdkFacade


def main() -> None:
    settings = get_settings()
    public_endpoint = settings.tos_presign_endpoint.strip()
    internal_endpoint = settings.tos_endpoint.strip()
    if not public_endpoint:
        raise RuntimeError("TOS_PRESIGN_ENDPOINT is required")
    public_host = (urlsplit(public_endpoint).hostname or "").casefold()
    internal_host = (urlsplit(internal_endpoint).hostname or "").casefold()
    if (
        urlsplit(public_endpoint).scheme != "https"
        or not public_host
        or public_host == internal_host
        or public_host.endswith(".ivolces.com")
    ):
        raise RuntimeError(
            "TOS_PRESIGN_ENDPOINT must be a distinct public HTTPS TOS endpoint"
        )

    facade = VolcengineTosSdkFacade.from_credentials(
        access_key=settings.tos_access_key,
        secret_key=settings.tos_secret_key,
        endpoint=internal_endpoint,
        presign_endpoint=public_endpoint,
        region=settings.tos_region,
    )
    signed = facade.presign_private_put(
        bucket=settings.tos_bucket,
        key="private/release-probes/public-presign.txt",
        mime_type="text/plain",
        byte_size=1,
        content_sha256="0" * 64,
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
    )
    signed_url = urlsplit(signed.url)
    if (
        signed_url.scheme != "https"
        or not signed_url.hostname
        or signed_url.hostname.casefold().endswith(".ivolces.com")
        or public_host not in signed_url.hostname.casefold()
    ):
        raise RuntimeError("TOS client presign did not use the public endpoint")
    print(f"TOS public presign probe passed for {signed_url.hostname}")


if __name__ == "__main__":
    main()
