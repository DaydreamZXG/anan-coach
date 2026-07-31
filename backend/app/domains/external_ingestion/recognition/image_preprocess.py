from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from PIL import Image, ImageOps

"""移植自 peipeishua `services/ocr/server.py` 的图像预处理管线。

拍照识题的最大痛点不是模型，而是「手机照片带着桌面/阴影/歪斜，OCR 识别率被背景拖垮」。
peipeishua 用两层纯 CPU 预处理解决：

1. auto_crop（自动裁剪）：分析四周边缘背景色 + 浅色文档区域检测，把题目区域从照片里裁出来。
2. 降采样：等比缩小到指定长边，减少解码耗时，也避免超大图喂给视觉模型反而变糊。

本模块是纯函数（PIL，无 numpy 依赖），无模型、无外部依赖，输出 PIL Image。
"""


# ---- 可调参数（对应 peipeishua 环境变量默认值） ----

AUTO_CROP_PADDING = 24
AUTO_CROP_ANALYSIS_EDGE = 720
AUTO_CROP_COLOR_DISTANCE = 42
AUTO_CROP_MAX_FOREGROUND_RATIO = 0.035
AUTO_CROP_MAX_TRIM_RATIO = 0.85
AUTO_CROP_MIN_AREA_RATIO = 0.25
AUTO_CROP_MIN_SAVED_PIXELS = 50_000
AUTO_CROP_LIGHT_MIN_GRAY = 218
AUTO_CROP_LIGHT_MAX_CHANNEL_SPREAD = 55
AUTO_CROP_LIGHT_MIN_ROW_RATIO = 0.08
AUTO_CROP_LIGHT_MIN_COL_RATIO = 0.08


@dataclass(frozen=True)
class PreprocessResult:
    """预处理结果：裁剪后的图 + 过程信息（供审计/调试）。"""

    image: Image.Image
    cropped: bool
    crop_strategy: str
    original_width: int
    original_height: int
    resized: bool
    reason: str = ""


def prepare_for_ocr(
    image: Image.Image,
    *,
    enable_crop: bool = True,
    max_long_edge: int = 2200,
) -> PreprocessResult:
    """对 PIL Image 应用裁剪 + 降采样，返回适合送 OCR/视觉模型的图。

    与原实现一致：先 EXIF 转正 → RGB → 可选自动裁剪 → 可选等比缩放。
    """
    prepared = ImageOps.exif_transpose(image).convert("RGB")
    original_width, original_height = prepared.size

    cropped = False
    crop_strategy = "none"
    if enable_crop:
        prepared, crop_info = auto_crop(prepared)
        cropped = bool(crop_info.get("cropped"))
        crop_strategy = str(crop_info.get("strategy", "none"))

    resized = False
    if max_long_edge > 0:
        long_edge = max(prepared.size)
        if long_edge > max_long_edge:
            scale = max_long_edge / long_edge
            target = (
                max(1, int(prepared.width * scale)),
                max(1, int(prepared.height * scale)),
            )
            prepared.thumbnail(target, Image.Resampling.LANCZOS)
            resized = True

    return PreprocessResult(
        image=prepared,
        cropped=cropped,
        crop_strategy=crop_strategy,
        original_width=original_width,
        original_height=original_height,
        resized=resized,
    )


def auto_crop(image: Image.Image) -> tuple[Image.Image, dict[str, Any]]:
    """裁剪掉照片四周的背景，返回 (裁剪后的图, 过程信息)。

    与原实现一致：小图直接跳过；先做边缘背景检测，再对比浅色文档检测取更优。
    用纯 PIL 逐行/逐列扫描（分析图 720px，开销可忽略），不引入 numpy 依赖。
    """
    original_width, original_height = image.size
    if original_width < 320 or original_height < 320:
        return image, {"cropped": False, "reason": "image_too_small"}

    analysis = image.copy()
    analysis.thumbnail((AUTO_CROP_ANALYSIS_EDGE, AUTO_CROP_ANALYSIS_EDGE))
    analysis_width, analysis_height = analysis.size
    rgb = analysis.convert("RGB")

    if analysis_width < 80 or analysis_height < 80:
        return image, {"cropped": False, "reason": "analysis_too_small"}

    band = max(2, min(16, min(analysis_width, analysis_height) // 32))
    max_vertical_trim = int(analysis_height * AUTO_CROP_MAX_TRIM_RATIO)
    max_horizontal_trim = int(analysis_width * AUTO_CROP_MAX_TRIM_RATIO)

    def median_color(pixels: list[tuple[int, int, int]]) -> tuple[int, int, int]:
        sorted_by_channel = [sorted(p[i] for p in pixels) for i in range(3)]
        mid = len(pixels) // 2
        return (
            sorted_by_channel[0][mid],
            sorted_by_channel[1][mid],
            sorted_by_channel[2][mid],
        )

    top_pixels = [rgb.getpixel((x, y)) for y in range(band) for x in range(analysis_width)]
    bottom_pixels = [
        rgb.getpixel((x, y))
        for y in range(analysis_height - band, analysis_height)
        for x in range(analysis_width)
    ]
    left_pixels = [rgb.getpixel((x, y)) for x in range(band) for y in range(analysis_height)]
    right_pixels = [
        rgb.getpixel((x, y))
        for x in range(analysis_width - band, analysis_width)
        for y in range(analysis_height)
    ]
    top_color = median_color(top_pixels)
    bottom_color = median_color(bottom_pixels)
    left_color = median_color(left_pixels)
    right_color = median_color(right_pixels)

    def color_distance(one: tuple[int, int, int], two: tuple[int, int, int]) -> int:
        return abs(one[0] - two[0]) + abs(one[1] - two[1]) + abs(one[2] - two[2])

    def row_foreground_ratio(y: int, color: tuple[int, int, int]) -> float:
        foreground = 0
        for x in range(analysis_width):
            if color_distance(rgb.getpixel((x, y)), color) > AUTO_CROP_COLOR_DISTANCE:
                foreground += 1
        return foreground / analysis_width

    def col_foreground_ratio(x: int, color: tuple[int, int, int]) -> float:
        foreground = 0
        for y in range(analysis_height):
            if color_distance(rgb.getpixel((x, y)), color) > AUTO_CROP_COLOR_DISTANCE:
                foreground += 1
        return foreground / analysis_height

    def background_like(ratio: float) -> bool:
        return ratio <= AUTO_CROP_MAX_FOREGROUND_RATIO

    top = 0
    while top < max_vertical_trim and background_like(row_foreground_ratio(top, top_color)):
        top += 1

    bottom = analysis_height
    while bottom - 1 > top and analysis_height - bottom < max_vertical_trim:
        if not background_like(row_foreground_ratio(bottom - 1, bottom_color)):
            break
        bottom -= 1

    left = 0
    while left < max_horizontal_trim and background_like(col_foreground_ratio(left, left_color)):
        left += 1

    right = analysis_width
    while right - 1 > left and analysis_width - right < max_horizontal_trim:
        if not background_like(col_foreground_ratio(right - 1, right_color)):
            break
        right -= 1

    analysis_bounds = (left, top, right, bottom)
    strategy = "edge_background"
    light_bounds = _find_light_document_bounds(rgb)

    if light_bounds is not None and _saved_analysis_pixels(
        light_bounds, analysis_width, analysis_height
    ) > _saved_analysis_pixels(analysis_bounds, analysis_width, analysis_height):
        analysis_bounds = light_bounds
        strategy = "light_document"

    left, top, right, bottom = analysis_bounds
    scale_x = original_width / analysis_width
    scale_y = original_height / analysis_height
    crop_left = max(0, int(left * scale_x) - AUTO_CROP_PADDING)
    crop_top = max(0, int(top * scale_y) - AUTO_CROP_PADDING)
    crop_right = min(original_width, int(right * scale_x) + AUTO_CROP_PADDING)
    crop_bottom = min(original_height, int(bottom * scale_y) + AUTO_CROP_PADDING)

    crop_width = crop_right - crop_left
    crop_height = crop_bottom - crop_top
    area_ratio = (crop_width * crop_height) / max(1, original_width * original_height)
    saved_pixels = (original_width * original_height) - (crop_width * crop_height)
    info: dict[str, Any] = {
        "cropped": False,
        "strategy": strategy,
        "savedPixels": saved_pixels,
        "areaRatio": round(area_ratio, 4),
    }

    if saved_pixels < AUTO_CROP_MIN_SAVED_PIXELS:
        info["reason"] = "crop_too_small"
        return image, info
    if crop_width < 200 or crop_height < 200 or area_ratio < AUTO_CROP_MIN_AREA_RATIO:
        info["reason"] = "crop_rejected"
        return image, info
    if (
        crop_left == 0
        and crop_top == 0
        and crop_right == original_width
        and crop_bottom == original_height
    ):
        info["reason"] = "no_background_edges"
        return image, info

    info["cropped"] = True
    return image.crop((crop_left, crop_top, crop_right, crop_bottom)), info


def _find_light_document_bounds(
    rgb: Image.Image,
) -> tuple[int, int, int, int] | None:
    """检测照片里的浅色文档区域（白纸在深色桌面上的场景），纯 PIL 实现。"""
    width, height = rgb.size

    def row_light_ratio(y: int) -> float:
        light = 0
        for x in range(width):
            r, g, b = rgb.getpixel((x, y))
            gray = (r + g + b) / 3
            spread = max(r, g, b) - min(r, g, b)
            if gray >= AUTO_CROP_LIGHT_MIN_GRAY and spread <= AUTO_CROP_LIGHT_MAX_CHANNEL_SPREAD:
                light += 1
        return light / width

    light_rows: list[int] = []
    for y in range(height):
        if row_light_ratio(y) >= AUTO_CROP_LIGHT_MIN_ROW_RATIO:
            light_rows.append(y)

    if not light_rows:
        return None

    segment = _largest_contiguous_segment(light_rows)
    if segment is None:
        return None

    top, bottom = segment

    def col_light_ratio(x: int) -> float:
        light = 0
        for y in range(top, bottom):
            r, g, b = rgb.getpixel((x, y))
            gray = (r + g + b) / 3
            spread = max(r, g, b) - min(r, g, b)
            if gray >= AUTO_CROP_LIGHT_MIN_GRAY and spread <= AUTO_CROP_LIGHT_MAX_CHANNEL_SPREAD:
                light += 1
        return light / max(1, bottom - top)

    light_cols: list[int] = []
    for x in range(width):
        if col_light_ratio(x) >= AUTO_CROP_LIGHT_MIN_COL_RATIO:
            light_cols.append(x)

    if not light_cols:
        return None

    return light_cols[0], top, light_cols[-1] + 1, bottom


def _saved_analysis_pixels(
    bounds: tuple[int, int, int, int], width: int, height: int
) -> int:
    left, top, right, bottom = bounds
    return (width * height) - max(0, right - left) * max(0, bottom - top)


def _largest_contiguous_segment(indexes: list[int]) -> tuple[int, int] | None:
    if not indexes:
        return None

    best_start = indexes[0]
    best_end = best_start + 1
    best_score = -1
    start = indexes[0]
    previous = indexes[0]

    for index in indexes[1:]:
        if index == previous + 1:
            previous = index
            continue

        score = previous - start + 1
        if score > best_score:
            best_score = score
            best_start = start
            best_end = previous + 1

        start = index
        previous = index

    score = previous - start + 1
    if score > best_score:
        best_start = start
        best_end = previous + 1

    return best_start, best_end
