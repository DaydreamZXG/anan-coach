from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Protocol


class CatalogUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class Question:
    question_id: str
    exam: str
    module: str
    stem: str
    options: list[str]
    answer: str
    explanation: str

    def stored(self) -> dict:
        return asdict(self)


class QuestionCatalog(Protocol):
    mode: str

    def practice(self, exam: str, count: int) -> list[Question]: ...

    def mock(self, exam: str, template_id: str | None) -> tuple[str, list[Question]]: ...


TEMPLATES = {
    "guokao": ("guokao-demo-complete-v1", 130),
    "shengkao": ("shengkao-demo-complete-v1", 120),
    "sydw": ("sydw-demo-complete-v1", 100),
}

MODULES = {
    "guokao": ["常识判断", "言语理解", "数量关系", "判断推理", "资料分析"],
    "shengkao": ["常识判断", "言语理解", "数量关系", "判断推理", "资料分析"],
    "sydw": ["职测常识", "言语理解", "数量分析", "判断推理", "综合应用"],
}


class DemoCatalog:
    """Owned synthetic content for protocol verification, never sold as real past papers."""

    mode = "demo"

    def _questions(self, exam: str, count: int) -> list[Question]:
        if exam not in MODULES:
            raise CatalogUnavailable(f"unsupported exam: {exam}")
        result: list[Question] = []
        for index in range(1, count + 1):
            module = MODULES[exam][(index - 1) % len(MODULES[exam])]
            answer_index = (index - 1) % 4
            result.append(
                Question(
                    question_id=f"demo-{exam}-{index:03d}",
                    exam=exam,
                    module=module,
                    stem=f"【演示题】{exam} {module}协议验证题 {index}：请选择标记为答案的选项。",
                    options=[f"{letter}. 演示选项 {letter}" for letter in "ABCD"],
                    answer="ABCD"[answer_index],
                    explanation=f"这是自有合成演示题；按确定性规则，第 {index} 题答案为 {'ABCD'[answer_index]}。",
                )
            )
        return result

    def practice(self, exam: str, count: int) -> list[Question]:
        if count < 5 or count > 20:
            raise ValueError("practice count must be between 5 and 20")
        return self._questions(exam, count)

    def mock(self, exam: str, template_id: str | None) -> tuple[str, list[Question]]:
        if exam not in TEMPLATES:
            raise CatalogUnavailable(f"unsupported exam: {exam}")
        expected, count = TEMPLATES[exam]
        if template_id and template_id != expected:
            raise CatalogUnavailable(f"template unavailable: {template_id}")
        return expected, self._questions(exam, count)


class LicensedProductionCatalog:
    mode = "licensed_production"

    def _blocked(self) -> None:
        raise CatalogUnavailable(
            "No active developer_api licensed publication view is configured; production catalog fails closed."
        )

    def practice(self, exam: str, count: int) -> list[Question]:
        self._blocked()

    def mock(self, exam: str, template_id: str | None) -> tuple[str, list[Question]]:
        self._blocked()
