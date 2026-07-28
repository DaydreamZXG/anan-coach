from __future__ import annotations

from typing import Final


OFFICIAL_POSITION_CONTRACT: Final = "guokao-official-position@v1"

# These fields are the auditable source payload preserved from the official
# Guokao workbook. They are not themselves additional qualification rules:
# deterministic fields are compiled explicitly and prose conditions remain in
# the official remark for learner confirmation.
OFFICIAL_POSITION_FIELD_NAMES: Final = (
    "departmentCode",
    "departmentName",
    "agencyName",
    "organizationType",
    "positionName",
    "positionAttribute",
    "positionDistribution",
    "positionDescription",
    "positionCode",
    "institutionLevel",
    "examCategory",
    "headcount",
    "major",
    "education",
    "degree",
    "politicalStatus",
    "grassrootsMinimumYears",
    "serviceProject",
    "professionalTest",
    "interviewRatio",
    "workLocation",
    "settlementLocation",
    "remark",
    "departmentWebsite",
    "phone1",
    "phone2",
    "phone3",
)
