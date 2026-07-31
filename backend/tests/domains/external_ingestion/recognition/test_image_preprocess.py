from __future__ import annotations

from PIL import Image

from app.domains.external_ingestion.recognition.image_preprocess import (
    prepare_for_ocr,
)


def _noisy_photo() -> Image.Image:
    """模拟手机拍照：深色桌面背景 + 中央大块白纸题目区（占画面大半，真实比例）。"""
    img = Image.new("RGB", (800, 1000), (60, 60, 60))  # 深灰桌面
    # 中央白色文档区（120,150）~（680,850），面积约 65%
    for x in range(120, 680):
        for y in range(150, 850):
            img.putpixel((x, y), (245, 245, 245))
    return img


def _plain_page() -> Image.Image:
    """整页白纸，无背景可裁（auto_crop 应跳过）。"""
    return Image.new("RGB", (600, 800), (255, 255, 255))


def test_auto_crop_removes_dark_background() -> None:
    img = _noisy_photo()
    result = prepare_for_ocr(img, enable_crop=True, max_long_edge=0)

    assert result.cropped is True
    # 裁剪后接近中央白纸区（约 560x700），不含深灰桌面。
    assert result.image.width >= 500
    assert result.image.width <= 620
    assert result.image.height >= 640
    assert result.image.height <= 760


def test_plain_page_skips_crop() -> None:
    img = _plain_page()
    result = prepare_for_ocr(img, enable_crop=True, max_long_edge=0)

    assert result.cropped is False
    assert result.image.size == (600, 800)


def test_downscale_long_edge() -> None:
    img = _plain_page()  # 600x800，长边 800
    result = prepare_for_ocr(img, enable_crop=True, max_long_edge=400)

    assert result.resized is True
    assert max(result.image.size) == 400


def test_small_image_skips_crop() -> None:
    img = Image.new("RGB", (200, 150), (0, 0, 0))
    result = prepare_for_ocr(img, enable_crop=True, max_long_edge=0)

    assert result.cropped is False
    assert result.image.size == (200, 150)


def test_exif_transpose_applied() -> None:
    # 用 PIL 标准方式写入 EXIF Orientation=6（旋转 90°），验证被转正。
    from io import BytesIO
    from PIL import Image as _PIL

    img = _PIL.new("RGB", (400, 200), (255, 255, 255))
    exif = img.getexif()
    exif[0x0112] = 6  # Orientation: rotate 90 CW
    buffer = BytesIO()
    img.save(buffer, "JPEG", exif=exif)
    buffer.seek(0)
    loaded = _PIL.open(buffer)

    result = prepare_for_ocr(loaded, enable_crop=False, max_long_edge=0)
    assert result.image.width == 200
    assert result.image.height == 400
