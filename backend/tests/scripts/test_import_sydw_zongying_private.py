from __future__ import annotations

from pathlib import Path

from scripts.import_sydw_zongying_private import (
    DATASET_ID,
    _paper_material_text,
    _render_private_assets,
    build_plan,
    parse_paper,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
SOURCE_DIR = REPOSITORY_ROOT / "data" / "sydw-liankao-zongying"


def test_full_private_source_inventory_is_lossless() -> None:
    plan = build_plan(SOURCE_DIR)

    assert len(plan.papers) == 45
    assert len(plan.tasks) == 212
    assert sum(len(paper.materials) for paper in plan.papers) == 209
    assert len(plan.image_payloads) == 31
    assert plan.summary()["distributionClass"] == "account_private"
    assert plan.summary()["publicationState"].startswith("quarantined_")


def test_irregular_2018_h2_b_markup_keeps_four_top_level_tasks() -> None:
    paper = parse_paper(SOURCE_DIR / "2018-H2-B.md", root=SOURCE_DIR)

    assert len(paper.tasks) == 4
    assert paper.tasks[0].prompt.startswith("概念分析题")
    assert paper.tasks[-1].display_level2 == "文章写作"


def test_all_source_images_are_bound_to_a_material_or_task() -> None:
    plan = build_plan(SOURCE_DIR)
    referenced = {
        reference
        for paper in plan.papers
        for material in paper.materials
        for reference in material.image_refs
    }
    referenced.update(
        reference
        for paper in plan.papers
        for task in paper.tasks
        for reference in task.image_refs
    )
    disk_images = {
        path.relative_to(SOURCE_DIR).as_posix()
        for path in SOURCE_DIR.rglob("*")
        if path.suffix.casefold() in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}
    }

    assert referenced == disk_images


def test_private_asset_rendering_never_creates_a_public_url() -> None:
    plan = build_plan(SOURCE_DIR)
    paper = next(
        paper
        for paper in plan.papers
        if any(material.image_refs for material in paper.materials)
    )
    rendered_materials = _paper_material_text(paper, plan)

    assert "private-asset://" in rendered_materials
    assert "https://" not in rendered_materials
    for material in paper.materials:
        rendered = _render_private_assets(material.content, material.image_refs, plan)
        for reference in material.image_refs:
            assert f"]({reference})" not in rendered


def test_dataset_identity_is_stable_and_private() -> None:
    assert DATASET_ID == "sydw-zongying-ab-private-20260811"

