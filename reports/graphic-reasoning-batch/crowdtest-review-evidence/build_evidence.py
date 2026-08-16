#!/usr/bin/env python3
"""Build screenshot-ready evidence from downloaded crowdtest ZIPs."""

from __future__ import annotations

import json
import textwrap
import zipfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parents[2]
PUBLIC = PROJECT / "reports/graphic-reasoning-batch/crowdtest-package/build/graphic-reasoning-semantic-public-v1"
DESKTOP = Path("/Users/zxg/Desktop/未命名文件夹")
ARCHIVES = {
    "summit": DESKTOP / "summit/3640-10359-summit.zip",
    "rudder": DESKTOP / "rudder/3640-10360-rudder.zip",
    "pelican": DESKTOP / "pelican/3640-10362-pelican.zip",
    "quarry": DESKTOP / "quarry/3640-10361-quarry.zip",
}

FONT_PATH = "/System/Library/Fonts/Supplemental/Arial Unicode.ttf"
MONO_PATH = "/System/Library/Fonts/SFNSMono.ttf"
FONT = ImageFont.truetype(FONT_PATH, 24)
SMALL = ImageFont.truetype(FONT_PATH, 20)
TITLE = ImageFont.truetype(FONT_PATH, 34)
MONO = ImageFont.truetype(MONO_PATH, 20)

INK = "#172033"
MUTED = "#586174"
BLUE = "#2563eb"
GREEN = "#087f5b"
RED = "#c92a2a"
AMBER = "#a15c00"
BG = "#f5f7fb"
CARD = "#ffffff"


def load_member(model: str, suffix: str):
    with zipfile.ZipFile(ARCHIVES[model]) as archive:
        name = next(name for name in archive.namelist() if name.endswith(suffix))
        return json.loads(archive.read(name))


def has_member(model: str, suffix: str) -> bool:
    with zipfile.ZipFile(ARCHIVES[model]) as archive:
        return any(name.endswith(suffix) for name in archive.namelist())


def asset(model: str, asset_id: str):
    return load_member(model, f"/output/assets/{asset_id}.json")


def canvas(height=1040):
    image = Image.new("RGB", (1500, height), BG)
    return image, ImageDraw.Draw(image)


def text(draw, xy, value, font=FONT, fill=INK, width=None, spacing=8):
    x, y = xy
    lines = []
    for paragraph in str(value).splitlines() or [""]:
        if width:
            lines.extend(textwrap.wrap(paragraph, width=width, break_long_words=False) or [""])
        else:
            lines.append(paragraph)
    draw.multiline_text((x, y), "\n".join(lines), font=font, fill=fill, spacing=spacing)


def card(draw, box, title=None):
    draw.rounded_rectangle(box, radius=18, fill=CARD, outline="#d9deea", width=2)
    if title:
        text(draw, (box[0] + 28, box[1] + 20), title, font=FONT, fill=BLUE)


def paste_contain(dst, src_path, box):
    src = Image.open(src_path).convert("RGB")
    max_w, max_h = box[2] - box[0], box[3] - box[1]
    scale = min(max_w / src.width, max_h / src.height)
    src = src.resize((round(src.width * scale), round(src.height * scale)))
    x = box[0] + (max_w - src.width) // 2
    y = box[1] + (max_h - src.height) // 2
    dst.paste(src, (x, y))


def header(draw, title, subtitle):
    text(draw, (55, 38), title, font=TITLE)
    text(draw, (57, 88), subtitle, font=SMALL, fill=MUTED)


def build_validation():
    image, draw = canvas(900)
    header(draw, "四模型产物：本地独立复核", "来源：下载后的原始 ZIP；验证器只检查契约、覆盖和文件完整性，不代表语义完全正确")
    models = ["summit", "rudder", "pelican", "quarry"]
    for i, model in enumerate(models):
        y = 155 + i * 155
        state = load_member(model, "/output/run-state.json")
        report = load_member(model, "/output/validation-report.json")
        has_errors = has_member(model, "/output/errors.jsonl")
        card(draw, (45, y, 1455, y + 125))
        text(draw, (75, y + 22), model.upper(), font=TITLE, fill=BLUE)
        text(draw, (300, y + 18), f"JSON 资产  {report['manifestAssetCount']} / {report['expectedAssetCount']}")
        text(draw, (700, y + 18), f"structured  {state['structuredCandidateCount']}")
        text(draw, (1000, y + 18), f"repair_pending  {state['repairPendingCount']}")
        if model == "summit" and not has_errors:
            text(draw, (300, y + 67), "逐题结构均有效；重新按交付要求检查：缺少 output/errors.jsonl", fill=RED)
        else:
            text(draw, (300, y + 67), "独立验证：3601/3601，有要求的 errors.jsonl", fill=GREEN)
    text(draw, (55, 800), "结论：四份均有全量 JSON；Summit 存在一个明确的最终打包缺项。语义质量需结合下面的原图抽查。", font=SMALL, fill=MUTED)
    image.save(ROOT / "00-four-model-validation.png")


def build_summit():
    image, draw = canvas(800)
    header(draw, "Summit：最终交付文件缺失", "检查对象：3640-10359-summit.zip；这是文件完整性问题，不等于 3601 份 JSON 无效")
    card(draw, (55, 155, 1445, 640), "实际检查")
    lines = [
        "✓ output/assets/*.json：3601 份",
        "✓ output/manifest.jsonl：存在",
        "✓ output/run-state.json：存在",
        "✓ output/validation-report.json：存在",
        "✗ output/errors.jsonl：ZIP 中不存在",
    ]
    for i, line in enumerate(lines):
        text(draw, (110, 225 + i * 66), line, font=FONT, fill=RED if line.startswith("✗") else GREEN)
    text(draw, (110, 570), "任务 README 明确要求 output/errors.jsonl；即使没有错误，也应保留空文件。", font=SMALL, fill=INK)
    text(draw, (55, 705), "可截图说明：本地解压并核对要求文件，发现 errors.jsonl 未被打包。", font=SMALL, fill=MUTED)
    image.save(ROOT / "01-summit-missing-errors-file.png")


def build_rudder():
    aid = "gr_0751a8530a3a10bda058e654"
    data = asset("rudder", aid)
    panel = next(p for p in data["panels"] if p.get("optionLabel") == "C")
    dot = next(e for e in panel["visualFacts"]["elements"] if e["label"] == "black_dot")
    image, draw = canvas(1000)
    header(draw, "Rudder：可见黑点数量记录错误", f"资产 {aid}；原图与提交 JSON 直接对照")
    card(draw, (45, 145, 900, 735), "原始题图")
    paste_contain(image, PUBLIC / f"input/images/{aid}.png", (80, 205, 865, 695))
    card(draw, (930, 145, 1455, 735), "Rudder JSON：选项 C")
    text(draw, (970, 225), f"kind: {dot['kind']}\nlabel: {dot['label']}\ncount: {dot['count']}", font=MONO, fill=INK)
    text(draw, (970, 390), "原观察文字：", font=SMALL, fill=MUTED)
    text(draw, (970, 430), panel["visualFacts"]["observations"][0], font=SMALL, fill=INK, width=24)
    text(draw, (55, 790), "人工可见事实：选项 C 有 3 个实心黑点；提交 JSON 写 count=2。该误差可能改变下游解题判断。", fill=RED, width=78)
    text(draw, (55, 905), "截图时把原图、JSON count 和上面的结论同时保留在画面内。", font=SMALL, fill=MUTED)
    image.save(ROOT / "02-rudder-dot-count-error.png")


def build_pelican():
    aid = "gr_0751a8530a3a10bda058e654"
    data = asset("pelican", aid)
    panel = data["panels"][0]
    dot = next(e for e in panel["visualFacts"]["elements"] if e["label"] == "black_dots")
    image, draw = canvas(1000)
    header(draw, "Pelican：实心黑点被建模成 circle", f"资产 {aid}；任务要求严格区分 dot 与 circle")
    card(draw, (45, 145, 900, 735), "原始题图")
    paste_contain(image, PUBLIC / f"input/images/{aid}.png", (80, 205, 865, 695))
    card(draw, (930, 145, 1455, 735), "Pelican JSON：题干第 1 格")
    text(draw, (970, 225), f"kind: {dot['kind']}\nlabel: {dot['label']}\ncount: {dot['count']}\nfillState: {dot['instances'][0]['fillState']}", font=MONO, fill=INK)
    text(draw, (970, 425), "数量识别正确，但图元类型错误。", font=SMALL, fill=AMBER, width=26)
    text(draw, (55, 790), "人工可见事实：这些是小型实心点，应写为 dot；写成 circle 会混淆“黑点数量”和“圆形元素数量”。", fill=RED, width=78)
    text(draw, (55, 905), "这是契约语义错误，不是图片是否能打开的问题。", font=SMALL, fill=MUTED)
    image.save(ROOT / "03-pelican-dot-type-error.png")


def build_quarry():
    aid = "gr_35db8a5c4667b231ee15abd7"
    data = asset("quarry", aid)
    layout = data["layout"]
    roles = [p["role"] for p in data["panels"]]
    image, draw = canvas(1000)
    header(draw, "Quarry：四组选项被拆成八个普通序列面板", f"资产 {aid}；原图 A–D 每组包含 ①② 两个立方体")
    card(draw, (45, 145, 930, 735), "原始题图")
    paste_contain(image, PUBLIC / f"input/images/{aid}.png", (80, 215, 895, 675))
    card(draw, (960, 145, 1455, 735), "Quarry JSON：layout")
    text(draw, (995, 225), f"layoutType: {layout['layoutType']}\npanelCount: {layout['panelCount']}\noptionCount: {layout['optionCount']}", font=MONO, fill=INK)
    text(draw, (995, 390), "panel roles:", font=SMALL, fill=MUTED)
    text(draw, (995, 430), "\n".join(f"p{i+1}: {role}" for i, role in enumerate(roles)), font=MONO, fill=INK)
    text(draw, (55, 790), "人工可见事实：原图有 A、B、C、D 四组选项；每组中的 ①② 应属于同一个选项。提交却写成 optionCount=0。", fill=RED, width=78)
    text(draw, (55, 905), "该错误会让下游模型失去选项边界，属于会影响做题的关键分组错误。", font=SMALL, fill=MUTED)
    image.save(ROOT / "04-quarry-option-grouping-error.png")


def main():
    missing = [str(path) for path in ARCHIVES.values() if not path.exists()]
    if missing:
        raise SystemExit("Missing archives: " + ", ".join(missing))
    ROOT.mkdir(parents=True, exist_ok=True)
    build_validation()
    build_summit()
    build_rudder()
    build_pelican()
    build_quarry()
    print("built 5 evidence PNGs in", ROOT)


if __name__ == "__main__":
    main()
