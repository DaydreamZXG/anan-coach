# RU-ESSAY-RUBRIC-TARGET-001

## Objective

Remove two launch-blocking false contracts:

1. A published essay task may be offered only with a frozen, source-grounded AI rubric compiled from the prompt, complete material and answer requirements. The learner answer is never rubric input.
2. The ability page must present a next-exam preparation target, not label the historical cohort P80 as the upper bound or present a midpoint-to-midpoint gap as unexplained precision.

This is a bounded release unit. It does not redesign essay review, position eligibility, the ability estimator, payment, or unrelated pages.

## Evidence ledger

| ID | Class | Claim | Locator |
| --- | --- | --- | --- |
| E-USER-ESSAY-RUBRIC-001 | user confirmed | Essay scoring criteria are generated from the task, complete material and answer requirements; they are not an official/model answer. | conversation:2026-08-09 |
| E-USER-NEXT-EXAM-TARGET-001 | user confirmed | The target must cover the highest cutoff among positions the learner can apply to and include an upward allowance for the next exam. | conversation:2026-08-09 |
| E-CODE-RUBRIC-REGRESSION-001 | code observed | The current rubric pipeline publishes after the generator only and records no critic call, while the provider and product operations contract still require generator plus independent critic/reviser. | `backend/app/domains/essay_intelligence/rubric_ai_pipeline.py` |
| E-PROD-RUBRIC-COVERAGE-001 | production observed | 1,427 current essay tasks exist and only 5 have a current published rubric; the reproduced task has none. | read-only production query:2026-08-09 |
| E-PROD-TARGET-COHORT-001 | production observed | For this learner's 2026 Hunan major-family cohort, n=30, P80=123.24, P95=128.29 and max=135.70. The comparable 2025 cohort has P80=116.16 and P95=122.35. Across 1,209 nationwide same-major-family positions that pass known hard conditions, the 2026 maximum is 142.40. | read-only production diagnostic:2026-08-09 |
| E-CODE-GAP-001 | code observed | The displayed -0.87 is target recommended 119.13 minus current recommended 120.00; it is not the distance to the target upper bound. | `backend/app/domains/planning/service.py` |

## Frozen decisions

- Restore exactly two rubric model phases: generator, then independent critic/reviser. A rejected or invalid critic result cannot publish.
- Version the restored two-phase contract as pipeline v3. Production prompt v12 is retained as the successful dry-run receipt; the provider failed same-key replay, so publication uses prompt v13 in one generate-review-compile-publish transaction without deleting the v12 ledger or weakening fingerprint checks.
- Preserve deterministic source grounding, compilation, idempotency, cost ledger and append-only publication. The tolerant quote locator may normalize punctuation/spacing only; persisted evidence remains a verbatim source span.
- Treat current official rubrics as authoritative, but require a current system-compiled rubric to carry pipeline v3 generator/critic approval audit; legacy single-phase system rubrics are upgraded by append-only successor publication.
- The rubric provider document contains product content only. It must not contain a user ID, learner answer, learning history or inferred cause.
- Expose the preparation range as selected-region `P80 + uplift`, selected-region `P95 + uplift`, and the current-year maximum among all nationwide same-major-family positions that pass known hard conditions plus uplift. Preference region is not treated as a hard eligibility restriction.
- Derive uplift only when the equivalent prior-year cohort has at least three samples: use the non-negative mean of the P80 and P95 year-over-year changes, rounded down to a whole point. For the verified Hunan cohort this is 6 points. If comparable evidence is insufficient, use 0 and state that no trend allowance could be evidenced.
- The gap is the distance from the current recommended estimate to the preparation range's recommended line (P95 + uplift). The UI labels it `距稳妥线`, and explains the reference year, target year, cohort size and uplift basis.
- Reference positions are shown from highest score downward so the historical coverage line is auditable.

## Allowed implementation surface

- `backend/app/domains/essay_intelligence/rubric_ai_pipeline.py`
- `backend/app/domains/essay_intelligence/rubric_ai_contracts.py`
- `backend/tests/test_essay_rubric_ai_pipeline.py`
- `backend/app/domains/position_decision/learner_target_score.py`
- `backend/app/domains/position_decision/learner_target_runtime.py`
- `backend/app/domains/position_decision/learner_target_schemas.py`
- `backend/app/domains/planning/repository_support.py`
- `backend/app/domains/planning/service.py`
- focused backend target-score/planning tests
- `apps/wechat/miniprogram/pages/ability/index.ts`
- `apps/wechat/miniprogram/pages/ability/index.wxml`
- focused WeChat ability tests
- targeted production rubric publication after code deployment

## Non-goals

- No official-answer claim and no learner-answer-dependent rubric generation.
- No change to eligibility truth, learner qualification facts or the ability estimate itself.
- No unbounded bulk AI run in this release unit; first publish the reproduced and currently assigned tasks, then measure before scheduling inventory backfill.
- No claim that one year of trend evidence is a long-term forecast. It is displayed with limited confidence.

## Acceptance

- A pipeline test proves two provider calls, independent critic approval, source-bound compilation audit, and no learner answer in either request.
- Rejection, source mismatch and invalid evidence still fail closed without a rubric publication.
- Target-score tests prove current P80/P95/max semantics, the comparable-year uplift, no-evidence fallback, descending highest-score references and version-fingerprint changes.
- Planning tests prove gap-to-stable-line semantics and evidence text.
- WeChat tests prove `距稳妥线`, target/current labels and evidence explanation are rendered without false `-0.87` precision.
- Production verification confirms the reproduced essay task has a published rubric whose compilation audit contains both call IDs, and the ability API returns the revised target and gap.

## Stop conditions

- Provider critic configuration or replay semantics are not available in production.
- The prior-year cohort cannot be compared on the same score scale.
- A required change touches qualification truth, review scoring contracts, database schema, commerce or unrelated page behavior.
