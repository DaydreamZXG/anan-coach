from __future__ import annotations

from scripts.match_reference_papers import candidate_evidence, normalize_stem


def test_normalize_stem_ignores_html_width_whitespace_and_punctuation() -> None:
    assert normalize_stem("<p>关于　生活中的化学品，下列说法正确的是（ ）。</p>") == (
        normalize_stem("关于生活中的化学品, 下列说法正确的是?")
    )


def test_candidate_evidence_tracks_reordered_options_by_answer_content() -> None:
    reference = {
        "sourceAnswer": "D",
        "sourceOptions": [
            {"content": "甲"},
            {"content": "乙"},
            {"content": "丙"},
            {"content": "正确内容"},
        ],
    }
    candidate = {
        "answer": "A",
        "optionTexts": ["正确内容", "丙", "乙", "甲"],
    }

    evidence = candidate_evidence(reference, candidate)

    assert evidence["optionsAgree"] is False
    assert evidence["optionSetAgrees"] is True
    assert evidence["answerAgrees"] is False
    assert evidence["answerContentAgrees"] is True


def test_candidate_evidence_rejects_same_generic_stem_with_other_options() -> None:
    reference = {
        "sourceAnswer": "A",
        "sourceOptions": [{"content": "臭氧"}, {"content": "氮气"}],
    }
    candidate = {
        "answer": "A",
        "optionTexts": ["二氧化碳", "氧气"],
    }

    evidence = candidate_evidence(reference, candidate)

    assert evidence["optionSetAgrees"] is False
    assert evidence["answerContentAgrees"] is False
