from __future__ import annotations

from PIL import Image, ImageDraw

from app.domains.external_ingestion.matching.fingerprint import visual_similarity
from app.domains.external_ingestion.recognition.models import VisualFeatureHash
from app.domains.external_ingestion.recognition.visual_hash import (
    feature_hashes,
    matching_feature_hashes,
)


def _feature(image: Image.Image) -> VisualFeatureHash:
    return VisualFeatureHash.model_validate(feature_hashes(image))


def test_portrait_screenshot_hashes_recall_a_tightly_cropped_question_figure() -> None:
    asset = Image.new("RGB", (550, 154), "white")
    draw = ImageDraw.Draw(asset)
    draw.rectangle((2, 2, 547, 120), outline="black", width=3)
    draw.rectangle((18, 25, 110, 116), outline="black", width=4)
    draw.polygon(((25, 100), (105, 28), (105, 100)), fill="black")
    for index in range(4):
        left = 145 + (index * 95)
        draw.rectangle((left, 30, left + 65, 105), outline="black", width=4)
        draw.line((left, 105, left + 65, 30), fill="black", width=4)
        draw.text((left + 25, 130), chr(ord("A") + index), fill="black")

    screenshot = Image.new("RGB", (998, 1558), "white")
    embedded = asset.resize((918, 257), Image.Resampling.LANCZOS)
    screenshot.paste(embedded, (40, 590))

    variants = tuple(
        VisualFeatureHash.model_validate(item)
        for item in matching_feature_hashes(screenshot)
    )

    assert len(variants) > 1
    assert visual_similarity(variants, (_feature(asset),)) >= 0.80


def test_landscape_asset_keeps_one_matching_hash() -> None:
    asset = Image.new("RGB", (550, 154), "white")

    assert len(matching_feature_hashes(asset)) == 1
