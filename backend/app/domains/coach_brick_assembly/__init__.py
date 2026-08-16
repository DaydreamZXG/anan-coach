"""Read-only assembly of the teaching bricks consumed by Anan coaching flows."""

from app.domains.coach_brick_assembly.service import (
    CoachBrickAssembly,
    CoachBrickAssemblyReason,
    CoachTeachingBrickAssembler,
    assembly_reason_message,
    project_exam_target_scope,
)

__all__ = [
    "CoachBrickAssembly",
    "CoachBrickAssemblyReason",
    "CoachTeachingBrickAssembler",
    "assembly_reason_message",
    "project_exam_target_scope",
]
