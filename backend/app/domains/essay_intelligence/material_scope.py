from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Literal


MaterialScopeMode = Literal["explicit", "all", "fallback_all"]


@dataclass(frozen=True)
class ResolvedMaterialScope:
    mode: MaterialScopeMode
    material_orders: tuple[int, ...]


_NUMBER = r"[0-9０-９零〇一二两三四五六七八九十]+"
_MATERIAL = r"(?:给定)?(?:材料|资料)"
_ALL_PATTERN = re.compile(
    r"(?:全部|所有|全部的|所有的)(?:给定)?(?:材料|资料)|"
    r"(?:给定)?(?:材料|资料)(?:的)?(?:全部|所有)|整组(?:材料|资料)"
)
_RANGE_PATTERN = re.compile(
    rf"{_MATERIAL}\s*(?:第)?\s*({_NUMBER})\s*"
    rf"(?:—|–|－|-|~|～|至|到)\s*(?:{_MATERIAL})?\s*(?:第)?\s*({_NUMBER})"
)
_PREFIXED_NUMBER_PATTERN = re.compile(rf"{_MATERIAL}\s*(?:第)?\s*({_NUMBER})")
_ENUMERATION_PATTERN = re.compile(
    rf"\s*(?:、|,|，|和|及|与)\s*(?:{_MATERIAL})?\s*(?:第)?\s*({_NUMBER})"
)
_CHINESE_DIGITS = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}


def resolve_material_scope(
    prompt: str,
    available_material_orders: list[int] | tuple[int, ...],
) -> ResolvedMaterialScope:
    available = tuple(sorted(set(int(item) for item in available_material_orders)))
    if not available:
        return ResolvedMaterialScope(mode="fallback_all", material_orders=())

    normalized_prompt = _normalize_prompt(prompt)
    if _ALL_PATTERN.search(normalized_prompt):
        return ResolvedMaterialScope(mode="all", material_orders=available)

    referenced: set[int] = set()
    for match in _RANGE_PATTERN.finditer(normalized_prompt):
        start = _parse_number(match.group(1))
        end = _parse_number(match.group(2))
        if start is None or end is None or start > end:
            return ResolvedMaterialScope(mode="fallback_all", material_orders=available)
        referenced.update(range(start, end + 1))

    for match in _PREFIXED_NUMBER_PATTERN.finditer(normalized_prompt):
        value = _parse_number(match.group(1))
        if value is None:
            return ResolvedMaterialScope(mode="fallback_all", material_orders=available)
        referenced.add(value)
        tail = normalized_prompt[match.end() :]
        offset = 0
        while continuation := _ENUMERATION_PATTERN.match(tail, offset):
            continued_value = _parse_number(continuation.group(1))
            if continued_value is None:
                return ResolvedMaterialScope(
                    mode="fallback_all",
                    material_orders=available,
                )
            referenced.add(continued_value)
            offset = continuation.end()

    if not referenced:
        return ResolvedMaterialScope(mode="fallback_all", material_orders=available)
    if not referenced.issubset(set(available)):
        return ResolvedMaterialScope(mode="fallback_all", material_orders=available)
    return ResolvedMaterialScope(
        mode="explicit",
        material_orders=tuple(item for item in available if item in referenced),
    )


def _normalize_prompt(prompt: str) -> str:
    return (
        prompt.translate(str.maketrans("０１２３４５６７８９", "0123456789"))
        .replace("“", "")
        .replace("”", "")
        .replace('"', "")
        .replace("'", "")
    )


def _parse_number(value: str) -> int | None:
    if value.isdigit():
        return int(value)
    if "十" not in value:
        digits = [_CHINESE_DIGITS.get(char) for char in value]
        if any(item is None for item in digits):
            return None
        result = 0
        for digit in digits:
            result = result * 10 + int(digit)
        return result
    if value.count("十") != 1:
        return None
    tens_text, ones_text = value.split("十", 1)
    tens = 1 if not tens_text else _CHINESE_DIGITS.get(tens_text)
    ones = 0 if not ones_text else _CHINESE_DIGITS.get(ones_text)
    if tens is None or ones is None:
        return None
    return tens * 10 + ones
