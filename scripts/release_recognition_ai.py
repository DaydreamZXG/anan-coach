from __future__ import annotations

from contextlib import contextmanager
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Iterator
from uuid import uuid4

from PIL import Image, ImageDraw, ImageFilter, ImageFont
from sqlalchemy import text

from app.contracts.external_learning.models import RecognitionKind
from app.core.config import get_settings
from app.db.session import get_session_factory
from app.domains.ai.governance.catalog import scenario_policy
from app.domains.ai.governance.evaluation import (
    EvaluationObservation,
    evaluate_suite,
    load_golden_manifest,
)
from app.domains.ai.governance.repository import AIGovernanceRepository
from app.domains.external_ingestion.recognition.grok_vision import (
    GrokVisionRecognitionProcessor,
)
from app.domains.external_ingestion.recognition.models import (
    ProcessorApproval,
    ProcessorRequest,
)


OPERATION = "recognition_process"
RELEASE_ID = "grok-vision-recognition-20260718-r1"
FONT_PATH = (
    Path(__file__).resolve().parents[1]
    / "backend/app/internal_services/assets/MapleMono-CN-Regular.ttf"
)


class _FixtureReader:
    def __init__(self, objects: dict[str, Path]) -> None:
        self._objects = objects

    @contextmanager
    def open(
        self,
        *,
        object_ref: str,
        expected_sha256: str,
        maximum_bytes: int,
        prefix: str,
        suffix: str,
    ) -> Iterator[Path]:
        del prefix, suffix
        path = self._objects[object_ref]
        payload = path.read_bytes()
        if len(payload) > maximum_bytes or sha256(payload).hexdigest() != expected_sha256:
            raise ValueError("recognition evaluation fixture failed integrity validation")
        yield path


def _render_fixture(path: Path, *, text_value: str, handwriting: bool) -> None:
    canvas = Image.new("RGB", (1600, 520), "white")
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.truetype(str(FONT_PATH), 78 if handwriting else 70)
    draw.text((90, 190), text_value, fill=(35, 45, 55), font=font)
    if handwriting:
        canvas = canvas.rotate(0.8, resample=Image.Resampling.BICUBIC, fillcolor="white")
        canvas = canvas.filter(ImageFilter.GaussianBlur(radius=0.25))
    canvas.save(path, format="PNG", optimize=True)


def _approval_for(
    approvals: tuple[ProcessorApproval, ...],
    *,
    scenario_key: str,
) -> ProcessorApproval:
    route_hint = {
        "recognition_printed_ocr": "import",
        "recognition_handwriting_ocr": "handwriting",
        "recognition_score_report": "score-report",
    }[scenario_key]
    return next(item for item in approvals if route_hint in item.route)


def _current_deployment(
    *,
    scenario_key: str,
    approval: ProcessorApproval,
) -> str | None:
    with get_session_factory()() as session:
        value = session.execute(
            text(
                """
                select deployment_id
                from ai_audit.scenario_deployments deployment
                where scenario_key = :scenario_key
                  and operation = :operation
                  and provider = :provider
                  and model = :model
                  and prompt_version = :prompt_version
                  and schema_version = :schema_version
                  and not exists (
                    select 1 from ai_audit.scenario_deployments successor
                    where successor.supersedes_deployment_id = deployment.deployment_id
                  )
                order by published_at desc limit 1
                """
            ),
            {
                "scenario_key": scenario_key,
                "operation": OPERATION,
                "provider": approval.provider,
                "model": approval.model_release,
                "prompt_version": approval.prompt_version,
                "schema_version": approval.output_contract,
            },
        ).scalar_one_or_none()
    return str(value) if value is not None else None


def main() -> None:
    settings = get_settings()
    approvals = tuple(
        ProcessorApproval.model_validate(item)
        for item in json.loads(settings.recognition_processor_approvals_json)
    )
    suites = [
        suite
        for suite in load_golden_manifest().suites
        if suite.scenario_key.startswith("recognition_")
    ]
    repository = AIGovernanceRepository()
    with TemporaryDirectory(prefix="anan-recognition-release-") as directory:
        root = Path(directory)
        objects: dict[str, Path] = {}
        for suite in suites:
            if _current_deployment(
                scenario_key=suite.scenario_key,
                approval=_approval_for(approvals, scenario_key=suite.scenario_key),
            ):
                continue
            for case in suite.cases:
                path = root / f"{case.case_id}.png"
                _render_fixture(
                    path,
                    text_value=str(case.expected_normalized_text),
                    handwriting=suite.scenario_key == "recognition_handwriting_ocr",
                )
                objects[str(case.input["imageRef"])] = path

        reader = _FixtureReader(objects)
        for suite in suites:
            approval = _approval_for(approvals, scenario_key=suite.scenario_key)
            existing = _current_deployment(
                scenario_key=suite.scenario_key,
                approval=approval,
            )
            if existing:
                print(f"deployment_ready={existing} scenario={suite.scenario_key}")
                continue
            processor = GrokVisionRecognitionProcessor(
                endpoint=settings.recognition_processor_endpoint,
                bearer_token=settings.recognition_processor_token,
                expected_provider_release=approval.provider_release,
                requested_model=settings.recognition_processor_model,
                expected_actual_model=settings.recognition_processor_actual_model,
                source_reader=reader,  # type: ignore[arg-type]
                maximum_input_bytes=settings.recognition_processor_max_input_bytes,
                maximum_output_bytes=settings.recognition_processor_max_output_bytes,
                maximum_pages=settings.recognition_processor_max_pages,
            )
            observations: list[EvaluationObservation] = []
            for case in suite.cases:
                object_ref = str(case.input["imageRef"])
                path = objects[object_ref]
                input_sha256 = sha256(path.read_bytes()).hexdigest()
                result = processor.process(
                    ProcessorRequest(
                        artifactVersionId=uuid4(),
                        ownerScopeHash=sha256(b"release-evaluation-owner").hexdigest(),
                        objectRef=object_ref,
                        mimeType="image/png",
                        inputSha256=input_sha256,
                        pageCount=1,
                        recognitionKind=(
                            RecognitionKind.HANDWRITING_OCR
                            if suite.scenario_key == "recognition_handwriting_ocr"
                            else RecognitionKind.PRINTED_OCR
                        ),
                        idempotencyKey=(
                            f"release-evaluation:{RELEASE_ID}:{case.case_id}"
                        ),
                    )
                )
                observations.append(
                    EvaluationObservation(
                        caseId=case.case_id,
                        output={
                            "textSpans": [
                                {"text": item.text} for item in result.text_spans
                            ],
                            "limitations": [
                                "识别结果为候选，必须由用户对照原图确认后才能进入学习证据。"
                            ],
                            "evidenceRefs": [object_ref],
                        },
                        costCny=Decimal("0"),
                    )
                )
            maximum = max(item.maximum_cost_cny for item in suite.cases)
            report = evaluate_suite(
                suite=suite,
                observations=tuple(observations),
                maximum_mean_cost_cny=maximum,
                maximum_p95_cost_cny=maximum,
            )
            with get_session_factory()() as session:
                repository.register_evaluation_suite(session, suite=suite)
                run_id = repository.record_evaluation_run(
                    session,
                    report=report,
                    operation=OPERATION,
                    provider=approval.provider,
                    model=approval.model_release,
                    model_tier=scenario_policy(suite.scenario_key).model_tier,
                    prompt_version=approval.prompt_version,
                    schema_version=approval.output_contract,
                    price_version=approval.price_version,
                )
                if report.status != "passed":
                    session.commit()
                    failures = {
                        item.case_id: item.failures
                        for item in report.cases
                        if not item.passed
                    }
                    raise RuntimeError(
                        f"recognition release evaluation failed: {failures}"
                    )
                deployment_id = repository.publish_scenario_deployment(
                    session,
                    release_id=RELEASE_ID,
                    scenario_key=suite.scenario_key,
                    operation=OPERATION,
                    provider=approval.provider,
                    model=approval.model_release,
                    prompt_version=approval.prompt_version,
                    schema_version=approval.output_contract,
                    evaluation_run_id=run_id,
                )
                session.commit()
            print(
                f"deployment_ready={deployment_id} scenario={suite.scenario_key} "
                f"cases={report.passed_count}/{report.case_count}"
            )


if __name__ == "__main__":
    main()
