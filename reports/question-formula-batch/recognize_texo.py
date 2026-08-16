#!/usr/bin/env python3
"""Run the lightweight Texo FormulaNet ONNX model as an offline batch job.

This intentionally depends only on ONNX Runtime, Pillow, NumPy and tokenizers.
It does not install Texo or its training stack into the application backend.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import time
from typing import Any, Iterable

import numpy as np
import onnxruntime as ort
from PIL import Image
from tokenizers import Tokenizer

from common import ROOT, latex_braces_balanced, read_jsonl, sha256_file, write_json


RESULTS = ROOT / "results"
IMAGE_SIZE = 384
MEAN = 0.7931
STD = 0.1738
START_TOKEN_ID = 0
PAD_TOKEN_ID = 1
EOS_TOKEN_ID = 2


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=RESULTS)
    parser.add_argument("--priority", choices=("question_critical", "explanation"))
    parser.add_argument("--asset-id", action="append", default=[])
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--threads", type=int, default=max(1, (os.cpu_count() or 4) // 2))
    parser.add_argument("--shard-count", type=int, default=1)
    parser.add_argument("--shard-index", type=int, default=0)
    parser.add_argument("--max-length", type=int, default=1024)
    parser.add_argument("--min-confidence", type=float, default=0.95)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def _content_bbox(image: Image.Image) -> tuple[int, int, int, int] | None:
    gray = np.asarray(image.convert("L"), dtype=np.float32)
    low = float(gray.min())
    high = float(gray.max())
    if high <= low:
        return None
    normalized = (gray - low) / (high - low) * 255.0
    ys, xs = np.nonzero(normalized < 200.0)
    if not len(xs):
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def preprocess_image(path: Path) -> np.ndarray:
    """Match Texo's EvalMER preprocessing without its training dependencies."""
    with Image.open(path) as source:
        if source.mode in ("RGBA", "LA") or "transparency" in source.info:
            rgba = source.convert("RGBA")
            background = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
            background.alpha_composite(rgba)
            image = background.convert("RGB")
        else:
            image = source.convert("RGB")
    bbox = _content_bbox(image)
    if bbox:
        image = image.crop(bbox)
    scale = min(IMAGE_SIZE / image.width, IMAGE_SIZE / image.height)
    resized = (
        max(1, round(image.width * scale)),
        max(1, round(image.height * scale)),
    )
    image = image.resize(resized, Image.Resampling.BILINEAR)
    canvas = Image.new("RGB", (IMAGE_SIZE, IMAGE_SIZE), (0, 0, 0))
    canvas.paste(image, ((IMAGE_SIZE - image.width) // 2, (IMAGE_SIZE - image.height) // 2))
    gray = np.asarray(canvas.convert("L"), dtype=np.float32) / 255.0
    normalized = (gray - MEAN) / STD
    return np.repeat(normalized[None, :, :], 3, axis=0).astype(np.float32)


def _chosen_probability(logits: np.ndarray, token_ids: np.ndarray) -> np.ndarray:
    values = logits[:, -1, :].astype(np.float64)
    maximum = values.max(axis=1, keepdims=True)
    denominator = np.exp(values - maximum).sum(axis=1)
    chosen = values[np.arange(len(token_ids)), token_ids]
    return np.exp(chosen - maximum[:, 0]) / denominator


@dataclass(frozen=True)
class Recognition:
    latex: str
    confidence: float
    token_count: int
    stopped_at_eos: bool


class TexoOnnxRecognizer:
    def __init__(self, model_dir: Path, threads: int) -> None:
        required = (
            "encoder_model.onnx",
            "decoder_model.onnx",
            "decoder_with_past_model.onnx",
            "tokenizer.json",
        )
        missing = [name for name in required if not (model_dir / name).is_file()]
        if missing:
            raise FileNotFoundError(f"Texo ONNX 目录缺少文件: {', '.join(missing)}")
        options = ort.SessionOptions()
        options.intra_op_num_threads = max(1, threads)
        options.inter_op_num_threads = 1
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        providers = ["CPUExecutionProvider"]
        self.encoder = ort.InferenceSession(str(model_dir / required[0]), options, providers=providers)
        self.decoder = ort.InferenceSession(str(model_dir / required[1]), options, providers=providers)
        self.decoder_with_past = ort.InferenceSession(
            str(model_dir / required[2]), options, providers=providers
        )
        self.tokenizer = Tokenizer.from_file(str(model_dir / required[3]))
        self.decoder_output_names = [item.name for item in self.decoder.get_outputs()]
        self.past_output_names = [item.name for item in self.decoder_with_past.get_outputs()]

    def recognize(self, pixel_values: np.ndarray, max_length: int) -> list[Recognition]:
        batch_size = pixel_values.shape[0]
        hidden = self.encoder.run(None, {"pixel_values": pixel_values})[0]
        first = self.decoder.run(
            None,
            {
                "input_ids": np.full((batch_size, 1), START_TOKEN_ID, dtype=np.int64),
                "encoder_hidden_states": hidden,
            },
        )
        state = dict(zip(self.decoder_output_names[1:], first[1:]))
        next_ids = first[0][:, -1, :].argmax(axis=1).astype(np.int64)
        probabilities = _chosen_probability(first[0], next_ids)
        sequences: list[list[int]] = [[] for _ in range(batch_size)]
        scores: list[list[float]] = [[] for _ in range(batch_size)]
        finished = np.zeros(batch_size, dtype=bool)

        for row, token_id in enumerate(next_ids.tolist()):
            sequences[row].append(token_id)
            if token_id not in (START_TOKEN_ID, PAD_TOKEN_ID, EOS_TOKEN_ID):
                scores[row].append(float(probabilities[row]))
            finished[row] = token_id == EOS_TOKEN_ID

        for _ in range(max(0, max_length - 1)):
            if bool(finished.all()):
                break
            feed: dict[str, np.ndarray] = {
                "input_ids": np.where(finished, PAD_TOKEN_ID, next_ids).reshape(-1, 1).astype(np.int64)
            }
            for model_input in self.decoder_with_past.get_inputs():
                name = model_input.name
                if name == "input_ids":
                    continue
                feed[name] = state[name.replace("past_key_values", "present")]
            outputs = self.decoder_with_past.run(None, feed)
            for name, value in zip(self.past_output_names[1:], outputs[1:]):
                state[name] = value
            next_ids = outputs[0][:, -1, :].argmax(axis=1).astype(np.int64)
            probabilities = _chosen_probability(outputs[0], next_ids)
            for row, token_id in enumerate(next_ids.tolist()):
                if finished[row]:
                    continue
                sequences[row].append(token_id)
                if token_id not in (START_TOKEN_ID, PAD_TOKEN_ID, EOS_TOKEN_ID):
                    scores[row].append(float(probabilities[row]))
                if token_id == EOS_TOKEN_ID:
                    finished[row] = True

        recognitions: list[Recognition] = []
        for row, token_ids in enumerate(sequences):
            latex = self.tokenizer.decode(token_ids, skip_special_tokens=True).strip()
            token_scores = scores[row]
            confidence = math.exp(sum(math.log(max(value, 1e-12)) for value in token_scores) / len(token_scores)) if token_scores else 0.0
            recognitions.append(
                Recognition(
                    latex=latex,
                    confidence=confidence,
                    token_count=len(token_ids),
                    stopped_at_eos=bool(finished[row]),
                )
            )
        return recognitions


def chunks(rows: list[dict[str, Any]], size: int) -> Iterable[list[dict[str, Any]]]:
    for index in range(0, len(rows), size):
        yield rows[index : index + size]


def success_result(
    task: dict[str, Any], recognition: Recognition, minimum_confidence: float, max_length: int
) -> dict[str, Any]:
    balanced = latex_braces_balanced(recognition.latex)
    truncated = recognition.token_count >= max_length - 1
    usable = bool(recognition.latex) and balanced and recognition.stopped_at_eos and not truncated
    status = (
        "candidate_auto"
        if usable and recognition.confidence >= minimum_confidence
        else "needs_manual_review"
    )
    return {
        "schema_version": "formula-asset-v1",
        "asset_id": task["asset_id"],
        "sha256": task["sha256"],
        "source_path": task["source_path"],
        "priority": task["priority"],
        "status": status,
        "provider": "texo_local",
        "provider_request_id": None,
        "blocks": [
            {
                "type": "formula",
                "format": "latex",
                "expression": recognition.latex or "[待复核]",
                "display": True,
            }
        ],
        "confidence": round(recognition.confidence, 6),
        "verification": {
            "source_hash_matches": True,
            "latex_nonempty": bool(recognition.latex),
            "braces_balanced": balanced,
            "note": (
                f"Texo 本地 ONNX 候选；{recognition.token_count} tokens；"
                f"{'EOS 正常收束' if recognition.stopped_at_eos and not truncated else '达到最大长度'}"
            ),
        },
    }


def failure_result(task: dict[str, Any], error: Exception) -> dict[str, Any]:
    return {
        "schema_version": "formula-asset-v1",
        "asset_id": task["asset_id"],
        "sha256": task["sha256"],
        "source_path": task["source_path"],
        "priority": task["priority"],
        "status": "failed",
        "provider": "texo_local",
        "provider_request_id": None,
        "blocks": [
            {"type": "formula", "format": "latex", "expression": "[识别失败]", "display": True}
        ],
        "confidence": None,
        "verification": {
            "source_hash_matches": str(error) != "source_hash_mismatch",
            "latex_nonempty": False,
            "braces_balanced": False,
            "note": f"Texo 本地识别失败: {str(error)[:200]}",
        },
    }


def selected_tasks(args: argparse.Namespace) -> list[dict[str, Any]]:
    requested = set(args.asset_id)
    rows = [
        row
        for row in read_jsonl(ROOT / "manifest.jsonl")
        if (not args.priority or row["priority"] == args.priority)
        and (not requested or row["asset_id"] in requested)
    ]
    if requested:
        missing = requested - {row["asset_id"] for row in rows}
        if missing:
            raise SystemExit(f"未在 manifest 中找到: {', '.join(sorted(missing))}")
    pending = [
        row
        for row in rows
        if args.overwrite or not (args.output_dir / f"{row['asset_id']}.json").is_file()
    ]
    limited = pending[: args.limit] if args.limit > 0 else pending
    return [
        row
        for index, row in enumerate(limited)
        if index % args.shard_count == args.shard_index
    ]


def main() -> None:
    args = arguments()
    if args.batch_size < 1:
        raise SystemExit("--batch-size 必须大于 0")
    if args.shard_count < 1 or not 0 <= args.shard_index < args.shard_count:
        raise SystemExit("--shard-index 必须在 0 到 --shard-count-1 之间")
    if args.max_length < 2 or args.max_length > 1024:
        raise SystemExit("--max-length 必须在 2～1024 之间")
    if not 0 <= args.min_confidence <= 1:
        raise SystemExit("--min-confidence 必须在 0～1 之间")
    tasks = selected_tasks(args)
    print(
        json.dumps(
            {
                "selected": len(tasks),
                "output_dir": str(args.output_dir),
                "batch_size": args.batch_size,
                "threads": args.threads,
                "dry_run": args.dry_run,
            },
            ensure_ascii=False,
        )
    )
    if args.dry_run or not tasks:
        return
    args.output_dir.mkdir(parents=True, exist_ok=True)
    recognizer = TexoOnnxRecognizer(args.model_dir, args.threads)
    started = time.monotonic()
    completed = 0
    for batch in chunks(tasks, args.batch_size):
        valid_tasks: list[dict[str, Any]] = []
        pixels: list[np.ndarray] = []
        for task in batch:
            try:
                source = Path(task["source_path"])
                if sha256_file(source) != task["sha256"]:
                    raise RuntimeError("source_hash_mismatch")
                pixels.append(preprocess_image(source))
                valid_tasks.append(task)
            except Exception as error:
                write_json(args.output_dir / f"{task['asset_id']}.json", failure_result(task, error))
                completed += 1
        if valid_tasks:
            try:
                recognitions = recognizer.recognize(np.stack(pixels), args.max_length)
                for task, recognition in zip(valid_tasks, recognitions):
                    write_json(
                        args.output_dir / f"{task['asset_id']}.json",
                        success_result(task, recognition, args.min_confidence, args.max_length),
                    )
                    completed += 1
            except Exception as error:
                for task in valid_tasks:
                    write_json(args.output_dir / f"{task['asset_id']}.json", failure_result(task, error))
                    completed += 1
        elapsed = time.monotonic() - started
        rate = completed / elapsed if elapsed else 0.0
        print(
            f"{completed}/{len(tasks)} {rate:.2f} images/s ETA "
            f"{max(0.0, (len(tasks) - completed) / rate) / 60:.1f} min",
            flush=True,
        )


if __name__ == "__main__":
    main()
