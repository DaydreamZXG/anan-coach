from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import tos
import pytest

from app.domains.external_ingestion.upload.models import (
    UploadContractError,
    UploadedPart,
)
from app.domains.external_ingestion.upload.tos_sdk import VolcengineTosSdkFacade


NOW = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)


class NotFound(RuntimeError):
    status_code = 404
    code = "NoSuchKey"


class FakeOfficialClient:
    def __init__(self) -> None:
        self.active: dict[str, SimpleNamespace] = {}
        self.objects: dict[str, SimpleNamespace] = {}
        self.parts: dict[str, tuple[UploadedPart, ...]] = {}
        self.create_calls = 0
        self.presign_calls = 0
        self.complete_calls = 0
        self.abort_calls = 0

    def pre_signed_url(self, **kwargs):
        self.presign_calls += 1
        headers = dict(kwargs["header"])
        headers["host"] = "bucket.tos.example.invalid"
        return SimpleNamespace(
            signed_url=f"https://bucket.tos.example.invalid/{kwargs['key']}?signed=1",
            signed_header=headers,
        )

    def list_multipart_uploads(self, bucket, **kwargs):
        prefix = kwargs["prefix"]
        uploads = [item for item in self.active.values() if item.key.startswith(prefix)]
        return SimpleNamespace(is_truncated=False, uploads=uploads)

    def create_multipart_upload(self, bucket, key, **kwargs):
        self.create_calls += 1
        upload_id = f"upload-{self.create_calls}"
        self.active[key] = SimpleNamespace(
            key=key,
            upload_id=upload_id,
            meta=dict(kwargs["meta"]),
            content_type=kwargs["content_type"],
        )
        return SimpleNamespace(upload_id=upload_id)

    def list_parts(self, bucket, key, upload_id, **kwargs):
        items = [
            SimpleNamespace(
                part_number=item.part_number, size=item.byte_size, etag=item.etag
            )
            for item in self.parts.get(upload_id, ())
        ]
        return SimpleNamespace(is_truncated=False, parts=items)

    def complete_multipart_upload(self, bucket, key, upload_id, **kwargs):
        self.complete_calls += 1
        active = self.active.pop(key)
        total = sum(item.byte_size for item in self.parts[upload_id])
        self.objects[key] = _head(
            byte_size=total,
            content_type=active.content_type,
            meta=active.meta,
        )
        return SimpleNamespace()

    def abort_multipart_upload(self, bucket, key, upload_id):
        self.abort_calls += 1
        active = self.active.get(key)
        if active is None or active.upload_id != upload_id:
            raise NotFound()
        del self.active[key]

    def head_object(self, bucket, key):
        if key not in self.objects:
            raise NotFound()
        return self.objects[key]

    def get_object_acl(self, bucket, key):
        return SimpleNamespace(grants=[])

    def delete_object(self, bucket, key):
        self.objects.pop(key, None)

    def copy_object(self, bucket, key, src_bucket, src_key, **kwargs):
        source = self.objects[src_key]
        self.objects[key] = _head(
            byte_size=source.content_length,
            content_type=kwargs["content_type"],
            meta=dict(kwargs["meta"]),
        )


def _head(*, byte_size: int, content_type: str, meta: dict[str, str]):
    return SimpleNamespace(
        content_length=byte_size,
        content_type=content_type,
        meta=meta,
        header={"x-tos-server-side-encryption": "AES256"},
        sse_algorithm=None,
    )


def facade(client: FakeOfficialClient) -> VolcengineTosSdkFacade:
    return VolcengineTosSdkFacade(
        client=client,
        tos_module=tos,
        region="cn-guangzhou",
        endpoint="https://tos-cn-guangzhou.volces.com",
        clock=lambda: NOW,
    )


def test_official_sdk_presign_binds_every_single_and_part_integrity_header() -> None:
    sdk = VolcengineTosSdkFacade.from_credentials(
        access_key="AK_TEST_ONLY",
        secret_key="SK_TEST_ONLY",
        endpoint="https://tos-cn-guangzhou.volces.com",
        region="cn-guangzhou",
        clock=lambda: NOW,
    )
    expiry = NOW + timedelta(minutes=10)

    single = sdk.presign_private_put(
        bucket="bucket-test",
        key="private/external-learning/a.jpg",
        mime_type="image/jpeg",
        byte_size=100,
        content_sha256="a" * 64,
        expires_at=expiry,
    )
    part = sdk.presign_multipart_part(
        bucket="bucket-test",
        key="private/external-learning/a.pdf",
        provider_upload_id="upload-1",
        part_number=1,
        byte_size=100,
        content_sha256="b" * 64,
        expires_at=expiry,
    )

    assert single.url.startswith("https://")
    assert single.signed_headers["content-length"] == "100"
    assert single.signed_headers["x-tos-content-sha256"] == "a" * 64
    assert single.signed_headers["x-tos-acl"] == "private"
    assert single.signed_headers["x-tos-server-side-encryption"] == "AES256"
    assert part.signed_headers["x-tos-content-sha256"] == "b" * 64
    assert "uploadId=upload-1" in part.url
    assert "partNumber=1" in part.url


def test_client_presign_uses_public_endpoint_without_moving_server_operations() -> None:
    internal = FakeOfficialClient()
    public = FakeOfficialClient()
    sdk = VolcengineTosSdkFacade(
        client=internal,
        presign_client=public,
        tos_module=tos,
        region="cn-guangzhou",
        endpoint="https://tos-cn-guangzhou.ivolces.com",
        presign_endpoint="https://tos-cn-guangzhou.volces.com",
        clock=lambda: NOW,
    )

    signed = sdk.presign_private_put(
        bucket="bucket-test",
        key="private/external-learning/public-client.jpg",
        mime_type="image/jpeg",
        byte_size=100,
        content_sha256="a" * 64,
        expires_at=NOW + timedelta(minutes=10),
    )

    assert signed.url.startswith("https://bucket.tos.example.invalid/")
    assert internal.presign_calls == 0
    assert public.presign_calls == 1


def test_official_facade_multipart_init_complete_and_abort_are_replay_safe() -> None:
    client = FakeOfficialClient()
    sdk = facade(client)
    operation_key = "c" * 64
    key = "private/external-learning/a.pdf"

    first = sdk.initiate_multipart(
        bucket="bucket-test",
        key=key,
        mime_type="application/pdf",
        content_sha256="d" * 64,
        operation_key=operation_key,
    )
    replay = sdk.initiate_multipart(
        bucket="bucket-test",
        key=key,
        mime_type="application/pdf",
        content_sha256="d" * 64,
        operation_key=operation_key,
    )
    assert replay == first
    assert client.create_calls == 1

    uploaded = (
        UploadedPart(part_number=1, byte_size=8, etag="etag-1"),
        UploadedPart(part_number=2, byte_size=2, etag="etag-2"),
    )
    client.parts[first] = uploaded
    assert sdk.list_multipart_parts(
        bucket="bucket-test", key=key, provider_upload_id=first
    ) == uploaded
    sdk.complete_multipart(
        bucket="bucket-test",
        key=key,
        provider_upload_id=first,
        parts=uploaded,
        operation_key=operation_key,
    )
    sdk.complete_multipart(
        bucket="bucket-test",
        key=key,
        provider_upload_id=first,
        parts=uploaded,
        operation_key=operation_key,
    )
    assert client.complete_calls == 1
    assert sdk.head(bucket="bucket-test", key=key).content_sha256 == "d" * 64

    abort_key = "private/external-learning/b.pdf"
    abort_id = sdk.initiate_multipart(
        bucket="bucket-test",
        key=abort_key,
        mime_type="application/pdf",
        content_sha256="e" * 64,
        operation_key="f" * 64,
    )
    sdk.abort_multipart(
        bucket="bucket-test",
        key=abort_key,
        provider_upload_id=abort_id,
        operation_key="f" * 64,
    )
    sdk.abort_multipart(
        bucket="bucket-test",
        key=abort_key,
        provider_upload_id=abort_id,
        operation_key="f" * 64,
    )
    assert client.abort_calls == 2


def test_official_facade_requires_the_init_receipt_for_completion_recovery() -> None:
    client = FakeOfficialClient()
    sdk = facade(client)
    init_receipt = "a" * 64
    key = "private/external-learning/receipt-bound.pdf"
    upload_id = sdk.initiate_multipart(
        bucket="bucket-test",
        key=key,
        mime_type="application/pdf",
        content_sha256="b" * 64,
        operation_key=init_receipt,
    )
    uploaded = (UploadedPart(part_number=1, byte_size=8, etag="etag-1"),)
    client.parts[upload_id] = uploaded

    with pytest.raises(
        UploadContractError,
        match="lacks its operation receipt",
    ):
        sdk.complete_multipart(
            bucket="bucket-test",
            key=key,
            provider_upload_id=upload_id,
            parts=uploaded,
            operation_key="c" * 64,
        )

    # TOS may have accepted the first request before the response was rejected.
    # Replaying with the receipt persisted at init must recover without a second
    # provider-side completion.
    sdk.complete_multipart(
        bucket="bucket-test",
        key=key,
        provider_upload_id=upload_id,
        parts=uploaded,
        operation_key=init_receipt,
    )
    assert client.complete_calls == 1


def test_official_facade_quarantine_copy_is_attributable_and_replay_safe() -> None:
    client = FakeOfficialClient()
    sdk = facade(client)
    source = "private/external-learning/source.jpg"
    destination = "private/quarantine/external-learning/result.jpg"
    client.objects[source] = _head(
        byte_size=100,
        content_type="image/jpeg",
        meta={"content-sha256": "a" * 64},
    )

    sdk.quarantine(
        bucket="bucket-test",
        source_key=source,
        destination_key=destination,
        operation_key="b" * 64,
    )
    sdk.quarantine(
        bucket="bucket-test",
        source_key=source,
        destination_key=destination,
        operation_key="b" * 64,
    )

    assert source not in client.objects
    assert client.objects[destination].meta["quarantine-operation-key"] == "b" * 64
