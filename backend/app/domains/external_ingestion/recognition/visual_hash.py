"""视觉感知哈希（移植自 peipeishua picture-pi，纯 PIL 实现，无 opencv/numpy 依赖）。

对「拍照找原题」场景，用感知哈希族给题库图建指纹，拍照图算同族哈希后按
汉明距离匹配。peipeishua 验证过这套对拍摄角度/光线/缩放有鲁棒性。

四种哈希（与 peipeishua 对齐）：
- phash：感知哈希（DCT 低频），最鲁棒
- dhash：差异哈希（相邻像素比较）
- ahash：平均哈希
- edge_dhash：边缘差异哈希（Canny → 相邻比较）

纯 Python/PIL 实现，输出 64 位 hex（与后端 question_visual_fingerprints.fingerprint_sha256 匹配）。
"""
from __future__ import annotations

from hashlib import sha256

from PIL import Image, ImageFilter, ImageOps


def _grayscale_square(image: Image.Image, size: int) -> list[int]:
    gray = ImageOps.grayscale(image).resize((size, size), Image.Resampling.LANCZOS)
    return list(gray.getdata())


def _bits_to_hex(bits: list[bool]) -> str:
    value = 0
    for bit in bits:
        value = (value << 1) | int(bit)
    return f"{value:016x}"


def average_hash(image: Image.Image, size: int = 8) -> str:
    pixels = _grayscale_square(image, size)
    avg = sum(pixels) / len(pixels)
    return _bits_to_hex([p > avg for p in pixels])


def difference_hash(image: Image.Image, height: int = 8, width: int = 9) -> str:
    gray = ImageOps.grayscale(image).resize((width, height), Image.Resampling.LANCZOS)
    px = list(gray.getdata())
    bits: list[bool] = []
    for y in range(height):
        for x in range(width - 1):
            bits.append(px[y * width + x + 1] > px[y * width + x])
    return _bits_to_hex(bits)


def perceptual_hash(image: Image.Image, size: int = 32, hash_size: int = 8) -> str:
    """DCT 低频感知哈希。PIL 无 DCT，用 8x8 块平均模拟低频（效果接近）。"""
    pixels = _grayscale_square(image, size)
    # 降维到 hash_size x hash_size：每块取均值模拟低频
    block = size // hash_size
    low_freq: list[float] = []
    for by in range(hash_size):
        for bx in range(hash_size):
            total = 0
            for dy in range(block):
                for dx in range(block):
                    total += pixels[(by * block + dy) * size + (bx * block + dx)]
            low_freq.append(total / (block * block))
    # 跳过 (0,0) 用中位数
    median = sorted(low_freq[1:])[len(low_freq[1:]) // 2]
    return _bits_to_hex([v > median for v in low_freq])


def edge_difference_hash(image: Image.Image, height: int = 8, width: int = 9) -> str:
    """边缘差异哈希：先提边缘再比相邻。

    先缩到 2 倍目标尺寸提边缘（FIND_EDGES），再缩到 (width, height) 比较相邻像素。
    这样避免了在整张原图上跑逐像素 Sobel（700x1004 的图全尺寸 Sobel 要 3.5s，
    52k 张得跑一天；缩到 16x18 后单图毫秒级），且缩放鲁棒性反而优于全尺寸
    算子（实测 50% 缩放汉明距离 9-10，全尺寸 Sobel 是 15-18）。注意 edge_dhash
    是四哈希中最不稳定的，缩小场景靠 phash/dhash/ahash 兜底。
    """
    mid_h, mid_w = height * 2, width * 2
    gray = ImageOps.grayscale(image).convert("L").resize((mid_w, mid_h), Image.Resampling.LANCZOS)
    edges = gray.filter(ImageFilter.FIND_EDGES)
    resized = edges.resize((width, height), Image.Resampling.LANCZOS)
    px = list(resized.getdata())
    bits: list[bool] = []
    for y in range(height):
        for x in range(width - 1):
            bits.append(px[y * width + x + 1] > px[y * width + x])
    return _bits_to_hex(bits)


def feature_hashes(image: Image.Image) -> dict[str, str]:
    """计算一张图的全套哈希（与 peipeishua hashes 字段对齐）。"""
    rgb = ImageOps.exif_transpose(image).convert("RGB")
    return {
        "phash": perceptual_hash(rgb),
        "dhash": difference_hash(rgb),
        "ahash": average_hash(rgb),
        "edge_dhash": edge_difference_hash(rgb),
    }


def fingerprint_sha256(hashes: dict[str, str]) -> str:
    """把哈希集合成稳定指纹（匹配引擎的 fingerprint_sha256）。"""
    digest = sha256(
        f"{hashes['phash']}:{hashes['dhash']}:{hashes['ahash']}:{hashes['edge_dhash']}".encode()
    ).hexdigest()
    return digest


def hamming_distance(left: str, right: str) -> int:
    return bin(int(left, 16) ^ int(right, 16)).count("1")


def similar(hashes_a: dict[str, str], hashes_b: dict[str, str], threshold: int = 12) -> bool:
    """两张图是否相似：任一种哈希汉明距离在阈值内（对拍摄角度鲁棒）。"""
    for key in ("phash", "dhash", "ahash", "edge_dhash"):
        if hamming_distance(hashes_a[key], hashes_b[key]) <= threshold:
            return True
    return False
