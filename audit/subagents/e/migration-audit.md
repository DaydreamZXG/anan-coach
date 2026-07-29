# Backend migration audit

Audit commit: `01752f1d7b8b606b6900160eebba0888d8def294`

This audit was read-only with respect to application, migration, deployment, and test source. Database execution used only the ephemeral local PostgreSQL cluster created by the existing pytest fixture.

## Result

The Alembic graph is structurally linear but is not executable from the repository's audited legacy baseline with the current application code.

- Migration files: **71**
- Bases: **1** (`20260712_00`)
- Heads: **1** (`20260728_70`)
- Branches/merge heads observed: **none**
- `alembic heads` / `alembic history`: **pass**
- Audited baseline → head execution: **fail at `20260716_33`**
- General pytest schema fixture: assembled through imports ending at `20260727_69`; it does not execute the real Alembic chain and does not reference `20260728_70`
- SQLAlchemy model/autogenerate comparison: **not available** (`target_metadata = None`; repositories predominantly use raw SQL)

Having one head and failing an upgrade are not contradictory. `alembic heads` verifies graph topology. The PostgreSQL replay executes every migration body and fails when revision `20260716_33` calls a current repository method whose signature has changed.

## M-01 — The historical upgrade path is coupled to mutable runtime code

Classification: confirmed defect  
Mapped finding: `E-001`  
Severity: P0  
Confidence: high

### Reproduction

From `backend/`:

```text
TEST_DATABASE_URL= .venv/bin/python -m pytest -q \
  tests/test_migrations.py::test_wechat_identity_migration_upgrades_from_audited_baseline
```

The test provisions the audited legacy schema, stamps `20260712_00`, upgrades to `20260713_12`, seeds legacy facts, then requests `upgrade head`. Revision `20260716_33` fails:

```text
TypeError: MockExamRepository.get_frozen_question_sources()
missing 1 required keyword-only argument: 'knowledge_release'
```

`20260716_33_stage6_mock_reporting.py:62-70` imports `MockExamRepository`, `freeze_learner_bundle`, and `question_content_version_id` from the live application. The current repository method now requires `knowledge_release`. A second historical migration (`20260715_21`) also imports live application modules, so the failure is a migration design pattern rather than an isolated typo.

Evidence:

- `evidence/failure-release-and-migration-targeted.log`
- `evidence/migrations-import-runtime-code.txt`
- `backend/migrations/versions/20260716_33_stage6_mock_reporting.py:58`
- `backend/app/domains/mock_exam/repository.py:330`

Expected: a migration already present in the chain remains self-contained and executable as later application code evolves.

Actual: its behavior and call signature change with the installed application package, so the same migration artifact no longer reaches head.

## M-02 — Revision authority is copied and currently disagrees

Classification: confirmed defect  
Mapped finding: `E-001`  
Severity: P0  
Confidence: high

| Authority | Declared revision |
|---|---:|
| Alembic graph | `20260728_70` |
| Artifact compatibility contract | `20260728_70` |
| `Settings.required_database_revision` default | `20260727_68` |
| `ops/env.example` | `20260727_69` |
| `ops/ilink-channel.env.example` | `20260727_68` |
| `ops/weknora.env.example` | `20260727_68` |
| General pytest schema assembly | last explicit import `20260727_69` |
| Backend README | says current head is `20260713_18` |

`ops/ilink-channel.env.example` and `ops/weknora.env.example` already had uncommitted user edits when the audit began, so their current `_68` values are supporting working-tree evidence, not claims about commit `01752f1`. The tracked `Settings` default (`_68`), tracked `ops/env.example` (`_69`), and tracked artifact contract (`_70`) are sufficient to confirm the mismatch.

`ops/scripts/activate_release.sh:83-97` derives the artifact head and exits unless it exactly equals `REQUIRED_DATABASE_REVISION`. Thus the checked-in example/default values are not merely stale documentation: using them blocks activation of this artifact. `/ready` also compares the database revision with the configured required revision, so an `_70` database paired with the default `_68` value is rejected.

The tracked test `test_operations_env_declares_weknora_activation_contract` fails because it expects `_68` while `ops/env.example` contains `_69`; neither equals the actual `_70` head. This demonstrates that the tests are another copied authority, not a resolution mechanism.

Evidence:

- `evidence/revision-drift.txt`
- `evidence/pytest-failure-clusters.csv`
- `ops/release-db-compatibility.env:1`
- `backend/app/core/config.py:193`
- `backend/app/core/config.py:671`
- `ops/scripts/activate_release.sh:83`

## M-03 — The ordinary test database is a shadow schema, not an Alembic head

Classification: confirmed test-infrastructure gap  
Mapped findings: `E-001`, `E-003`  
Severity: P1  
Confidence: high

`backend/tests/conftest.py::_reset_test_schema` loads a 2,950-line `minimal_schema.sql`, manually adds a constraint, then imports selected private `_STATEMENTS`/`_TEST_STATEMENTS` from migration modules. It never runs `alembic upgrade head`, does not create a truthful `alembic_version`, and ends its explicit migration imports at `_69`.

Consequences observed in this audit:

1. The general test suite can run against a schema that cannot be obtained through the real migration path.
2. The failing baseline-to-head migration was only detected by the dedicated migration test.
3. Readiness tests seed `alembic_version` from the stale runtime default instead of exercising the artifact head.
4. Revision `_70`'s processor-release data is absent from the standard schema assembly unless an individual test supplies it.

The inventory is in `evidence/migration-inventory.csv`. A `false` value in `referenced_by_pytest_fixture` does not by itself mean the object is absent—some earlier/later DDL may have been copied into `minimal_schema.sql`. It proves that the fixture is not a revision-faithful execution path.

## Baseline and model-consistency boundary

`20260712_00` intentionally contains no DDL. `backend/migrations/README.md` requires a complete audited legacy schema to be provisioned and stamped before Alembic is used. Therefore:

- a blank-database `alembic upgrade head` is not a supported check;
- the repository's dedicated audited-baseline replay is the valid migration check, and it currently fails;
- no production database or real data was accessed;
- production drift, installed extensions/roles, row counts, and the live `alembic_version` remain unverified;
- `migrations/env.py` exposes no SQLAlchemy `MetaData`, so `alembic check` cannot compare declarative models with migrations;
- full head → base downgrade was not executed because the baseline is deliberately non-reconstructive and several migrations correctly reject destructive downgrade when facts exist.

Passing controls from the full suite include nine server-setup schema contract tests and the separate stage 1.5, stage 3.5, knowledge-release migration tests other than the baseline-to-head case. These do not override the failed end-to-end replay.

## Recommended Wave 0 scope

1. Establish one generated database-compatibility authority from the artifact's Alembic head; consume it in runtime settings, release scripts, examples, readiness tests, and rollback metadata.
2. Remove live `app.*` imports from historical migration execution. Freeze the required transform inside the migration (or provide a deliberately versioned compatibility module whose interface is immutable).
3. Add a CI job that provisions the audited baseline and executes the entire chain to the artifact head on PostgreSQL.
4. Make the ordinary integration fixture either run the real chain or verify its resulting schema/data fingerprint and `alembic_version` against that chain.
5. Gate release activation on the same full-chain test and exact head contract.

Regression tests:

- `test_wechat_identity_migration_upgrades_from_audited_baseline`
- exact assertion that settings, artifact metadata, deployment examples, rollback metadata, and `alembic heads` resolve to the same revision
- `/ready` against the actual artifact head
- an ephemeral audited-baseline → head → application smoke test
- a fixture fingerprint comparison against a database produced by the real chain

Rollback guidance for a later repair wave: do not downgrade a live database to compensate for application metadata drift. Roll back the application only to an artifact whose declared compatibility contract includes the live revision; otherwise roll forward with a compatibility release.
