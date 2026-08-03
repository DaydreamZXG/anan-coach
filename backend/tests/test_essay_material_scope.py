from app.domains.essay_intelligence.material_scope import resolve_material_scope


def test_resolves_one_explicit_material() -> None:
    scope = resolve_material_scope("请根据材料4概括主要问题。", [1, 2, 3, 4, 5])

    assert scope.mode == "explicit"
    assert scope.material_orders == (4,)


def test_resolves_ranges_enumerations_and_chinese_numbers() -> None:
    assert resolve_material_scope(
        "根据给定资料2—4，提出建议。", [1, 2, 3, 4, 5]
    ).material_orders == (2, 3, 4)
    assert resolve_material_scope("根据材料一、三和材料四作答。", [1, 2, 3, 4]).material_orders == (
        1,
        3,
        4,
    )
    assert resolve_material_scope("根据“给定资料二至四”作答。", [1, 2, 3, 4]).material_orders == (
        2,
        3,
        4,
    )


def test_returns_all_for_explicit_all_or_unspecified_prompt() -> None:
    explicit_all = resolve_material_scope("请结合全部给定资料作答。", [1, 2, 3])
    unspecified = resolve_material_scope("请概括主要问题。", [1, 2, 3])

    assert explicit_all.mode == "all"
    assert explicit_all.material_orders == (1, 2, 3)
    assert unspecified.mode == "fallback_all"
    assert unspecified.material_orders == (1, 2, 3)


def test_falls_back_to_all_when_reference_is_invalid_or_reversed() -> None:
    missing = resolve_material_scope("根据材料4作答。", [1, 2, 3])
    reversed_range = resolve_material_scope("根据材料3至1作答。", [1, 2, 3])

    assert missing.mode == "fallback_all"
    assert missing.material_orders == (1, 2, 3)
    assert reversed_range.mode == "fallback_all"
    assert reversed_range.material_orders == (1, 2, 3)
