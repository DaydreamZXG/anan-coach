#!/usr/bin/env python3
"""GPU batch runner for Texo FormulaNet on a temporary cleaning workstation."""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import sys
import time
from typing import Any, Iterable

import numpy as np
from PIL import Image
import torch
from transformers import (
    AutoConfig,
    AutoModel,
    AutoTokenizer,
    MBartConfig,
    VisionEncoderDecoderConfig,
    VisionEncoderDecoderModel,
)

from common import ROOT, latex_braces_balanced, read_jsonl, sha256_file, write_json


IMAGE_SIZE = 384
MEAN = 0.7931
STD = 0.1738


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path)
    parser.add_argument("--tokenizer-dir", type=Path)
    parser.add_argument("--texo-source-dir", type=Path, required=True)
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, default=ROOT / "manifest.jsonl")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "results")
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--priority", choices=("question_critical", "explanation"))
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--max-length", type=int, default=1024)
    parser.add_argument("--min-confidence", type=float, default=0.95)
    parser.add_argument("--shard-count", type=int, default=1)
    parser.add_argument("--shard-index", type=int, default=0)
    parser.add_argument("--overwrite", action="store_true")
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


def chunks(rows: list[dict[str, Any]], size: int) -> Iterable[list[dict[str, Any]]]:
    for index in range(0, len(rows), size):
        yield rows[index : index + size]


def local_source(input_dir: Path, asset_id: str) -> Path:
    matches = list(input_dir.glob(f"{asset_id}.*"))
    if len(matches) != 1:
        raise FileNotFoundError(f"input_count={len(matches)}")
    return matches[0]


def result_for(
    task: dict[str, Any], latex: str, confidence: float, stopped_at_eos: bool,
    token_count: int, max_length: int, min_confidence: float
) -> dict[str, Any]:
    balanced = latex_braces_balanced(latex)
    truncated = token_count >= max_length - 1
    usable = bool(latex) and balanced and stopped_at_eos and not truncated
    status = "candidate_auto" if usable and confidence >= min_confidence else "needs_manual_review"
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
            {"type": "formula", "format": "latex", "expression": latex or "[待复核]", "display": True}
        ],
        "confidence": round(confidence, 6),
        "verification": {
            "source_hash_matches": True,
            "latex_nonempty": bool(latex),
            "braces_balanced": balanced,
            "note": (
                f"Texo GPU 离线候选；{token_count} tokens；"
                f"{'EOS 正常收束' if stopped_at_eos and not truncated else '达到最大长度'}"
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
            "note": f"Texo GPU 识别失败: {str(error)[:200]}",
        },
    }


def load_model(args: argparse.Namespace):
    sys.path.insert(0, str(args.texo_source_dir))
    from texo.model.hgnet2 import HGNetv2, HGNetv2Config

    try:
        AutoConfig.register("my_hgnetv2", HGNetv2Config)
    except ValueError:
        pass
    try:
        AutoModel.register(HGNetv2Config, HGNetv2)
    except ValueError:
        pass
    if args.checkpoint:
        if not args.tokenizer_dir:
            raise SystemExit("--checkpoint 需要同时指定 --tokenizer-dir")
        encoder_config = HGNetv2Config(
            stem_channels=[3, 32, 48],
            stage_config={
                "stage1": [48, 48, 128, 1, 6, 3, False, False],
                "stage2": [128, 96, 512, 1, 6, 3, True, False],
                "stage3": [512, 192, 1024, 3, 6, 5, True, True],
                "stage4": [1024, 384, 2048, 1, 6, 5, True, True],
            },
            hidden_size=2048,
            pretrained="",
            freeze=False,
        )
        decoder_config = MBartConfig(
            vocab_size=1264,
            max_position_embeddings=1027,
            d_model=384,
            decoder_layers=2,
            decoder_attention_heads=16,
            decoder_ffn_dim=1536,
            bos_token_id=0,
            pad_token_id=1,
            eos_token_id=2,
            decoder_start_token_id=0,
            forced_eos_token_id=2,
            layer_norm_eps=1e-5,
            is_decoder=True,
            is_encoder_decoder=False,
            add_cross_attention=True,
            scale_embedding=True,
            tie_word_embeddings=False,
        )
        config = VisionEncoderDecoderConfig.from_encoder_decoder_configs(
            encoder_config, decoder_config
        )
        config.decoder_start_token_id = 0
        config.pad_token_id = 1
        config.eos_token_id = 2
        model = VisionEncoderDecoderModel(config)
        state_dict = torch.load(args.checkpoint, map_location="cpu", weights_only=True)
        model.load_state_dict(state_dict, strict=True)
        tokenizer = AutoTokenizer.from_pretrained(args.tokenizer_dir)
    else:
        tokenizer = AutoTokenizer.from_pretrained(args.model_dir)
        model = VisionEncoderDecoderModel.from_pretrained(args.model_dir)
    model.to(torch.device(args.device)).eval()
    return model, tokenizer


def selected_tasks(args: argparse.Namespace) -> list[dict[str, Any]]:
    rows = [
        row
        for row in read_jsonl(args.manifest)
        if not args.priority or row["priority"] == args.priority
    ]
    if args.limit > 0:
        rows = rows[: args.limit]
    rows = [
        row for index, row in enumerate(rows) if index % args.shard_count == args.shard_index
    ]
    if not args.overwrite:
        rows = [row for row in rows if not (args.output_dir / f"{row['asset_id']}.json").is_file()]
    return rows


def main() -> None:
    args = arguments()
    if args.batch_size < 1:
        raise SystemExit("--batch-size 必须大于 0")
    if args.shard_count < 1 or not 0 <= args.shard_index < args.shard_count:
        raise SystemExit("分片参数无效")
    tasks = selected_tasks(args)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    print(json.dumps({"selected": len(tasks), "device": args.device, "batch_size": args.batch_size}, ensure_ascii=False))
    if not tasks:
        return
    model, tokenizer = load_model(args)
    device = torch.device(args.device)
    started = time.monotonic()
    completed = 0
    for batch in chunks(tasks, args.batch_size):
        valid_tasks: list[dict[str, Any]] = []
        pixels: list[np.ndarray] = []
        for task in batch:
            try:
                source = local_source(args.input_dir, task["asset_id"])
                if sha256_file(source) != task["sha256"]:
                    raise RuntimeError("source_hash_mismatch")
                pixels.append(preprocess_image(source))
                valid_tasks.append(task)
            except Exception as error:
                write_json(args.output_dir / f"{task['asset_id']}.json", failure_result(task, error))
                completed += 1
        if valid_tasks:
            try:
                pixel_values = torch.from_numpy(np.stack(pixels)).to(device)
                with torch.inference_mode():
                    generated = model.generate(
                        pixel_values=pixel_values,
                        max_length=args.max_length,
                        num_beams=1,
                        do_sample=False,
                        return_dict_in_generate=True,
                        output_scores=True,
                    )
                sequences = generated.sequences
                decoded = tokenizer.batch_decode(sequences, skip_special_tokens=True)
                transition = model.compute_transition_scores(sequences, generated.scores, normalize_logits=True)
                for row, (task, latex) in enumerate(zip(valid_tasks, decoded)):
                    token_ids = sequences[row, 1:]
                    usable_mask = (token_ids != model.config.pad_token_id) & (token_ids != model.config.eos_token_id)
                    log_scores = transition[row, : token_ids.shape[0]][usable_mask]
                    confidence = float(torch.exp(log_scores.mean()).item()) if log_scores.numel() else 0.0
                    stopped = bool((sequences[row] == model.config.eos_token_id).any().item())
                    token_count = int((sequences[row] != model.config.pad_token_id).sum().item())
                    write_json(
                        args.output_dir / f"{task['asset_id']}.json",
                        result_for(
                            task, latex.strip(), confidence, stopped, token_count,
                            args.max_length, args.min_confidence
                        ),
                    )
                    completed += 1
                del pixel_values, generated, sequences, transition
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
