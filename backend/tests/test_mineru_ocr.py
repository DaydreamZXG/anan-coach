from app.domains.external_ingestion.recognition.mineru_ocr import (
    _apply_formula_enrichment,
    _spans_from_formula_lines,
    _spans_from_markdown,
)
from app.domains.xfyun.formula import FormulaLine, FormulaRecognition, XfyunFormulaError


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
