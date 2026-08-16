from __future__ import annotations

from copy import deepcopy

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.domains.teaching_packages import (
    AttachStructuredVisualEvidenceInput,
    CompileTeachingPackageInput,
    ExamTargetScope,
    ReusableMethodAsset,
    StructuredVisualAttachReasonCode,
    StructuredVisualEvidence,
    StructuredVisualEvidenceReader,
    TeachingQuestionCandidate,
    TeachingQuestionVisualBinding,
    attach_structured_visual_evidence,
    compile_teaching_package,
)


ASSET_SHA = "1" * 64
OTHER_ASSET_SHA = "2" * 64


def package():
    scope = ExamTargetScope(
        examCode="guokao",
        regionCode="national",
        year=2026,
        categoryCode="general",
        subjectCode="xingce",
    )
    method = ReusableMethodAsset(
        methodCardRef="method-card:growth-rate-base:v7",
        microSkillId="growth-rate-base",
        title="增长率分母选择",
        recognition="已知现期量和增长量。",
        methodSteps=("确认现期量和增长量", "计算基期量"),
        applicability=("求同比增长率",),
    )
    candidates = tuple(
        TeachingQuestionCandidate(
            questionRef=question_ref,
            sourceScopeKey=scope.scope_key,
            microSkillId="growth-rate-base",
            exposureState="unseen",
            stem=f"{question_ref}：根据图表计算增长率。",
            options=("A. 10%", "B. 20%", "C. 30%", "D. 40%"),
            answerKey=("B",),
            explanation="基期为 100，增长量为 20。",
        )
        for question_ref in ("question:student:v1", "question:transfer:v1")
    )
    result = compile_teaching_package(
        CompileTeachingPackageInput(
            targetScopeKey=scope.scope_key,
            microSkillId="growth-rate-base",
            sourceQuestionRef="question:source:v1",
            methodAsset=method,
            questionCandidates=candidates,
        )
    )
    return result.package_candidates[0]


def evidence(
    *,
    question_ref: str = "question:student:v1",
    content_version_id: str = "content:student:v1",
    source_sha: str = ASSET_SHA,
    review_state: str = "verified",
    audiences: tuple[str, ...] = ("student", "verifier"),
    evidence_ref: str | None = None,
) -> StructuredVisualEvidence:
    return StructuredVisualEvidence(
        evidenceRef=evidence_ref or f"visual:{question_ref}:v1",
        questionRef=question_ref,
        contentVersionId=content_version_id,
        sourceAssetSha256=source_sha,
        reviewState=review_state,
        audiences=audiences,
        chartType="table",
        title="2024 年某项指标",
        unit="万人",
        facts=(
            {
                "factRef": f"visual:{question_ref}:fact:2023",
                "kind": "table_cell",
                "dimensions": ("2023", "指标"),
                "value": "100",
                "unit": "万人",
            },
            {
                "factRef": f"visual:{question_ref}:fact:2024",
                "kind": "table_cell",
                "dimensions": ("2024", "指标"),
                "value": "120",
                "unit": "万人",
            },
        ),
    )


def test_verifier_only_media_is_not_exposed_to_student_projection() -> None:
    student_ref = "question:student:v1"
    student = evidence(question_ref=student_ref)
    explanation = evidence(
        question_ref=student_ref,
        audiences=("verifier",),
        evidence_ref="visual:question:student:v1:analysis",
    )
    command_input = AttachStructuredVisualEvidenceInput(
        package=package(),
        bindings=(
            TeachingQuestionVisualBinding(
                questionRef=student_ref,
                contentVersionId="content:student:v1",
                visualRequired=True,
                sourceAssetSha256s=(ASSET_SHA,),
                evidence=(student, explanation),
            ),
            binding("question:transfer:v1", required=False, visual=None),
        ),
    )

    result = attach_structured_visual_evidence(command_input)

    assert result.state == "attachable"
    sidecar = result.package_candidates[0]
    assert [item.evidence_ref for item in sidecar.student_visual_evidence] == [
        student.evidence_ref
    ]
    assert [item.evidence_ref for item in sidecar.student_verifier_visual_evidence] == [
        explanation.evidence_ref,
        student.evidence_ref,
    ]


def binding(
    question_ref: str,
    *,
    required: bool,
    visual: StructuredVisualEvidence | None = None,
    content_version_id: str | None = None,
    source_shas: tuple[str, ...] | None = None,
) -> TeachingQuestionVisualBinding:
    suffix = "student" if "student" in question_ref else "transfer"
    return TeachingQuestionVisualBinding(
        questionRef=question_ref,
        contentVersionId=content_version_id or f"content:{suffix}:v1",
        visualRequired=required,
        sourceAssetSha256s=source_shas if source_shas is not None else (ASSET_SHA,),
        evidence=(visual,) if visual is not None else (),
    )


def command(
    *,
    student: TeachingQuestionVisualBinding | None = None,
    transfer: TeachingQuestionVisualBinding | None = None,
) -> AttachStructuredVisualEvidenceInput:
    return AttachStructuredVisualEvidenceInput(
        package=package(),
        bindings=(
            student
            or binding(
                "question:student:v1",
                required=True,
                visual=evidence(),
            ),
            transfer
            or binding("question:transfer:v1", required=False, visual=None),
        ),
    )


def test_verified_identity_bound_evidence_attaches_without_answer_fields() -> None:
    result = attach_structured_visual_evidence(command())

    assert result.state == "attachable"
    assert result.reason_codes == ()
    sidecar = result.package_candidates[0]
    assert len(sidecar.student_visual_evidence) == 1
    visual = sidecar.student_visual_evidence[0]
    assert visual.question_ref == "question:student:v1"
    assert visual.facts[1].value == "120"
    payload = visual.model_dump(mode="json", by_alias=True)
    assert set(payload) == {
        "evidenceRef",
        "questionRef",
        "chartType",
        "title",
        "unit",
        "facts",
        "evidenceKind",
        "audiences",
    }
    serialized = str(payload)
    for forbidden in ("answerKey", "explanation", "methodSteps", "mastery", "ability"):
        assert forbidden not in serialized


def test_required_visual_without_evidence_fails_closed() -> None:
    student = binding("question:student:v1", required=True, visual=None)
    result = attach_structured_visual_evidence(command(student=student))

    assert result.state == "unavailable"
    assert result.reason_codes == (
        StructuredVisualAttachReasonCode.VISUAL_EVIDENCE_UNAVAILABLE,
    )
    assert result.package_candidates == ()


@pytest.mark.parametrize("review_state", ("candidate", "needs_review"))
def test_unreviewed_evidence_never_auto_promotes(review_state: str) -> None:
    student = binding(
        "question:student:v1",
        required=True,
        visual=evidence(review_state=review_state),
    )
    result = attach_structured_visual_evidence(command(student=student))

    assert result.state == "unavailable"
    assert result.reason_codes == (
        StructuredVisualAttachReasonCode.VISUAL_EVIDENCE_UNVERIFIED,
    )
    assert student.evidence[0].review_state == review_state


def test_machine_verified_evidence_remains_auditable_and_attachable() -> None:
    student = binding(
        "question:student:v1",
        required=True,
        visual=evidence(review_state="machine_verified"),
    )

    result = attach_structured_visual_evidence(command(student=student))

    assert result.state == "attachable"
    assert student.evidence[0].review_state == "machine_verified"


@pytest.mark.parametrize(
    "student",
    (
        binding(
            "question:student:v1",
            required=True,
            visual=evidence(question_ref="question:other:v1"),
        ),
        binding(
            "question:student:v1",
            required=True,
            visual=evidence(content_version_id="content:student:v0"),
        ),
        binding(
            "question:student:v1",
            required=True,
            visual=evidence(source_sha=OTHER_ASSET_SHA),
        ),
    ),
)
def test_question_content_or_asset_mismatch_fails_closed(
    student: TeachingQuestionVisualBinding,
) -> None:
    result = attach_structured_visual_evidence(command(student=student))

    assert result.state == "unavailable"
    assert result.reason_codes == (
        StructuredVisualAttachReasonCode.VISUAL_EVIDENCE_VERSION_MISMATCH,
    )
    assert result.package_candidates == ()


def test_duplicate_or_wrong_slot_binding_returns_no_partial_sidecar() -> None:
    duplicate = binding(
        "question:student:v1", required=True, visual=evidence()
    )
    result = attach_structured_visual_evidence(
        AttachStructuredVisualEvidenceInput(
            package=package(),
            bindings=(duplicate, duplicate),
        )
    )

    assert result.state == "unavailable"
    assert result.reason_codes == (
        StructuredVisualAttachReasonCode.VISUAL_EVIDENCE_BINDING_DUPLICATE,
    )
    assert result.package_candidates == ()


def test_binding_order_is_deterministic_and_input_remains_unchanged() -> None:
    original = command()
    before = deepcopy(original.model_dump(mode="json", by_alias=True))
    reordered = AttachStructuredVisualEvidenceInput(
        package=original.package,
        bindings=tuple(reversed(original.bindings)),
    )

    first = attach_structured_visual_evidence(original)
    second = attach_structured_visual_evidence(reordered)

    assert first == second
    assert original.model_dump(mode="json", by_alias=True) == before
    assert original.package.contract_version == "multi-exam-teaching-package@v2"


def test_non_visual_two_slot_package_attaches_empty_compatible_sidecar() -> None:
    result = attach_structured_visual_evidence(
        command(
            student=binding("question:student:v1", required=False, visual=None),
            transfer=binding("question:transfer:v1", required=False, visual=None),
        )
    )

    assert result.state == "attachable"
    sidecar = result.package_candidates[0]
    assert sidecar.student_visual_evidence == ()
    assert sidecar.user_transfer_visual_evidence == ()


def test_all_four_question_visuals_attach_in_stable_order() -> None:
    visuals = tuple(
        evidence(
            source_sha=str(index) * 64,
        ).model_copy(update={"evidence_ref": f"visual:{index}"})
        for index in (4, 2, 3, 1)
    )
    student = TeachingQuestionVisualBinding(
        questionRef="question:student:v1",
        contentVersionId="content:student:v1",
        visualRequired=True,
        sourceAssetSha256s=tuple(str(index) * 64 for index in (1, 2, 3, 4)),
        evidence=visuals,
    )

    result = attach_structured_visual_evidence(command(student=student))

    sidecar = result.package_candidates[0]
    assert sidecar.contract_version == "structured-visual-teaching-evidence@v2"
    assert [item.evidence_ref for item in sidecar.student_visual_evidence] == [
        "visual:1", "visual:2", "visual:3", "visual:4"
    ]


def test_reader_returns_only_current_head_and_every_visual(postgres_url) -> None:
    engine = create_engine(postgres_url, pool_pre_ping=True, future=True)
    reader = StructuredVisualEvidenceReader()
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql("drop schema if exists product cascade")
            connection.exec_driver_sql("create schema product")
            connection.exec_driver_sql(
                "create table product.questions ("
                "question_id text primary key, status text not null)"
            )
            connection.exec_driver_sql(
                "create table product.question_content_heads ("
                "question_id text primary key, content_version_id text not null)"
            )
            connection.exec_driver_sql(
                "create table product.structured_visual_evidence_versions ("
                "evidence_version_id text primary key, evidence_ref text not null, "
                "chart_type text not null, payload jsonb not null)"
            )
            connection.exec_driver_sql(
                "create table product.structured_visual_evidence_heads ("
                "evidence_ref text primary key, evidence_version_id text not null)"
            )
            connection.exec_driver_sql(
                "create table product.question_assets ("
                "asset_id text primary key, question_id text not null, part_kind text)"
            )
            connection.exec_driver_sql(
                "create table product.question_structured_visual_evidence_bindings ("
                "evidence_ref text not null, evidence_version_id text not null, "
                "question_id text not null, content_version_id text not null, "
                "source_asset_sha256 text not null, runtime_asset_id text)"
            )
            connection.exec_driver_sql(
                "insert into product.questions values ('q1', 'active')"
            )
            connection.exec_driver_sql(
                "insert into product.question_content_heads values ('q1', 'current-v2')"
            )
            for index in (4, 1, 3, 2):
                connection.execute(
                    text(
                        "insert into product.structured_visual_evidence_versions "
                        "values (:version, :ref, 'table', cast(:payload as jsonb))"
                    ),
                    {
                        "version": f"v{index}",
                        "ref": f"visual:{index}",
                        "payload": '{"chartType":"table","facts":[{"factRef":"fact:'
                        + str(index)
                        + '","kind":"table_cell","dimensions":["row","col"],'
                          '"value":"1","unit":""}]}',
                    },
                )
                connection.execute(
                    text(
                        "insert into product.structured_visual_evidence_heads "
                        "values (:ref, :version)"
                    ),
                    {"ref": f"visual:{index}", "version": f"v{index}"},
                )
                connection.execute(
                    text(
                        "insert into product.question_structured_visual_evidence_bindings "
                        "values (:ref, :version, 'q1', 'current-v2', :sha, null)"
                    ),
                    {
                        "ref": f"visual:{index}",
                        "version": f"v{index}",
                        "sha": str(index) * 64,
                    },
                )
            connection.exec_driver_sql(
                "insert into product.structured_visual_evidence_versions values ("
                "'old-v', 'visual:old', 'table', "
                "'{\"chartType\":\"table\",\"facts\":[{\"factRef\":\"old-fact\","
                "\"kind\":\"table_cell\",\"dimensions\":[\"r\",\"c\"],"
                "\"value\":\"1\",\"unit\":\"\"}]}'::jsonb)"
            )
            connection.exec_driver_sql(
                "insert into product.structured_visual_evidence_heads "
                "values ('visual:old', 'old-v')"
            )
            connection.exec_driver_sql(
                "insert into product.question_structured_visual_evidence_bindings "
                    "values ('visual:old', 'old-v', 'q1', 'stale-v1', repeat('9',64), null)"
            )
        with Session(engine) as session:
            binding = reader.binding_for_question(
                session, question_ref="q1", visual_required=True
            )
        assert binding is not None
        assert binding.content_version_id == "current-v2"
        assert [item.evidence_ref for item in binding.evidence] == [
            "visual:1", "visual:2", "visual:3", "visual:4"
        ]
    finally:
        with engine.begin() as connection:
            connection.exec_driver_sql("drop schema if exists product cascade")
        engine.dispose()


def test_reader_hides_evidence_when_question_is_not_active(postgres_url) -> None:
    engine = create_engine(postgres_url, pool_pre_ping=True, future=True)
    reader = StructuredVisualEvidenceReader()
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql("drop schema if exists product cascade")
            connection.exec_driver_sql("create schema product")
            connection.exec_driver_sql(
                "create table product.questions ("
                "question_id text primary key, status text not null)"
            )
            connection.exec_driver_sql(
                "create table product.question_content_heads ("
                "question_id text primary key, content_version_id text not null)"
            )
            connection.exec_driver_sql(
                "create table product.structured_visual_evidence_versions ("
                "evidence_version_id text primary key, evidence_ref text not null, "
                "chart_type text not null, payload jsonb not null)"
            )
            connection.exec_driver_sql(
                "create table product.structured_visual_evidence_heads ("
                "evidence_ref text primary key, evidence_version_id text not null)"
            )
            connection.exec_driver_sql(
                "create table product.question_assets ("
                "asset_id text primary key, question_id text not null, part_kind text)"
            )
            connection.exec_driver_sql(
                "create table product.question_structured_visual_evidence_bindings ("
                "evidence_ref text not null, evidence_version_id text not null, "
                "question_id text not null, content_version_id text not null, "
                "source_asset_sha256 text not null, runtime_asset_id text)"
            )
            connection.exec_driver_sql(
                "insert into product.questions values ('q1', 'quarantined')"
            )
            connection.exec_driver_sql(
                "insert into product.question_content_heads values ('q1', 'content-v1')"
            )
            connection.exec_driver_sql(
                "insert into product.structured_visual_evidence_versions values ("
                "'evidence-v1', 'visual:1', 'table', "
                "'{\"chartType\":\"table\",\"facts\":[]}'::jsonb)"
            )
            connection.exec_driver_sql(
                "insert into product.structured_visual_evidence_heads "
                "values ('visual:1', 'evidence-v1')"
            )
            connection.exec_driver_sql(
                "insert into product.question_structured_visual_evidence_bindings "
                "values ('visual:1', 'evidence-v1', 'q1', 'content-v1', repeat('1',64), null)"
            )
        with Session(engine) as session:
            binding = reader.binding_for_question(
                session, question_ref="q1", visual_required=True
            )
        assert binding is None
    finally:
        with engine.begin() as connection:
            connection.exec_driver_sql("drop schema if exists product cascade")
        engine.dispose()


def test_reader_unions_current_analysis_evidence_as_verifier_only(postgres_url) -> None:
    engine = create_engine(postgres_url, pool_pre_ping=True, future=True)
    reader = StructuredVisualEvidenceReader()
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql("drop schema if exists product cascade")
            connection.exec_driver_sql("create schema product")
            connection.exec_driver_sql(
                "create table product.questions ("
                "question_id text primary key, status text not null)"
            )
            connection.exec_driver_sql(
                "create table product.question_content_heads ("
                "question_id text primary key, content_version_id text not null)"
            )
            connection.exec_driver_sql(
                "create table product.question_content_versions ("
                "question_id text, content_version_id text, snapshot_payload jsonb, "
                "primary key(question_id,content_version_id))"
            )
            connection.exec_driver_sql(
                "create table product.question_explanations ("
                "question_id text primary key, analysis_source text, "
                "final_analysis_asset_refs jsonb)"
            )
            connection.exec_driver_sql(
                "create table product.question_occurrences ("
                "occurrence_id text primary key, question_id text, "
                "source_occurrence_question_key text)"
            )
            connection.exec_driver_sql(
                "create table product.structured_visual_evidence_versions ("
                "evidence_version_id text primary key, evidence_ref text not null, "
                "chart_type text not null, payload jsonb not null)"
            )
            connection.exec_driver_sql(
                "create table product.structured_visual_evidence_heads ("
                "evidence_ref text primary key, evidence_version_id text not null)"
            )
            connection.exec_driver_sql(
                "create table product.question_assets ("
                "asset_id text primary key, question_id text not null, part_kind text)"
            )
            connection.exec_driver_sql(
                "create table product.question_structured_visual_evidence_bindings ("
                "evidence_ref text not null, evidence_version_id text not null, "
                "question_id text not null, content_version_id text not null, "
                "source_asset_sha256 text not null, runtime_asset_id text)"
            )
            connection.exec_driver_sql(
                "create table product.question_analysis_visual_evidence_bindings ("
                "evidence_ref text not null, evidence_version_id text not null, "
                "question_id text not null, content_version_id text not null, "
                "analysis_source text not null, "
                "source_occurrence_question_key text not null, "
                "source_image_ref text not null, runtime_object_key text, "
                "source_asset_sha256 text not null)"
            )
            connection.exec_driver_sql(
                "insert into product.questions values ('q1','active')"
            )
            connection.exec_driver_sql(
                "insert into product.question_content_heads values ('q1','content-v1')"
            )
            connection.exec_driver_sql(
                "insert into product.question_content_versions values "
                "('q1','content-v1','{\"analysis_source\":\"original_analysis\"}'::jsonb)"
            )
            connection.execute(
                text(
                    "insert into product.question_explanations values "
                    "('q1','original_analysis',cast(:refs as jsonb))"
                ),
                {
                    "refs": '{"source":"original_analysis","assets":['
                    '{"imageSrc":"paper/formula.png",'
                    '"objectKey":"source/formula.png",'
                    '"contentSha256":"'
                    + "a" * 64
                    + '"}]}'
                },
            )
            connection.exec_driver_sql(
                "insert into product.question_occurrences values "
                "('o1','q1','pq_source')"
            )
            connection.execute(
                text(
                    "insert into product.structured_visual_evidence_versions "
                    "values ('v1','analysis-media:formula','formula',"
                    "cast(:payload as jsonb))"
                ),
                {
                    "payload": '{"chartType":"formula",'
                    '"evidenceKind":"formula",'
                    '"reviewState":"machine_verified",'
                    '"sourceRoles":["analysis"],'
                    '"audiences":["verifier"],'
                    '"facts":[{"factRef":"formula:1",'
                    '"kind":"formula","dimensions":["latex"],'
                    '"value":"x^2","unit":""}]}'
                },
            )
            connection.exec_driver_sql(
                "insert into product.structured_visual_evidence_heads values "
                "('analysis-media:formula','v1')"
            )
            connection.exec_driver_sql(
                "insert into product.question_analysis_visual_evidence_bindings values ("
                "'analysis-media:formula','v1','q1','content-v1','original_analysis',"
                "'pq_source','paper/formula.png','source/formula.png',repeat('a',64))"
            )
        with Session(engine) as session:
            binding = reader.binding_for_question(
                session, question_ref="q1", visual_required=False
            )
        assert binding is not None
        assert len(binding.evidence) == 1
        assert tuple(value.value for value in binding.evidence[0].audiences) == (
            "verifier",
        )
    finally:
        with engine.begin() as connection:
            connection.exec_driver_sql("drop schema if exists product cascade")
        engine.dispose()
