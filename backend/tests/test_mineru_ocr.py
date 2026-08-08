from app.domains.external_ingestion.recognition.mineru_ocr import (
    MinerURecognitionProcessor,
    _apply_formula_enrichment,
    _spans_from_formula_lines,
    _spans_from_markdown,
)
from app.domains.external_ingestion.recognition.errors import ProcessorResponseError
from app.domains.xfyun.formula import FormulaLine, FormulaRecognition, XfyunFormulaError
from pypdf import PdfReader, PdfWriter
import pytest


def test_formula_lines_become_ordered_matchable_text_spans() -> None:
    spans = _spans_from_formula_lines(
        (
            FormulaLine(text="若 $x^2=4$", confidence=0.96, contains_formula=True),
            FormulaLine(text="则 x 的值是", confidence=0.91, contains_formula=False),
        ),
        provider_request_id="mineru:test|xfyun:test",
    )

    assert [item.text for item in spans] == ["若 $x^2=4$", "则 x 的值是"]
    assert [item.confidence for item in spans] == [0.96, 0.91]
    assert spans[0].box.y < spans[1].box.y


class SuccessfulFormulaClient:
    def recognize(self, _image: bytes) -> FormulaRecognition:
        return FormulaRecognition(
            request_id="formula-1",
            lines=(FormulaLine(text="若 $x^2=4$", confidence=0.96, contains_formula=True),),
        )


class FailedFormulaClient:
    def recognize(self, _image: bytes) -> FormulaRecognition:
        raise XfyunFormulaError("provider failed")


def test_meaningful_formula_replaces_flattened_markdown_for_matching() -> None:
    primary = _spans_from_markdown("若 x2=4", "mineru:test")

    spans, provider_id = _apply_formula_enrichment(
        SuccessfulFormulaClient(),
        image=b"image",
        primary_spans=primary,
        provider_request_id="mineru:test",
    )

    assert [item.text for item in spans] == ["若 $x^2=4$"]
    assert provider_id == "mineru:test|xfyun:formula-1"


def test_formula_failure_keeps_primary_mineru_result() -> None:
    primary = _spans_from_markdown("原始题干", "mineru:test")

    spans, provider_id = _apply_formula_enrichment(
        FailedFormulaClient(),
        image=b"image",
        primary_spans=primary,
        provider_request_id="mineru:test",
    )

    assert spans == primary
    assert provider_id == "mineru:test|xfyun:unavailable"


def _processor(*, maximum_pages: int = 20) -> MinerURecognitionProcessor:
    return MinerURecognitionProcessor(
        endpoint="https://ocr.example.com/api/v4",
        bearer_token="secret",
        expected_provider_release="mineru-release-v1",
        source_reader=object(),  # type: ignore[arg-type]
        maximum_input_bytes=10_000_000,
        maximum_output_bytes=10_000_000,
        maximum_pages=maximum_pages,
    )


def test_native_multi_page_pdf_is_preserved_instead_of_opened_as_an_image(tmp_path) -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    writer.add_blank_page(width=595, height=842)
    source = tmp_path / "notes.pdf"
    with source.open("wb") as handle:
        writer.write(handle)
    original = source.read_bytes()

    pdf, formula_image, visual_hashes = _processor()._prepare_pdf(
        source,
        mime_type="application/pdf",
    )

    assert pdf == original
    assert len(PdfReader(source).pages) == 2
    assert formula_image == b""
    assert visual_hashes == ()


def test_native_pdf_page_limit_is_enforced_before_provider_dispatch(tmp_path) -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    writer.add_blank_page(width=595, height=842)
    source = tmp_path / "too-many-pages.pdf"
    with source.open("wb") as handle:
        writer.write(handle)

    with pytest.raises(ProcessorResponseError, match="page capacity"):
        _processor(maximum_pages=1)._prepare_pdf(source, mime_type="application/pdf")


def test_native_pdf_skips_single_image_formula_enrichment() -> None:
    primary = _spans_from_markdown("原始材料", "mineru:test")

    spans, provider_id = _apply_formula_enrichment(
        SuccessfulFormulaClient(),
        image=b"",
        primary_spans=primary,
        provider_request_id="mineru:test",
    )

    assert spans == primary
    assert provider_id == "mineru:test"
