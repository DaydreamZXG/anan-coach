from __future__ import annotations

import base64
import binascii
import hashlib
import io
import os
import secrets
import tempfile
import time
from pathlib import Path
from typing import Annotated, Literal
from uuid import uuid4

import modal
from fastapi import Header, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, Field


APP_NAME = "anan-recognition-ocr"
PROVIDER_RELEASE = "modal-paddleocr-vl-v1-20260727-r1"
MODEL_RELEASE = "PaddleOCR-VL-v1"
MAXIMUM_IMAGE_BYTES = 10 * 1024 * 1024
MAXIMUM_BASE64_CHARACTERS = 4 * ((MAXIMUM_IMAGE_BYTES + 2) // 3)
MAXIMUM_IMAGE_PIXELS = 40_000_000

# This is the immutable image built by the operator-owned, already deployed
# peipeishua-shenlun-ocr v7 release. Reusing the image avoids a mutable
# `latest` pull while retaining the existing PaddleOCR-VL model cache.
runtime_image = modal.Image.from_id("im-otObDV3fjToTbtbmcXKjKk")
model_cache = modal.Volume.from_name(
    "peipeishua-paddleocr-vl-cache",
    create_if_missing=False,
)
endpoint_auth = modal.Secret.from_name("anan-ocr-auth")
app = modal.App(APP_NAME)


class RecognitionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["anan-modal-ocr-request@v1"]
    mime_type: Literal["image/jpeg", "image/png"]
    input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    image_base64: str = Field(
        min_length=4,
        max_length=MAXIMUM_BASE64_CHARACTERS,
    )
    mode: Literal["fast", "thorough"] = "fast"
    max_candidates: int = Field(default=3, ge=1, le=4)


@app.cls(
    image=runtime_image,
    gpu="T4",
    timeout=2400,
    startup_timeout=900,
    scaledown_window=120,
    min_containers=0,
    max_containers=3,
    retries=0,
    volumes={"/cache": model_cache},
    secrets=[endpoint_auth],
)
class AnanOcrService:
    @modal.enter()
    def load(self) -> None:
        import re

        from PIL import Image, ImageOps
        from paddleocr import PaddleOCRVL

        self.re = re
        self.Image = Image
        self.ImageOps = ImageOps
        self.pipeline = PaddleOCRVL(pipeline_version="v1")

    def _decode_image(self, payload: RecognitionRequest):
        try:
            raw = base64.b64decode(payload.image_base64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="image_payload_invalid",
            ) from exc
        if not raw or len(raw) > MAXIMUM_IMAGE_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="image_payload_too_large",
            )
        digest = hashlib.sha256(raw).hexdigest()
        if not secrets.compare_digest(digest, payload.input_sha256):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="image_digest_mismatch",
            )
        try:
            with self.Image.open(io.BytesIO(raw)) as opened:
                source_format = str(opened.format or "").upper()
                width, height = opened.size
                if width < 32 or height < 32 or width * height > MAXIMUM_IMAGE_PIXELS:
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                        detail="image_dimensions_outside_policy",
                    )
                image = self.ImageOps.exif_transpose(opened).convert("RGB")
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="image_decode_failed",
            ) from exc
        expected_format = "JPEG" if payload.mime_type == "image/jpeg" else "PNG"
        if source_format != expected_format:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="image_mime_mismatch",
            )
        return image, digest

    def _extract_blocks(self, result) -> list[dict[str, object]]:
        data = getattr(result, "json", None)
        if not isinstance(data, dict):
            return []
        root = data.get("res", data)
        blocks = root.get("parsing_res_list") if isinstance(root, dict) else None
        normalized: list[dict[str, object]] = []
        if not isinstance(blocks, list):
            return normalized
        for block in blocks:
            if not isinstance(block, dict):
                continue
            text = str(block.get("block_content") or "").strip()
            bbox = block.get("block_bbox")
            if not text or not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
                continue
            try:
                coordinates = [float(value) for value in bbox]
            except (TypeError, ValueError):
                continue
            raw_confidence = block.get("block_score", block.get("score", 0.5))
            try:
                confidence = min(1.0, max(0.0, float(raw_confidence)))
            except (TypeError, ValueError):
                confidence = 0.5
            normalized.append(
                {
                    "text": text,
                    "bbox": coordinates,
                    "label": str(block.get("block_label") or "unknown"),
                    "order": block.get("block_order"),
                    "confidence": confidence,
                }
            )
        return normalized

    def _score_blocks(
        self,
        blocks: list[dict[str, object]],
        *,
        angle: int,
    ) -> float:
        text = "\n".join(str(block["text"]) for block in blocks)
        chinese = len(self.re.findall(r"[\u4e00-\u9fff]", text))
        punctuation = len(self.re.findall(r"[，。；：“”！？、]", text))
        bad_symbols = len(self.re.findall(r"[□�@#$%^&*_={}\[\]<>]", text))
        repeated_noise = len(self.re.findall(r"([\u4e00-\u9fff])\1{2,}", text))
        horizontal = sum(
            1
            for block in blocks
            if "vertical" not in str(block.get("label", "")).casefold()
        )
        vertical = len(blocks) - horizontal
        horizontal_geometry = 0
        vertical_geometry = 0
        for block in blocks:
            x1, y1, x2, y2 = block["bbox"]
            width = float(x2) - float(x1)
            height = float(y2) - float(y1)
            if width >= height * 1.2:
                horizontal_geometry += 1
            elif height > width * 1.2:
                vertical_geometry += 1
        # Exam material is overwhelmingly horizontal. A strong rotation prior
        # keeps EXIF-correct images at 0° unless rotation materially improves OCR.
        return (
            chinese
            + punctuation * 2
            + horizontal * 18
            + horizontal_geometry * 24
            - vertical * 120
            - vertical_geometry * 60
            - bad_symbols * 20
            - repeated_noise * 30
            - (180 if angle else 0)
        )

    def _reorder_blocks(
        self,
        blocks: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        enriched: list[dict[str, object]] = []
        for block in blocks:
            x1, y1, x2, y2 = block["bbox"]
            enriched.append(
                {
                    **block,
                    "cx": (x1 + x2) / 2,
                    "cy": (y1 + y2) / 2,
                    "bw": x2 - x1,
                    "bh": y2 - y1,
                }
            )
        return sorted(enriched, key=lambda block: (block["cy"], block["cx"]))

    def _recognize(
        self,
        image,
        *,
        max_candidates: int,
    ) -> dict[str, object]:
        started = time.monotonic()
        angles = (0, 90, 270, 180)[:max_candidates]
        results: list[dict[str, object]] = []
        with tempfile.TemporaryDirectory(prefix="anan-modal-ocr-") as directory:
            root = Path(directory)
            for angle in angles:
                candidate_started = time.monotonic()
                rotated = image.rotate(-angle, expand=True)
                path = root / f"candidate-{angle}.jpg"
                rotated.save(path, format="JPEG", quality=92, optimize=True)
                width, height = rotated.size
                blocks: list[dict[str, object]] = []
                for result in self.pipeline.predict(str(path)):
                    blocks.extend(self._extract_blocks(result))
                ordered = self._reorder_blocks(blocks)
                results.append(
                    {
                        "angle": angle,
                        "width": width,
                        "height": height,
                        "score": self._score_blocks(blocks, angle=angle),
                        "elapsed_ms": round(
                            (time.monotonic() - candidate_started) * 1000
                        ),
                        "text": "\n\n".join(str(block["text"]) for block in ordered),
                        "blocks": ordered,
                    }
                )
        results.sort(key=lambda item: float(item["score"]), reverse=True)
        if not results or not str(results[0]["text"]).strip():
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="recognition_empty",
            )
        best = results[0]
        return {
            "selected_angle": best["angle"],
            "width": best["width"],
            "height": best["height"],
            "score": best["score"],
            "text": best["text"],
            "blocks": best["blocks"],
            "candidate_summary": [
                {
                    "angle": item["angle"],
                    "score": item["score"],
                    "elapsed_ms": item["elapsed_ms"],
                    "width": item["width"],
                    "height": item["height"],
                }
                for item in results
            ],
            "elapsed_ms": round((time.monotonic() - started) * 1000),
        }

    @modal.fastapi_endpoint(method="POST", label="recognize")
    def recognize(
        self,
        payload: RecognitionRequest,
        response: Response,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, object]:
        expected = os.environ.get("ANAN_OCR_BEARER_TOKEN", "")
        scheme, _, credential = (authorization or "").partition(" ")
        supplied = credential.strip() if scheme.casefold() == "bearer" else ""
        if not expected or not secrets.compare_digest(supplied, expected):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="processor_credential_invalid",
            )
        request_id = str(uuid4())
        image, digest = self._decode_image(payload)
        thorough = payload.mode == "thorough"
        max_candidates = payload.max_candidates
        maximum = 4 if thorough else 3
        if not 1 <= max_candidates <= maximum:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="candidate_count_outside_policy",
            )
        try:
            result = self._recognize(image, max_candidates=max_candidates)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="recognition_failed",
            ) from exc
        response.headers["X-Processor-Release"] = PROVIDER_RELEASE
        response.headers["X-Request-ID"] = request_id
        return {
            "schema_version": "anan-modal-ocr-response@v1",
            "provider_release": PROVIDER_RELEASE,
            "model_release": MODEL_RELEASE,
            "provider_request_id": request_id,
            "input_sha256": digest,
            **result,
        }
