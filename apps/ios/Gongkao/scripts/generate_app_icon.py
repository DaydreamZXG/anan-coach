#!/usr/bin/env python3
"""Generate D-logo app icon PNGs for iOS AppIcon asset catalog."""
import os
import math
from PIL import Image, ImageDraw

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "AppIcon.appiconset")
os.makedirs(OUTPUT_DIR, exist_ok=True)

SIZE = 1024
PADDING = 150
INNER = SIZE - PADDING * 2

def draw_d_logo(size, scale=1):
    """Draw the D-letter gradient logo on a transparent background."""
    W = H = size
    pad = int(size * (PADDING / SIZE))
    inner = size - pad * 2

    img = Image.new("RGBA", (W, H), (255, 255, 255, 0))
    draw = ImageDraw.Draw(img)

    # D shape parameters
    left = pad
    right = pad + inner
    top = pad
    bottom = pad + inner
    corner_r = int(inner * 0.20)
    stroke_w = int(inner * 0.24)
    fold_s = int(inner * 0.32)

    # Build D outline path (outer)
    # Start: top-left rounded corner
    path = []
    # Top bar (with top-left rounded)
    path.append((left + corner_r, top))
    # Top-right arc - we'll do it as a series of points
    # Actually, let's build the shape with bezier-like line segments
    # Simplified: D = left vertical bar + right semicircle + bottom bar + fold cut

    # We'll use a mask approach: draw filled D shape, then cut inner hole
    # Create shape as polygon with arcs approximated

    def add_arc(cx, cy, r, start_deg, end_deg, steps=40):
        points = []
        start = math.radians(start_deg)
        end = math.radians(end_deg)
        for i in range(steps + 1):
            t = start + (end - start) * i / steps
            x = cx + r * math.cos(t)
            y = cy + r * math.sin(t)
            points.append((x, y))
        return points

    # Outer D shape (before fold cut):
    # Top edge: from (left + corner_r, top) to (right - r_outer, top)
    # Top-right big arc: from top to right side
    # Right edge: straight down
    # Bottom-right arc
    # Bottom edge: to fold point
    # Fold up-left: to (left, bottom - fold_s)
    # Left edge: up to (left, top + corner_r)
    # Top-left rounded corner

    r_outer_top = inner * 0.32  # top outer arc radius
    r_outer_bottom = inner * 0.30  # bottom outer arc radius

    # Top right center: where the top arc pivots
    top_arc_cx = right - r_outer_top
    top_arc_cy = top + r_outer_top

    # Bottom right center
    bot_arc_cx = right - r_outer_bottom
    bot_arc_cy = bottom - r_outer_bottom

    points = []

    # Start at top-left rounded corner start
    points.append((left, top + corner_r))

    # Top-left corner arc (90 degrees: 180 to 270)
    arc_pts = add_arc(left + corner_r, top + corner_r, corner_r, 180, 270, 20)
    points.extend(arc_pts)

    # Top edge
    points.append((top_arc_cx, top))

    # Top-right outer arc (-90 to 0 degrees, from top to right)
    arc_pts = add_arc(top_arc_cx, top_arc_cy, r_outer_top, -90, 0, 40)
    points.extend(arc_pts)

    # Right side down to bottom arc start
    points.append((right, bot_arc_cy))

    # Bottom-right outer arc (0 to 90 degrees)
    arc_pts = add_arc(bot_arc_cx, bot_arc_cy, r_outer_bottom, 0, 90, 40)
    points.extend(arc_pts)

    # Bottom edge: right -> fold point
    fold_x = left + fold_s
    points.append((fold_x, bottom))

    # Fold: diagonal up-left
    points.append((left, bottom - fold_s))

    # Left edge up
    points.append((left, top + corner_r))

    # Create mask for outer shape
    outer_mask = Image.new("L", (W, H), 0)
    outer_draw = ImageDraw.Draw(outer_mask)
    outer_draw.polygon(points, fill=255)

    # Inner hole (D's white center)
    inner_w = inner * 0.44
    inner_h = inner * 0.44
    inner_left = left + stroke_w
    inner_top = top + (inner - inner_h) / 2
    inner_right = inner_left + inner_w
    inner_bottom = inner_top + inner_h
    inner_r = inner_h / 2

    # Inner shape: D-shaped hole
    inner_points = []
    # Left side (flat)
    inner_points.append((inner_left, inner_top))
    # Top-right inner arc
    inner_cx = inner_right - inner_r
    inner_cy = inner_top + inner_r
    arc_pts = add_arc(inner_cx, inner_cy, inner_r, -90, 90, 30)
    inner_points.extend(arc_pts)
    # Bottom
    inner_points.append((inner_left, inner_bottom))

    inner_mask = Image.new("L", (W, H), 0)
    inner_draw = ImageDraw.Draw(inner_mask)
    inner_draw.polygon(inner_points, fill=255)

    # Subtract inner from outer
    import numpy as np
    outer_arr = np.array(outer_mask)
    inner_arr = np.array(inner_mask)
    final_mask_arr = np.clip(outer_arr.astype(int) - inner_arr.astype(int), 0, 255).astype(np.uint8)
    final_mask = Image.fromarray(final_mask_arr, mode="L")

    # Create gradient (blue -> cyan -> purple, diagonal)
    gradient = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    grad_pixels = gradient.load()

    # Define gradient colors
    # Deep blue (top-left-ish): (74, 92, 242) -> RGB
    # Cyan (top-right-ish): (102, 191, 255)
    # Purple (bottom-right-ish): (168, 115, 250)
    c1 = (74, 92, 242)   # deep blue
    c2 = (102, 191, 255)  # cyan
    c3 = (168, 115, 250)  # purple

    for y in range(H):
        for x in range(W):
            # Diagonal gradient: mix based on position
            # t = 0 at top-left, t = 1 at bottom-right
            t = (x / W * 0.6 + y / H * 0.4)
            t = max(0, min(1, t))
            if t < 0.5:
                tt = t / 0.5
                r = int(c1[0] + (c2[0] - c1[0]) * tt)
                g = int(c1[1] + (c2[1] - c1[1]) * tt)
                b = int(c1[2] + (c2[2] - c1[2]) * tt)
            else:
                tt = (t - 0.5) / 0.5
                r = int(c2[0] + (c3[0] - c2[0]) * tt)
                g = int(c2[1] + (c3[1] - c2[1]) * tt)
                b = int(c2[2] + (c3[2] - c2[2]) * tt)
            grad_pixels[x, y] = (r, g, b, 255)

    # Apply mask to gradient
    result = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    result_pixels = result.load()
    grad_pixels = gradient.load()
    mask_pixels = final_mask.load()

    for y in range(H):
        for x in range(W):
            a = mask_pixels[x, y]
            if a > 0:
                r, g, b, _ = grad_pixels[x, y]
                result_pixels[x, y] = (r, g, b, a)

    # Shadow under fold? Skip for now - keep crisp.

    return result


def add_white_bg(img):
    """Add white background for AppIcon (iOS requires opaque)."""
    bg = Image.new("RGBA", img.size, (255, 255, 255, 255))
    bg.paste(img, (0, 0), img)
    return bg.convert("RGB")


# Sizes needed for iOS AppIcon (from Xcode template)
sizes = [
    # name, point_size, scale
    ("1024x1024@1x", 1024, 1),  # App Store
    ("60x60@2x", 60, 2),         # iPhone Notification iOS 7-14 2x
    ("60x60@3x", 60, 3),         # iPhone Notification iOS 7-14 3x
    ("40x40@2x", 40, 2),         # iPhone Settings iOS 7-14 2x
    ("40x40@3x", 40, 3),         # iPhone Settings iOS 7-14 3x
    ("57x57@2x", 57, 2),         # iPhone App iOS 5,6 2x (114px)
    ("120x120@1x", 120, 1),       # iPhone App iOS 7-14 2x
    ("180x180@1x", 180, 1),       # iPhone App iOS 7-14 3x
    ("20x20@2x", 20, 2),         # iPad Notification iOS 7-14 2x
    ("20x20@1x", 20, 1),         # iPad Notifications
    ("29x29@2x", 29, 2),         # iPad Settings
    ("29x29@1x", 29, 1),         # iPad Settings
    ("40x40@2x", 40, 2),         # iPad Spotlight
    ("40x40@1x", 40, 1),         # iPad Spotlight
    ("76x76@1x", 76, 1),         # iPad App
    ("76x76@2x", 76, 2),         # iPad App 2x
    ("83.5x83.5@2x", 83.5, 2),   # iPad Pro
]

# Generate at 1024 and downscale for each size
master = draw_d_logo(1024)
master_rgb = add_white_bg(master)

import json
images_list = []

for name, pts, scale in sizes:
    px = int(pts * scale)
    resized = master_rgb.resize((px, px), Image.LANCZOS)
    fname = f"Icon-{name}.png"
    out_path = os.path.join(OUTPUT_DIR, fname)
    resized.save(out_path, "PNG")
    print(f"Generated {fname} ({px}x{px})")

    idiom = "ios-marketing" if pts == 1024 else "universal"
    images_list.append({
        "filename": fname,
        "idiom": idiom,
        "scale": f"{int(scale)}x",
        "size": f"{pts}x{pts}",
    })

contents = {
    "images": images_list,
    "info": {
        "author": "xcode",
        "version": 1
    }
}

with open(os.path.join(OUTPUT_DIR, "Contents.json"), "w") as f:
    json.dump(contents, f, indent=2)

# Also create Assets.xcassets structure
assets_dir = os.path.join(os.path.dirname(__file__), "..", "Assets.xcassets")
os.makedirs(assets_dir, exist_ok=True)

# Move AppIcon.appiconset into Assets.xcassets
import shutil
dest = os.path.join(assets_dir, "AppIcon.appiconset")
if os.path.exists(dest):
    shutil.rmtree(dest)
shutil.move(OUTPUT_DIR, assets_dir)

# Contents.json for Assets.xcassets root
with open(os.path.join(assets_dir, "Contents.json"), "w") as f:
    json.dump({"info": {"author": "xcode", "version": 1}}, f, indent=2)

print(f"\nDone. Assets at: {assets_dir}")
