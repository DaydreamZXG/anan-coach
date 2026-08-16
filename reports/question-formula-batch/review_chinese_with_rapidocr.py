#!/usr/bin/env python3
"""Cross-check Chinese text inside formula images with a dedicated OCR engine.

This script never decides formula structure.  It only reports the Chinese text
seen in the source image so a separate verifier can compare it with the current
LaTeX candidate.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
import glob
import json
import os
from pathlib import Path
from typing import Any

import cv2
import numpy as np


ENGINE: Any = None


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=8)
    return parser.parse_args()


def init_worker() -> None:
    global ENGINE
    os.environ.setdefault("OMP_NUM_THREADS", "2")
    os.environ.setdefault("OPENBLAS_NUM_THREADS", "2")
    from rapidocr_onnxruntime import RapidOCR

    ENGINE = RapidOCR()


def normalized_image(input_dir: str, asset_id: str) -> np.ndarray:
    matches = glob.glob(str(Path(input_dir) / f"{asset_id}.*"))
    if len(matches) != 1:
        raise FileNotFoundError(f"input_count={len(matches)}")
    image = cv2.imread(matches[0], cv2.IMREAD_UNCHANGED)
    if image is None:
        raise ValueError("image_open_failed")
    if image.ndim == 3 and image.shape[2] == 4:
        alpha = image[:, :, 3:] / 255.0
        image = (image[:, :, :3] * alpha + 255 * (1 - alpha)).astype("uint8")
    height, width = image.shape[:2]
    scale = max(4, min(12, round(96 / max(height, 1))))
    image = cv2.resize(
        image,
        (max(1, width * scale), max(1, height * scale)),
        interpolation=cv2.INTER_CUBIC,
    )
    return cv2.copyMakeBorder(
        image,
        32,
        32,
        32,
        32,
        cv2.BORDER_CONSTANT,
        value=(255, 255, 255),
    )


def recognize(payload: tuple[str, str, str]) -> dict[str, Any]:
    asset_id, expression, input_dir = payload
    try:
        result, _ = ENGINE(normalized_image(input_dir, asset_id))
        observations = [
            {"text": item[1], "confidence": round(float(item[2]), 6)}
            for item in (result or [])
        ]
        return {
            "asset_id": asset_id,
            "current_expression": expression,
            "observations": observations,
            "error": None,
        }
    except Exception as error:
        return {
            "asset_id": asset_id,
            "current_expression": expression,
            "observations": [],
            "error": str(error)[:240],
        }


def main() -> None:
    args = arguments()
    rows = [json.loads(line) for line in args.candidates.read_text(encoding="utf-8").splitlines()]
    payloads = [
        (row["asset_id"], row["current_expression"], str(args.input_dir))
        for row in rows
    ]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with ProcessPoolExecutor(max_workers=args.workers, initializer=init_worker) as executor:
        results = executor.map(recognize, payloads, chunksize=4)
        with args.output.open("w", encoding="utf-8") as output:
            for index, result in enumerate(results, start=1):
                output.write(json.dumps(result, ensure_ascii=False) + "\n")
                if index % 100 == 0 or index == len(rows):
                    print(f"{index}/{len(rows)}", flush=True)


if __name__ == "__main__":
    main()
