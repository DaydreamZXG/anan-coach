#!/usr/bin/env python3
"""Batched Qwen3-VL semantic extraction from deterministic panel contact sheets.

The source image and deterministic panel boxes remain the visual authority.  The
model emits answer-isolated semantic candidates only; every generated record
stays repair_pending until factual review or a family-specific deterministic
gate accepts it.
"""

from __future__ import annotations

import argparse
import gc
import hashlib
import json
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from qwen_client import parse_json_object  # noqa: E402
from run_batch import (  # noqa: E402
    forbidden,
    normalize,
    prompt,
    read_jsonl,
    solver_document,
    validate,
    write_json,
    write_jsonl,
)


WORKSPACE_PREFIX = "/Users/zxg/Documents/新项目/"


def resolve_source(job: dict[str, Any], source_root: Path) -> Path:
    source = Path(job["sourcePaths"][0])
    if source.is_file():
        return source
    source_text = str(source)
    if not source_text.startswith(WORKSPACE_PREFIX):
        raise FileNotFoundError(f"source is outside the known workspace: {source}")
    resolved = source_root / source_text[len(WORKSPACE_PREFIX) :]
    if not resolved.is_file():
        raise FileNotFoundError(resolved)
    return resolved


def contact_sheet(job: dict[str, Any], source: Path, destination: Path) -> Path:
    layout = job["layoutCandidate"]
    boxes = layout["panelBoxes"]
    roles = layout["panelRoles"]
    tile = 384
    header = 46
    columns = 3
    rows = math.ceil(len(boxes) / columns)
    sheet = Image.new("RGB", (columns * tile, rows * (tile + header)), (245, 247, 250))
    draw = ImageDraw.Draw(sheet)
    font_path = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
    font = ImageFont.truetype(str(font_path), 24) if font_path.is_file() else ImageFont.load_default()
    with Image.open(source) as opened:
        source_image = opened.convert("RGB")
        option_index = 0
        for index, (box, role) in enumerate(zip(boxes, roles), 1):
            if role == "option":
                option_index += 1
            option_label = chr(ord("A") + option_index - 1) if role == "option" else ""
            label = f"P{index} {role}" + (f" {option_label}" if option_label else "")
            left = ((index - 1) % columns) * tile
            top = ((index - 1) // columns) * (tile + header)
            draw.rectangle((left, top, left + tile - 1, top + header - 1), fill="white", outline="black", width=2)
            draw.text((left + 12, top + 8), label, fill="black", font=font)
            panel_top = top + header
            draw.rectangle((left, panel_top, left + tile - 1, panel_top + tile - 1), fill="white", outline="black", width=2)
            if box is None:
                draw.text((left + 118, panel_top + 174), "MISSING", fill="black", font=font)
                continue
            x, y, width, height = (int(box[key]) for key in ("x", "y", "width", "height"))
            crop = source_image.crop((x, y, x + width, y + height))
            crop.thumbnail((tile - 28, tile - 28), Image.Resampling.LANCZOS)
            paste_x = left + (tile - crop.width) // 2
            paste_y = panel_top + (tile - crop.height) // 2
            sheet.paste(crop, (paste_x, paste_y))
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".tmp.png")
    sheet.save(temporary, format="PNG", optimize=True)
    temporary.replace(destination)
    return destination


def model_inputs(processor: Any, prompts: list[str], images: list[Image.Image]) -> Any:
    texts = []
    for value in prompts:
        messages = [{
            "role": "user",
            "content": [
                {"type": "image"},
                {"type": "text", "text": value + "\n输入图是按确定性分框生成的编号联系表；每个 P 编号只对应一个面板。只输出 JSON。"},
            ],
        }]
        texts.append(processor.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=True,
            enable_thinking=False,
        ))
    return processor(text=texts, images=images, padding=True, return_tensors="pt")


def write_acceptance(
    output_root: Path,
    job: dict[str, Any],
    source: Path,
    model_name: str,
    raw_text: str,
    candidate: dict[str, Any] | None,
    errors: list[str],
    normalizations: list[dict[str, str]],
    runtime_error: str | None,
) -> dict[str, Any]:
    asset_root = output_root / "assets" / job["assetId"]
    (asset_root / "raw-model-response.txt").write_text(raw_text + "\n", encoding="utf-8")
    write_json(asset_root / "raw-model-candidate.json", candidate)
    source_hash_ok = hashlib.sha256(source.read_bytes()).hexdigest() == job["sourceSha256"]
    layout = job["layoutCandidate"]
    missing_recovery = bool(
        candidate
        and candidate.get("panel_count") == layout["panelCount"] + 1
        and candidate.get("option_count") == layout["optionCount"]
        and sum(panel.get("role") == "missing" for panel in candidate.get("panels", []) if isinstance(panel, dict)) == 1
    )
    panel_agreement = bool(candidate and (candidate.get("panel_count") == layout["panelCount"] or missing_recovery))
    option_agreement = bool(candidate and candidate.get("option_count") == layout["optionCount"])
    if candidate is not None and not errors:
        write_json(asset_root / "solver.json", solver_document(job, candidate))
        write_json(asset_root / "evaluation.json", {
            "schemaVersion": "graphic-reasoning-semantic-evaluation-v1",
            "assetId": job["assetId"],
            "solverInputExcludesAnswer": True,
            "questionAnswers": [
                {
                    "questionId": reference.get("questionId"),
                    "answerLabel": reference.get("answerLabel"),
                    "optionLabels": reference.get("optionLabels", []),
                }
                for reference in job["questionReferences"]
            ],
        })
    gates = {
        "sourceHash": "pass" if source_hash_ok else "fail",
        "modelSchema": "pass" if candidate is not None and not errors else "fail",
        "panelCountAgreement": "pass" if panel_agreement else "fail",
        "optionCountAgreement": "pass" if option_agreement else "fail",
        "answerIsolation": "pass" if candidate is not None and not forbidden(candidate) else "fail",
        "contactSheetEvidence": "pass",
        "originalImagePresentation": "pass",
        "vectorEnhancement": "not_applicable",
    }
    schema_valid = all(value in {"pass", "not_applicable"} for value in gates.values())
    acceptance = {
        "schemaVersion": "graphic-reasoning-semantic-acceptance-v2",
        "assetId": job["assetId"],
        "sourceSha256": job["sourceSha256"],
        "checkpointKey": job["checkpointKey"],
        "model": model_name,
        "geometryAuthority": False,
        "modelInput": "deterministic_panel_contact_sheet",
        "normalizations": normalizations,
        "validationErrors": errors,
        "runtimeError": runtime_error,
        "gates": gates,
        "schemaStatus": "validated" if schema_valid else "invalid",
        "qualityStatus": "unreviewed" if schema_valid else "blocked_by_schema",
        "repairReasons": ["factual_quality_not_reviewed"] if schema_valid else ["schema_or_runtime_failure"],
        "status": "repair_pending",
        "finalVectorAcceptance": False,
    }
    write_json(asset_root / "acceptance.json", acceptance)
    return acceptance


def save_state(
    output_root: Path,
    model_name: str,
    rows: list[dict[str, Any]],
    requested: int,
    user_excluded: int,
) -> None:
    write_json(output_root / "run-state.json", {
        "schemaVersion": "graphic-reasoning-offline-semantic-run-state-v1",
        "observedAt": datetime.now(timezone.utc).isoformat(),
        "model": model_name,
        "requested": requested,
        "userExcluded": user_excluded,
        "processed": len(rows),
        "schemaValidated": sum(row["schemaStatus"] == "validated" for row in rows),
        "qualityAccepted": 0,
        "repairPending": len(rows),
        "rows": rows,
    })


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--model-name", default="qwen3-vl-30b-a3b-instruct")
    parser.add_argument("--queue", type=Path, default=HERE / "queue.jsonl")
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--max-new-tokens", type=int, default=1536)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--asset", action="append", default=[])
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    import torch
    from transformers import AutoModelForMultimodalLM, AutoProcessor

    queue = read_jsonl(args.queue)
    selected = set(args.asset)
    targets = [job for job in queue if not selected or job["assetId"] in selected]
    if args.limit > 0:
        targets = targets[: args.limit]
    user_excluded = sum(job.get("regularSubtypeCandidate") == "excluded_by_user" for job in targets)
    targets = [job for job in targets if job.get("regularSubtypeCandidate") != "excluded_by_user"]
    output_root = args.output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    processor = AutoProcessor.from_pretrained(args.model_dir, padding_side="left")
    model = AutoModelForMultimodalLM.from_pretrained(
        args.model_dir,
        dtype=torch.bfloat16,
        device_map="auto",
        attn_implementation="sdpa",
        low_cpu_mem_usage=True,
        max_memory={0: "45GiB", 1: "45GiB", "cpu": "180GiB"},
    )
    model.eval()
    completed_rows: list[dict[str, Any]] = []
    errors_rows: list[dict[str, Any]] = []

    pending: list[tuple[dict[str, Any], Path, Path]] = []
    for job in targets:
        acceptance_path = output_root / "assets" / job["assetId"] / "acceptance.json"
        if not args.force and acceptance_path.is_file():
            existing = json.loads(acceptance_path.read_text(encoding="utf-8"))
            if existing.get("checkpointKey") == job["checkpointKey"] and existing.get("model") == args.model_name:
                completed_rows.append({
                    "assetId": job["assetId"],
                    "schemaStatus": existing.get("schemaStatus", "invalid"),
                    "status": "checkpoint_reused",
                })
                continue
        source = resolve_source(job, args.source_root.resolve())
        sheet = contact_sheet(job, source, output_root / "assets" / job["assetId"] / "evidence" / "contact-sheet.png")
        pending.append((job, source, sheet))

    for offset in range(0, len(pending), max(1, args.batch_size)):
        group = pending[offset : offset + max(1, args.batch_size)]
        jobs = [item[0] for item in group]
        opened = [Image.open(item[2]).convert("RGB") for item in group]
        prompts = [prompt(job) for job in jobs]
        responses = [""] * len(group)
        group_error: str | None = None
        try:
            inputs = model_inputs(processor, prompts, opened).to(model.device, dtype=model.dtype)
            input_width = int(inputs["input_ids"].shape[1])
            with torch.inference_mode():
                outputs = model.generate(
                    **inputs,
                    max_new_tokens=args.max_new_tokens,
                    do_sample=False,
                    use_cache=True,
                )
            responses = processor.batch_decode(outputs[:, input_width:], skip_special_tokens=True)
            del inputs, outputs
        except Exception as exc:
            group_error = f"{type(exc).__name__}: {exc}"
        finally:
            for image in opened:
                image.close()
            gc.collect()
            torch.cuda.empty_cache()

        for index, (job, source, _) in enumerate(group):
            raw_text = responses[index].strip() if index < len(responses) else ""
            candidate: dict[str, Any] | None = None
            normalizations: list[dict[str, str]] = []
            runtime_error = group_error
            validation_errors: list[str] = []
            if group_error is None:
                try:
                    raw_candidate = parse_json_object(raw_text)
                    normalized, normalizations = normalize(raw_candidate, job)
                    if isinstance(normalized, dict):
                        candidate = normalized
                    validation_errors = validate(candidate, str(job.get("regularSubtypeCandidate") or ""))
                except Exception as exc:
                    runtime_error = f"{type(exc).__name__}: {exc}"
                    validation_errors = [runtime_error]
            else:
                validation_errors = [group_error]
            acceptance = write_acceptance(
                output_root,
                job,
                source,
                args.model_name,
                raw_text,
                candidate,
                validation_errors,
                normalizations,
                runtime_error,
            )
            row = {
                "assetId": job["assetId"],
                "schemaStatus": acceptance["schemaStatus"],
                "status": acceptance["status"],
            }
            completed_rows.append(row)
            if validation_errors:
                errors_rows.append({"assetId": job["assetId"], "errors": validation_errors})
        save_state(output_root, args.model_name, completed_rows, len(targets), user_excluded)
        write_jsonl(output_root / "errors.jsonl", errors_rows)
        print(json.dumps({
            "processed": len(completed_rows),
            "requested": len(targets),
            "schemaValidated": sum(row["schemaStatus"] == "validated" for row in completed_rows),
            "errors": len(errors_rows),
        }, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    raise SystemExit(main())
