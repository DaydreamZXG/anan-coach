from __future__ import annotations

from pathlib import Path

from PIL import Image

from scripts.repair_question_analysis_assets import extract_analysis_assets


def _write_png(path: Path, *, size: tuple[int, int]) -> None:
    Image.new("RGB", size, "white").save(path, format="PNG")


def test_extracts_original_analysis_image_from_the_province_asset_folder(
    tmp_path: Path,
) -> None:
    asset_root = tmp_path / "fenbi-final-analysis-v1-20260613-assets"
    source = asset_root / "四川" / "paper_images" / "analysis.png"
    source.parent.mkdir(parents=True)
    _write_png(source, size=(240, 120))
    record = {
        "province": "四川",
        "original_analysis_html": (
            '<p>如下图</p><p><img src="paper_images/analysis.png" /></p>'
        ),
    }

    assets = extract_analysis_assets(record, asset_root=asset_root)

    assert len(assets) == 1
    assert assets[0].image_src == "paper_images/analysis.png"
    assert assets[0].object_key == (
        "data/question-bank-export/files/source-question-bank/"
        "fenbi-final-analysis-v1-20260613-assets/四川/paper_images/analysis.png"
    )
    assert assets[0].byte_size == source.stat().st_size
    assert (assets[0].pixel_width, assets[0].pixel_height) == (240, 120)


def test_rejects_remote_and_parent_traversal_analysis_sources(tmp_path: Path) -> None:
    asset_root = tmp_path / "assets"
    asset_root.mkdir()
    record = {
        "original_analysis_html": (
            '<img src="https://example.com/remote.png">'
            '<img src="../outside.png">'
        )
    }

    assert extract_analysis_assets(record, asset_root=asset_root) == ()


def test_excludes_inline_tex_glyphs_and_deduplicates_explanation_images(
    tmp_path: Path,
) -> None:
    asset_root = tmp_path / "assets"
    paper = asset_root / "paper_images"
    paper.mkdir(parents=True)
    _write_png(paper / "degree.png", size=(16, 16))
    _write_png(paper / "analysis.png", size=(200, 100))
    record = {
        "original_analysis_html": (
            '<p>旋转90<img flag="tex" src="paper_images/degree.png"></p>'
            '<img src="paper_images/analysis.png">'
            '<img src="paper_images/analysis.png">'
        )
    }

    assets = extract_analysis_assets(record, asset_root=asset_root)

    assert [asset.image_src for asset in assets] == ["paper_images/analysis.png"]


def test_excludes_tiny_inline_non_tex_sprites(tmp_path: Path) -> None:
    asset_root = tmp_path / "assets"
    paper = asset_root / "paper_images"
    paper.mkdir(parents=True)
    _write_png(paper / "shape-a.png", size=(21, 24))
    record = {
        "original_analysis_html": '<p>图形<img src="paper_images/shape-a.png">叠加</p>'
    }

    assert extract_analysis_assets(record, asset_root=asset_root) == ()
