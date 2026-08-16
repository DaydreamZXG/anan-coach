from __future__ import annotations

import html
import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np


ROOT = Path(__file__).resolve().parent
SAMPLES = ROOT / "representative-samples"
BINARY_SOURCE = SAMPLES / "img_0069_d91227ae6f8a6e2c.png"
LINE_SOURCE = SAMPLES / "img_0084_3d7e7c60c1e2f078.png"


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    width: int
    height: int

    @property
    def center(self) -> tuple[float, float]:
        return self.x + self.width / 2, self.y + self.height / 2

    @property
    def area(self) -> int:
        return self.width * self.height

    def contains_center(self, other: "Rect") -> bool:
        cx, cy = other.center
        return self.x <= cx <= self.x + self.width and self.y <= cy <= self.y + self.height


def rectangle_candidates(gray: np.ndarray) -> list[Rect]:
    _, binary = cv2.threshold(gray, 180, 255, cv2.THRESH_BINARY_INV)
    contours, _ = cv2.findContours(binary, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
    found: list[Rect] = []
    for contour in contours:
        perimeter = cv2.arcLength(contour, True)
        approximation = cv2.approxPolyDP(contour, 0.02 * perimeter, True)
        if len(approximation) != 4 or not cv2.isContourConvex(approximation):
            continue
        x, y, width, height = cv2.boundingRect(contour)
        area = cv2.contourArea(contour)
        if width < 25 or height < 25 or area < 500:
            continue
        if area / (width * height) < 0.75:
            continue
        rect = Rect(x, y, width, height)
        if not any(
            abs(rect.x - old.x) < 3
            and abs(rect.y - old.y) < 3
            and abs(rect.width - old.width) < 4
            and abs(rect.height - old.height) < 4
            for old in found
        ):
            found.append(rect)
    return found


def dominant_panels(rectangles: list[Rect]) -> list[Rect]:
    squareish = [
        item
        for item in rectangles
        if 0.8 <= item.width / item.height <= 1.25 and max(item.width, item.height) >= 50
    ]
    clusters: list[list[Rect]] = []
    for rect in sorted(squareish, key=lambda item: (item.width + item.height) / 2):
        size = (rect.width + rect.height) / 2
        for cluster in clusters:
            reference = np.median([(item.width + item.height) / 2 for item in cluster])
            if abs(size - reference) <= max(4, reference * 0.06):
                cluster.append(rect)
                break
        else:
            clusters.append([rect])
    viable = [cluster for cluster in clusters if 4 <= len(cluster) <= 20]
    if not viable:
        raise ValueError("no repeated panel-size cluster found")
    # The larger repeated squares are panels; the smaller repeated squares are their inner grids.
    selected = max(viable, key=lambda cluster: np.median([item.area for item in cluster]))
    return sorted(selected, key=lambda item: (item.y, item.x))


def panel_groups(rectangles: list[Rect], panels: list[Rect]) -> list[tuple[Rect, list[Rect]]]:
    groups: list[tuple[Rect, list[Rect]]] = []
    panel_area = float(np.median([panel.area for panel in panels]))
    for candidate in rectangles:
        if candidate.area < panel_area * 2:
            continue
        members = [panel for panel in panels if candidate.contains_center(panel)]
        if len(members) >= 3 and candidate not in panels:
            groups.append((candidate, sorted(members, key=lambda item: (item.y, item.x))))
    # Keep only the tightest container for each identical member set.
    unique: dict[tuple[tuple[int, int], ...], tuple[Rect, list[Rect]]] = {}
    for container, members in groups:
        key = tuple((member.x, member.y) for member in members)
        previous = unique.get(key)
        if previous is None or container.area < previous[0].area:
            unique[key] = (container, members)
    return sorted(unique.values(), key=lambda item: (item[0].y, item[0].x))


def consecutive_centers(indices: np.ndarray) -> list[float]:
    groups: list[list[int]] = []
    for raw in indices.tolist():
        value = int(raw)
        if not groups or value > groups[-1][-1] + 1:
            groups.append([value])
        else:
            groups[-1].append(value)
    return [sum(group) / len(group) for group in groups]


def find_inner_grid(gray: np.ndarray, panel: Rect, rectangles: list[Rect]) -> Rect | None:
    candidates = [
        item
        for item in rectangles
        if panel.contains_center(item)
        and item.area < panel.area * 0.9
        and item.area > panel.area * 0.35
        and 0.85 <= item.width / item.height <= 1.15
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda item: item.area)


def occupancy_matrix(gray: np.ndarray, grid: Rect) -> list[list[int]]:
    crop = gray[grid.y : grid.y + grid.height, grid.x : grid.x + grid.width]
    dark = crop < 160
    vertical = consecutive_centers(np.where(dark.sum(axis=0) > crop.shape[0] * 0.68)[0])
    horizontal = consecutive_centers(np.where(dark.sum(axis=1) > crop.shape[1] * 0.68)[0])
    if len(vertical) < 3 or len(horizontal) < 3:
        raise ValueError("grid lines were not recovered")
    matrix: list[list[int]] = []
    for top, bottom in zip(horizontal, horizontal[1:]):
        row: list[int] = []
        for left, right in zip(vertical, vertical[1:]):
            x1, x2 = int(left + 2), int(right - 1)
            y1, y2 = int(top + 2), int(bottom - 1)
            cell = dark[y1:y2, x1:x2]
            row.append(int(cell.size > 0 and float(cell.mean()) > 0.5))
        matrix.append(row)
    return matrix


def hough_line_count(gray: np.ndarray, panel: Rect) -> int:
    crop = gray[panel.y : panel.y + panel.height, panel.x : panel.x + panel.width].copy()
    crop[:4, :] = 255
    crop[-4:, :] = 255
    crop[:, :4] = 255
    crop[:, -4:] = 255
    binary = cv2.threshold(crop, 160, 255, cv2.THRESH_BINARY_INV)[1]
    lines = cv2.HoughLinesP(
        binary,
        1,
        np.pi / 180,
        threshold=12,
        minLineLength=max(12, int(min(panel.width, panel.height) * 0.18)),
        maxLineGap=3,
    )
    return 0 if lines is None else int(len(lines))


def contour_path(gray: np.ndarray, panel: Rect) -> str:
    crop = gray[panel.y : panel.y + panel.height, panel.x : panel.x + panel.width].copy()
    crop[:4, :] = 255
    crop[-4:, :] = 255
    crop[:, :4] = 255
    crop[:, -4:] = 255
    mask = cv2.threshold(crop, 160, 255, cv2.THRESH_BINARY_INV)[1]
    contours, _ = cv2.findContours(mask, cv2.RETR_TREE, cv2.CHAIN_APPROX_NONE)
    commands: list[str] = []
    for contour in contours:
        approximation = cv2.approxPolyDP(contour, 0.22, True)
        points = approximation[:, 0, :]
        if len(points) < 3:
            continue
        x0, y0 = points[0]
        commands.append(f"M{int(x0)} {int(y0)}")
        commands.extend(f"L{int(x)} {int(y)}" for x, y in points[1:])
        commands.append("Z")
    return " ".join(commands)


def build_binary_payload() -> dict[str, object]:
    gray = cv2.imread(str(BINARY_SOURCE), cv2.IMREAD_GRAYSCALE)
    if gray is None:
        raise SystemExit(f"cannot read {BINARY_SOURCE}")
    rectangles = rectangle_candidates(gray)
    panels = dominant_panels(rectangles)
    groups = panel_groups(rectangles, panels)
    stem_group = next(members for _, members in groups if len(members) == 9)
    option_group = next(members for _, members in groups if len(members) == 4)
    items: list[dict[str, object]] = []
    for index, panel in enumerate(stem_group):
        row, column = divmod(index, 3)
        grid = find_inner_grid(gray, panel, rectangles)
        items.append(
            {
                "id": f"r{row + 1}c{column + 1}",
                "role": "missing" if grid is None else "stem",
                "row": row + 1,
                "column": column + 1,
                "occupancy": None if grid is None else occupancy_matrix(gray, grid),
            }
        )
    for index, panel in enumerate(option_group):
        grid = find_inner_grid(gray, panel, rectangles)
        if grid is None:
            raise ValueError("option grid not found")
        items.append(
            {
                "id": chr(ord("A") + index),
                "role": "option",
                "position": index + 1,
                "occupancy": occupancy_matrix(gray, grid),
            }
        )
    shapes = {
        (len(item["occupancy"]), len(item["occupancy"][0]))
        for item in items
        if item["occupancy"] is not None
    }
    return {
        "schemaVersion": "graphic-binary-grid-v1",
        "sourceAsset": BINARY_SOURCE.name,
        "layoutType": "matrix_3x3",
        "visualGrammar": "binary_grid",
        "items": items,
        "validation": {
            "panelCount": len(panels),
            "stemPanelCount": len(stem_group),
            "optionPanelCount": len(option_group),
            "matrixShapes": [list(shape) for shape in sorted(shapes)],
            "singleMissingPanel": sum(item["role"] == "missing" for item in items) == 1,
        },
    }


def build_line_payload() -> dict[str, object]:
    gray = cv2.imread(str(LINE_SOURCE), cv2.IMREAD_GRAYSCALE)
    if gray is None:
        raise SystemExit(f"cannot read {LINE_SOURCE}")
    rectangles = rectangle_candidates(gray)
    panels = dominant_panels(rectangles)
    groups = panel_groups(rectangles, panels)
    stem_group = next(members for _, members in groups if len(members) == 9)
    option_group = next(members for _, members in groups if len(members) == 4)
    line_counts = [hough_line_count(gray, panel) for panel in stem_group]
    missing_index = int(np.argmin(line_counts))
    items: list[dict[str, object]] = []
    for index, panel in enumerate(stem_group):
        row, column = divmod(index, 3)
        is_missing = index == missing_index
        items.append(
            {
                "id": f"r{row + 1}c{column + 1}",
                "role": "missing" if is_missing else "stem",
                "row": row + 1,
                "column": column + 1,
                "viewBox": [0, 0, panel.width, panel.height],
                "path": None if is_missing else contour_path(gray, panel),
                "longLineEvidenceCount": line_counts[index],
            }
        )
    for index, panel in enumerate(option_group):
        items.append(
            {
                "id": chr(ord("A") + index),
                "role": "option",
                "position": index + 1,
                "viewBox": [0, 0, panel.width, panel.height],
                "path": contour_path(gray, panel),
                "longLineEvidenceCount": hough_line_count(gray, panel),
            }
        )
    return {
        "schemaVersion": "graphic-line-topology-candidate-v1",
        "sourceAsset": LINE_SOURCE.name,
        "layoutType": "matrix_3x3",
        "visualGrammar": "line_art",
        "items": items,
        "validation": {
            "panelCount": len(panels),
            "stemPanelCount": len(stem_group),
            "optionPanelCount": len(option_group),
            "missingPanel": items[missing_index]["id"],
            "missingHasFewestLongLines": line_counts[missing_index] < 8,
        },
    }


def render_binary(payload: dict[str, object], output: Path) -> None:
    cell = 7
    inner = 9 * cell
    panel = 77
    gap = 8
    margin = 12
    stem_height = 3 * panel + 2 * gap
    options_y = margin + stem_height + 35
    width = margin * 2 + 4 * panel + 3 * gap
    height = options_y + panel + 32
    items = payload["items"]
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#fff"/>',
    ]

    def draw(item: dict[str, object], x: int, y: int, label: str = "") -> None:
        parts.append(f'<rect x="{x}" y="{y}" width="{panel}" height="{panel}" fill="#fff" stroke="#111" stroke-width="2"/>')
        matrix = item["occupancy"]
        if matrix is None:
            parts.append(f'<text x="{x + panel / 2}" y="{y + 58}" text-anchor="middle" font-family="Arial" font-size="52">?</text>')
        else:
            ox = x + (panel - inner) / 2
            oy = y + (panel - inner) / 2
            for row, values in enumerate(matrix):
                for column, value in enumerate(values):
                    fill = "#111" if value else "#fff"
                    parts.append(f'<rect x="{ox + column * cell:.2f}" y="{oy + row * cell:.2f}" width="{cell}" height="{cell}" fill="{fill}" stroke="#111" stroke-width="0.8"/>')
        if label:
            parts.append(f'<text x="{x + panel / 2}" y="{y + panel + 24}" text-anchor="middle" font-family="Arial" font-size="22">{label}</text>')

    stem = [item for item in items if item["role"] != "option"]
    options = [item for item in items if item["role"] == "option"]
    for index, item in enumerate(stem):
        row, column = divmod(index, 3)
        draw(item, margin + column * (panel + gap), margin + row * (panel + gap))
    option_start = (width - (4 * panel + 3 * gap)) / 2
    for index, item in enumerate(options):
        draw(item, int(option_start + index * (panel + gap)), options_y, html.escape(item["id"]))
    parts.append("</svg>")
    output.write_text("\n".join(parts), encoding="utf-8")


def render_line(payload: dict[str, object], output: Path) -> None:
    panel = 78
    gap = 8
    margin = 12
    options_x = margin + 3 * panel + 3 * gap + 28
    options_y = margin + panel
    width = options_x + 4 * panel + 3 * gap + margin
    height = margin * 2 + 3 * panel + 2 * gap + 28
    items = payload["items"]
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#fff"/>',
    ]

    def draw(item: dict[str, object], x: int, y: int, label: str = "") -> None:
        parts.append(f'<rect x="{x}" y="{y}" width="{panel}" height="{panel}" fill="#fff" stroke="#111" stroke-width="2"/>')
        if item["role"] == "missing":
            parts.append(f'<text x="{x + panel / 2}" y="{y + 58}" text-anchor="middle" font-family="Arial" font-size="52">?</text>')
        else:
            _, _, source_width, source_height = item["viewBox"]
            scale = min(panel / source_width, panel / source_height)
            parts.append(
                f'<g transform="translate({x} {y}) scale({scale:.5f})">'
                f'<path d="{item["path"]}" fill="#111" fill-rule="evenodd"/></g>'
            )
        if label:
            parts.append(f'<text x="{x + panel / 2}" y="{y + panel + 24}" text-anchor="middle" font-family="Arial" font-size="22">{label}</text>')

    stem = [item for item in items if item["role"] != "option"]
    options = [item for item in items if item["role"] == "option"]
    for index, item in enumerate(stem):
        row, column = divmod(index, 3)
        draw(item, margin + column * (panel + gap), margin + row * (panel + gap))
    for index, item in enumerate(options):
        draw(item, options_x + index * (panel + gap), options_y, html.escape(item["id"]))
    parts.append("</svg>")
    output.write_text("\n".join(parts), encoding="utf-8")


def main() -> None:
    binary = build_binary_payload()
    line = build_line_payload()
    (ROOT / "structured-binary-grid-prototype.json").write_text(
        json.dumps(binary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (ROOT / "structured-line-matrix-prototype.json").write_text(
        json.dumps(line, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    render_binary(binary, ROOT / "redrawn-binary-grid-prototype.svg")
    render_line(line, ROOT / "redrawn-line-matrix-prototype.svg")
    print(json.dumps({"binary": binary["validation"], "line": line["validation"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
