#!/usr/bin/env python3
"""Snap literal font-size / border-radius / box-shadow decls to locked tokens.

Run from apps/wechat/miniprogram. Usage:
    python3 style-codemod.py [--write]
"""

from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

WRITE = "--write" in sys.argv

ROOTS = [
    "pages",
    "custom-tab-bar",
    "components",
    "features",
    "task-runtime",
    "design-system/primitives",
    "design-system/reporting",
    "design-system/stage9",
]
# In-flight edits owned by the concurrent UI session; migrate later.
EXCLUDE: set[str] = set()

FONT_TIERS = [
    (22, "--text-micro"),
    (24, "--text-caption"),
    (26, "--text-body"),
    (29, "--text-body-lg"),
    (33, "--text-section"),
    (37, "--text-title-sm"),
    (42, "--text-title"),
    (50, "--text-title-lg"),
    (58, "--text-hero"),
    (68, "--text-stat"),
    (76, "--text-display"),
    (120, "--text-stat-lg"),
]

RADIUS_TIERS = [
    (9, "--radius-8"),
    (14, "--radius-12"),
    (18, "--radius-16"),
    (26, "--radius-24"),
    (36, "--radius-32"),
    (44, "--radius-40"),
    (56, "--radius-48"),
    (98, "--radius-64"),
]

changes: Counter[str] = Counter()
unmapped: Counter[str] = Counter()


def font_token(n: float, file: str) -> str | None:
    if file.startswith("custom-tab-bar/") and n <= 21:
        return "--text-tab"
    for upper, token in FONT_TIERS:
        if n <= upper:
            return token
    return None


def radius_component(part: str) -> str:
    m = re.fullmatch(r"([0-9.]+)rpx", part)
    if not m:
        return part  # 0, px values, keep as-is
    n = float(m.group(1))
    if n <= 4:
        return part
    if n >= 99:
        return "var(--radius-capsule)"
    for upper, token in RADIUS_TIERS:
        if n <= upper:
            return f"var({token})"
    return part


def parse_color(layer: str) -> tuple[float, float, float, float] | None:
    m = re.search(r"rgba?\(([^)]*)\)", layer)
    if not m:
        return None
    body = m.group(1).replace("/", " ").replace(",", " ")
    parts = body.split()
    if len(parts) < 3:
        return None
    r, g, b = (float(parts[0]), float(parts[1]), float(parts[2]))
    a = 1.0
    if len(parts) >= 4:
        raw = parts[3]
        a = float(raw[:-1]) / 100 if raw.endswith("%") else float(raw)
    return r, g, b, a


def shadow_token(value: str) -> str | None:
    if re.fullmatch(r"0 0 0 9999rpx rgba\(0,\s*0,\s*0,\s*0\.56\)", value):
        return "--shadow-spotlight-mask"
    if value.startswith("inset"):
        return "--shadow-well"
    head = value.split("rgb", 1)[0]
    nums = [float(x) for x in re.findall(r"(-?[0-9.]+)(?:rpx)?(?=\s|$)", head)]
    color = parse_color(value)
    if len(nums) < 2 or color is None:
        return None
    y = nums[1]
    blur = nums[2] if len(nums) > 2 else 0.0
    r, g, b, a = color
    if r < 60 and g > 150:
        return "--shadow-cyan"
    if b >= 200 and r >= 60:
        return "--shadow-ai"
    if y < 0:
        return "--shadow-dock" if blur <= 40 else "--shadow-overlay"
    if blur <= 10:
        return "--shadow-crisp"
    if a >= 0.10:
        return "--shadow-elevated"
    if blur >= 50:
        return "--shadow-card-strong"
    if blur >= 24:
        return "--shadow-card"
    if blur > 10:
        return "--shadow-soft"
    return "--shadow-crisp"


def rewrite(prop: str, value: str, file: str) -> str | None:
    bang = ""
    v = value.strip()
    if v.endswith("!important"):
        bang = " !important"
        v = v[: -len("!important")].strip()
    if "var(" in v or v in {"none", "inherit", "0", "50%"}:
        return None
    if prop == "font-size":
        m = re.fullmatch(r"([0-9.]+)rpx", v)
        if not m:
            unmapped[f"font-size: {v}"] += 1
            return None
        token = font_token(float(m.group(1)), file)
        if token is None:
            unmapped[f"font-size: {v}"] += 1
            return None
        return f"var({token}){bang}"
    if prop == "border-radius":
        parts = v.split()
        if all(re.fullmatch(r"[0-9.]+(rpx)?|0", p) for p in parts):
            new = " ".join(radius_component(p) for p in parts)
            if new == v:
                if "rpx" in v:
                    pass  # hairline kept intentionally
                else:
                    unmapped[f"border-radius: {v}"] += 1
                return None
            return f"{new}{bang}"
        unmapped[f"border-radius: {v}"] += 1
        return None
    if prop == "box-shadow":
        token = shadow_token(v)
        if token is None:
            unmapped[f"box-shadow: {v}"] += 1
            return None
        return f"var({token}){bang}"
    return None


DECL = re.compile(r"(?P<prop>font-size|border-radius|box-shadow)(?P<sep>\s*:\s*)(?P<val>[^;}]+)")

for root in ROOTS:
    for path in sorted(Path(root).rglob("*.wxss")):
        rel = str(path)
        if rel in EXCLUDE:
            continue
        src = path.read_text()

        def sub(m: re.Match[str]) -> str:
            new = rewrite(m.group("prop"), m.group("val"), rel)
            if new is None:
                return m.group(0)
            changes[f"{m.group('prop')}: {m.group('val').strip()} -> {new}"] += 1
            return f"{m.group('prop')}{m.group('sep')}{new}"

        out = DECL.sub(sub, src)
        if out != src and WRITE:
            path.write_text(out)

print(f"{'WROTE' if WRITE else 'DRY RUN'}: {sum(changes.values())} replacements, {len(changes)} distinct")
for k, n in sorted(changes.items()):
    print(f"  {n:4d}  {k}")
print(f"\nUNMAPPED ({sum(unmapped.values())}):")
for k, n in unmapped.most_common():
    print(f"  {n:4d}  {k}")

# ---------------------------------------------------------------------------
# Phase 2 (2026-07-26): spacing rhythm + font-weight lock.
# ---------------------------------------------------------------------------

SPACE_LADDER = [2, 4, 8, 12, 16, 20, 24, 28, 32, 40, 48, 56, 64, 80, 96]
SPACING_PROPS = (
    "padding", "padding-top", "padding-right", "padding-bottom", "padding-left",
    "margin", "margin-top", "margin-right", "margin-bottom", "margin-left",
    "gap", "row-gap", "column-gap",
)
WEIGHT_STEPS = [400, 500, 600, 700, 800]
COMPLEX = ("var(", "calc(", "env(", "max(", "min(", "%")


def snap_space(n: float) -> float:
    if abs(n) <= 4 or abs(n) > 96:
        return n
    sign = -1 if n < 0 else 1
    m = abs(n)
    best = min(SPACE_LADDER, key=lambda s: (abs(s - m), -s))
    return sign * best


def snap_weight(n: float) -> int:
    if n < 450:
        return 400
    if n < 580:
        return 500
    if n < 680:
        return 600
    if n < 780:
        return 700
    return 800


def rewrite_phase2(prop: str, value: str) -> str | None:
    v = value.strip()
    bang = ""
    if v.endswith("!important"):
        bang = " !important"
        v = v[: -len("!important")].strip()
    if prop == "font-weight":
        if v == "normal":
            return f"400{bang}"
        if v == "bold":
            return f"700{bang}"
        if not re.fullmatch(r"[0-9]+", v):
            return None
        n = int(v)
        s = snap_weight(n)
        return f"{s}{bang}" if s != n else None
    if any(t in v for t in COMPLEX):
        return None

    def comp(part: str) -> str:
        m = re.fullmatch(r"(-?[0-9.]+)rpx", part)
        if not m:
            return part
        n = float(m.group(1))
        s = snap_space(n)
        return part if s == n else f"{s:g}rpx"

    parts = v.split()
    if not all(re.fullmatch(r"-?[0-9.]+(rpx)?|0|auto", p) for p in parts):
        return None
    new = " ".join(comp(p) for p in parts)
    return f"{new}{bang}" if new != v else None


PHASE2_DECL = re.compile(
    r"(?<![-a-zA-Z])(?P<prop>" + "|".join(SPACING_PROPS) + r"|font-weight)(?P<sep>\s*:\s*)(?P<val>[^;}]+)"
)

p2_changes: Counter[str] = Counter()

for root in ROOTS:
    for path in sorted(Path(root).rglob("*.wxss")):
        rel = str(path)
        if rel in EXCLUDE:
            continue
        src = path.read_text()

        def sub2(m: re.Match[str]) -> str:
            new = rewrite_phase2(m.group("prop"), m.group("val"))
            if new is None:
                return m.group(0)
            p2_changes[f"{m.group('prop')}: {m.group('val').strip()} -> {new}"] += 1
            return f"{m.group('prop')}{m.group('sep')}{new}"

        out = PHASE2_DECL.sub(sub2, src)
        if out != src and WRITE:
            path.write_text(out)

print(f"\nPHASE2 {'WROTE' if WRITE else 'DRY RUN'}: {sum(p2_changes.values())} replacements, {len(p2_changes)} distinct")
for k, n in sorted(p2_changes.items()):
    print(f"  {n:4d}  {k}")
