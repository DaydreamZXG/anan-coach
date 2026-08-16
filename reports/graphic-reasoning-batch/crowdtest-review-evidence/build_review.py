#!/usr/bin/env python3
"""Build a local, human-operated review page from original ZIP data."""

from __future__ import annotations

import base64
import csv
import html
import json
import mimetypes
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parents[2]
PUBLIC = PROJECT / "reports/graphic-reasoning-batch/crowdtest-package/build/graphic-reasoning-semantic-public-v1"
PRIVATE = PROJECT / "reports/graphic-reasoning-batch/crowdtest-package/build/graphic-reasoning-semantic-private-evaluation-v1"
DESKTOP = Path("/Users/zxg/Desktop/未命名文件夹")
ARCHIVES = {
    "summit": DESKTOP / "summit/3640-10359-summit.zip",
    "rudder": DESKTOP / "rudder/3640-10360-rudder.zip",
    "pelican": DESKTOP / "pelican/3640-10362-pelican.zip",
    "quarry": DESKTOP / "quarry/3640-10361-quarry.zip",
}
MODELS = list(ARCHIVES)


def load_assets() -> dict[str, dict[str, dict]]:
    result: dict[str, dict[str, dict]] = {}
    for model, archive_path in ARCHIVES.items():
        docs: dict[str, dict] = {}
        with zipfile.ZipFile(archive_path) as archive:
            for name in archive.namelist():
                if "/output/assets/" not in name or not name.endswith(".json"):
                    continue
                doc = json.loads(archive.read(name))
                docs[doc["assetId"]] = doc
        result[model] = docs
    return result


def compact(doc: dict) -> dict:
    panels = []
    for panel in doc.get("panels", []):
        facts = panel.get("visualFacts") or {}
        elements = []
        for element in facts.get("elements") or []:
            elements.append({
                "kind": element.get("kind"),
                "label": element.get("label"),
                "text": element.get("textContent"),
                "count": element.get("count"),
                "instances": element.get("instances"),
                "parts": element.get("parts"),
                "attributes": element.get("attributes"),
            })
        panels.append({
            "id": panel.get("id"),
            "role": panel.get("role"),
            "optionLabel": panel.get("optionLabel"),
            "elements": elements,
            "relations": facts.get("relations"),
            "counts": facts.get("counts"),
            "observations": facts.get("observations"),
            "uncertainties": panel.get("uncertainties"),
        })
    return {
        "assetId": doc.get("assetId"),
        "layout": doc.get("layout"),
        "visualGrammar": doc.get("visualGrammar"),
        "panels": panels,
        "reasoningCandidates": doc.get("reasoningCandidates"),
        "globalUncertainties": doc.get("globalUncertainties"),
        "extraction": doc.get("extraction"),
    }


def image_data_uri(asset_id: str) -> str:
    matches = list((PUBLIC / "input/images").glob(f"{asset_id}.*"))
    if len(matches) != 1:
        raise RuntimeError(f"image lookup failed for {asset_id}")
    mime = mimetypes.guess_type(matches[0].name)[0] or "image/png"
    encoded = base64.b64encode(matches[0].read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def selected_ids(all_docs: dict[str, dict[str, dict]]) -> list[str]:
    with (PRIVATE / "manual-review-sample-private.csv").open("r", encoding="utf-8-sig", newline="") as handle:
        sample = list(csv.DictReader(handle))
    priority = [
        "gr_0751a8530a3a10bda058e654",
        "gr_35db8a5c4667b231ee15abd7",
        "gr_440d7e11964ebad6f9303a17",
        "gr_814f04ca63ec75e0fd69a9fb",
        "gr_09a239f1f5c89bec86ff315d",
        "gr_7b00c758d0ab44e2c464e04f",
        "gr_06e94661142e35ee6757e42d",
        "gr_bf93e1e02d1f963a073debe5",
        "gr_523e7523e447cdf9e3eef777",
        "gr_f08ab4925bade2770e8caeb2",
    ]
    ids = priority + [row["assetId"] for row in sample]
    seen = set()
    return [aid for aid in ids if aid in all_docs[MODELS[0]] and not (aid in seen or seen.add(aid))]


def main() -> int:
    for path in ARCHIVES.values():
        if not path.exists():
            raise SystemExit(f"missing archive: {path}")
    all_docs = load_assets()
    ids = selected_ids(all_docs)
    records = []
    for aid in ids:
        records.append({
            "assetId": aid,
            "image": image_data_uri(aid),
            "models": {model: compact(all_docs[model][aid]) for model in MODELS},
        })
    payload = json.dumps(records, ensure_ascii=False).replace("</", "<\\/")
    template = (ROOT / "review-template.html").read_text(encoding="utf-8")
    rendered = template.replace("__REVIEW_DATA__", payload)
    (ROOT / "review.html").write_text(rendered, encoding="utf-8")
    print(f"built review.html with {len(records)} source-image cases and four original model outputs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
