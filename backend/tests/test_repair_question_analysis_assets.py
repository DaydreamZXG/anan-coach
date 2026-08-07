from __future__ import annotations

from pathlib import Path

from scripts.repair_question_analysis_assets import extract_analysis_assets


def test_extracts_original_analysis_image_from_the_province_asset_folder(
    tmp_path: Path,
) -> None:
    asset_root = tmp_path / "fenbi-final-analysis-v1-20260613-assets"
    source = asset_root / "四川" / "paper_images" / "analysis.png"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"analysis-image")
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
    assert assets[0].byte_size == len(b"analysis-image")


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
    (paper / "degree.png").write_bytes(b"degree")
    (paper / "analysis.png").write_bytes(b"analysis")
    record = {
        "original_analysis_html": (
            '<p>旋转90<img flag="tex" src="paper_images/degree.png"></p>'
            '<img src="paper_images/analysis.png">'
            '<img src="paper_images/analysis.png">'
        )
    }

    assets = extract_analysis_assets(record, asset_root=asset_root)

    assert [asset.image_src for asset in assets] == ["paper_images/analysis.png"]
