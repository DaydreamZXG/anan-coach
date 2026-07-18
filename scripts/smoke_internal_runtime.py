from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import os
from uuid import uuid4

import httpx
import tos


def main() -> None:
    client = tos.TosClientV2(
        os.environ["TOS_ACCESS_KEY"],
        os.environ["TOS_SECRET_KEY"],
        os.environ["TOS_ENDPOINT"],
        os.environ["TOS_REGION"],
    )
    bucket = os.environ["TOS_BUCKET"]
    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
    )
    digest = sha256(png).hexdigest()
    scan_key = f"private/smoke/stage15-scanner-{uuid4()}.png"
    packet_key: str | None = None
    try:
        client.put_object(
            bucket,
            scan_key,
            content=png,
            content_length=len(png),
            content_sha256=digest,
            content_type="image/png",
            acl=tos.ACLType.ACL_Private,
            server_side_encryption="AES256",
            meta={"content-sha256": digest},
        )
        scan = httpx.post(
            "http://127.0.0.1:8000/internal-runtime/v1/private-object-scans",
            headers={
                "Authorization": "Bearer " + os.environ["UPLOAD_SCANNER_API_KEY"],
                "X-Scanner-Release": os.environ["UPLOAD_SCANNER_RELEASE_ID"],
            },
            json={
                "contractVersion": "private-object-scan@v1",
                "objectRef": f"tos://{bucket}/{scan_key}",
                "expectedSha256": digest,
                "providerRoute": os.environ["UPLOAD_SCANNER_ROUTE"],
            },
            timeout=120,
        )
        scan.raise_for_status()
        scan_data = scan.json()
        print(
            "scanner={} mime={} definitions={}".format(
                scan_data["verdict"],
                scan_data["detectedMimeType"],
                str(scan_data["definitionVersion"]).split("/")[0],
            )
        )
        packet_id = str(uuid4())
        packet = httpx.post(
            "http://127.0.0.1:8000/internal-runtime/v1/private-packets/render",
            headers={
                "Authorization": "Bearer " + os.environ["PACKET_RENDERER_API_KEY"],
                "X-Renderer-Release": os.environ["PACKET_RENDERER_RELEASE_ID"],
                "Idempotency-Key": "stage15-renderer-smoke",
            },
            json={
                "contractVersion": "private-packet-render@v1",
                "packetVersionId": packet_id,
                "ownerSubjectRef": "a" * 64,
                "renderFormat": "pdf",
                "manifestSha256": "b" * 64,
                "expiresAt": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
                "contents": [
                    {
                        "questionId": "smoke-q1",
                        "contentVersionId": "c" * 64,
                        "orderNo": 1,
                        "answerBindingCode": "smoke-answer-binding",
                        "track": "xingce",
                        "title": "资料分析",
                        "stem": "下列说法正确的是？",
                        "materialText": "2025 年某地区生产总值增长。",
                        "options": [
                            {"label": "A", "text": "选项甲"},
                            {"label": "B", "text": "选项乙"},
                        ],
                    }
                ],
            },
            timeout=60,
        )
        packet.raise_for_status()
        packet_data = packet.json()
        packet_key = str(packet_data["artifactRef"]).split(
            f"tos://{bucket}/", 1
        )[1]
        assert client.get_object(bucket, packet_key).read(5) == b"%PDF-"
        print(
            "renderer=ready sha256={} private=true".format(
                str(packet_data["artifactSha256"])[:12]
            )
        )
    finally:
        try:
            client.delete_object(bucket, scan_key)
        except Exception:
            pass
        if packet_key:
            try:
                client.delete_object(bucket, packet_key)
            except Exception:
                pass


if __name__ == "__main__":
    main()
