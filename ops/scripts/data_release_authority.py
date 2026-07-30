#!/usr/bin/env python3
"""Capture or verify the active commercial data release authority.

Application activation consumes this detached receipt read-only. Position and
knowledge publication are separate workflows and must complete before a receipt
is signed.
"""

from __future__ import annotations

import argparse
from datetime import datetime
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import stat
import tempfile
from typing import Any
from uuid import UUID

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row

from release_manifest import (
    DATA_RELEASE_RECEIPT_VERSION,
    _validate_data_release_receipt,
)


COMMIT = re.compile(r"^[0-9a-f]{40,64}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
BOOTSTRAP_BASELINE_VERSION = "anan-data-bootstrap-baseline/v1"


class DataReleaseAuthorityError(RuntimeError):
    """Raised when active business data cannot be proven exactly."""


_POSITION_RELEASE_SQL = """
select
  release.release_id::text as release_id,
  release.exam_code,
  release.exam_year,
  release.rule_set_version,
  release.position_data_version,
  release.content_digest as release_content_sha256,
  product.compute_position_release_content_digest(release.release_id)
    as recomputed_content_sha256,
  import_batch.source_sha256 as source_snapshot_sha256
from product.position_rule_releases release
join product.position_import_batches import_batch
  on import_batch.position_data_version = release.position_data_version
where release.exam_code = %(exam_code)s
  and release.exam_year = %(exam_year)s
  and release.status = 'active'
order by release.activated_at desc, release.release_id
"""

_POSITION_WINDOW_SQL = """
select
  business_window.window_id::text as window_id,
  business_window.window_kind as kind,
  business_window.sequence_no,
  business_window.opens_at,
  business_window.closes_at,
  business_window.official_source_sha256
from product.position_release_windows business_window
where business_window.release_id = %(release_id)s::uuid
  and business_window.opens_at <= transaction_timestamp()
  and transaction_timestamp() < business_window.closes_at
order by
  business_window.opens_at,
  business_window.window_kind,
  business_window.sequence_no
"""

_BOOTSTRAP_POSITION_RELEASE_SQL = """
select
  release.release_id::text as release_id,
  release.exam_code,
  release.exam_year,
  release.rule_set_version,
  release.position_data_version,
  release.activated_at
from product.position_rule_releases release
where release.exam_code = %(exam_code)s
  and release.exam_year = %(exam_year)s
  and release.status = 'active'
order by release.activated_at desc, release.release_id
"""

_BOOTSTRAP_POSITION_CONTENT_SQL = """
select
  encode(
    public.digest(
      convert_to(
        jsonb_build_object(
          'contractVersion', 'position-bootstrap-content@v1',
          'release', jsonb_build_object(
            'releaseId', release.release_id::text,
            'examCode', release.exam_code,
            'examYear', release.exam_year,
            'ruleSetVersion', release.rule_set_version,
            'positionDataVersion', release.position_data_version
          ),
          'positions', (
            select coalesce(
              jsonb_agg(
                to_jsonb(position) - 'created_at' - 'updated_at'
                order by position.position_id
              ),
              '[]'::jsonb
            )
            from product.positions position
            where position.year = release.exam_year
          ),
          'compilations', (
            select coalesce(
              jsonb_agg(
                to_jsonb(compilation) - 'compiled_at'
                order by compilation.position_id
              ),
              '[]'::jsonb
            )
            from product.position_rule_compilations compilation
            join product.positions position
              on position.position_id = compilation.position_id
             and position.year = release.exam_year
            where compilation.rule_set_version = release.rule_set_version
              and compilation.position_data_version =
                    release.position_data_version
          ),
          'requirements', (
            select coalesce(
              jsonb_agg(
                to_jsonb(requirement) - 'created_at'
                order by
                  requirement.position_id,
                  requirement.fact_key,
                  requirement.requirement_id
              ),
              '[]'::jsonb
            )
            from product.position_requirements requirement
            join product.positions position
              on position.position_id = requirement.position_id
             and position.year = release.exam_year
            where requirement.rule_set_version = release.rule_set_version
              and requirement.position_data_version =
                    release.position_data_version
          ),
          'officialEntryScores', (
            select coalesce(
              jsonb_agg(
                jsonb_build_object(
                  'positionId', score_link.position_id::text,
                  'link',
                    to_jsonb(score_link) - 'created_at' - 'updated_at',
                  'evidence',
                    to_jsonb(evidence) - 'created_at' - 'updated_at'
                )
                order by score_link.position_id, evidence.evidence_id
              ),
              '[]'::jsonb
            )
            from product.position_entry_score_links score_link
            join product.positions position
              on position.position_id = score_link.position_id
             and position.year = release.exam_year
            join product.position_entry_score_evidence evidence
              on evidence.evidence_id = score_link.evidence_id
             and evidence.position_code = position.position_code
          ),
          'historicalScores', (
            select coalesce(
              jsonb_agg(
                jsonb_build_object(
                  'positionId', score_link.position_id::text,
                  'link',
                    to_jsonb(score_link) - 'created_at' - 'updated_at',
                  'history',
                    to_jsonb(history) - 'created_at' - 'updated_at'
                )
                order by score_link.position_id, history.score_history_id
              ),
              '[]'::jsonb
            )
            from product.position_score_links score_link
            join product.positions position
              on position.position_id = score_link.position_id
             and position.year = release.exam_year
            join product.position_score_histories history
              on history.score_history_id = score_link.score_history_id
             and history.position_code = position.position_code
            where history.entry_score_min is not null
          ),
          'businessWindows', {business_windows}
        )::text,
        'UTF8'
      ),
      'sha256'
    ),
    'hex'
  ) as release_content_sha256,
  (
    select count(*)::integer
    from product.positions position
    where position.year = release.exam_year
  ) as position_count,
  (
    select count(*)::integer
    from product.position_rule_compilations compilation
    join product.positions position
      on position.position_id = compilation.position_id
     and position.year = release.exam_year
    where compilation.rule_set_version = release.rule_set_version
      and compilation.position_data_version = release.position_data_version
  ) as compilation_count
from product.position_rule_releases release
where release.release_id = %(release_id)s::uuid
"""

_BOOTSTRAP_WINDOWS_CONTENT_SQL = """
(
  select coalesce(
    jsonb_agg(
      to_jsonb(business_window) - 'created_at'
      order by
        business_window.opens_at,
        business_window.window_kind,
        business_window.sequence_no
    ),
    '[]'::jsonb
  )
  from product.position_release_windows business_window
  where business_window.release_id = release.release_id
)
"""

_KNOWLEDGE_RELEASE_SQL = """
select
  release.release_id::text as release_id,
  release.release_version,
  release.source_data_version,
  release.source_fingerprint,
  release.fresh_until,
  release.node_count,
  release.question_binding_count,
  release.question_binding_fingerprint,
  (
    select count(*)::integer
    from product.knowledge_map_nodes node
    where node.release_id = release.release_id
  ) as actual_node_count,
  (
    select count(*)::integer
    from product.knowledge_map_nodes node
    where node.release_id = release.release_id
      and (
        node.status is distinct from 'published'
        or node.exam_scope is distinct from release.exam_scope
        or node.track is distinct from release.track
      )
  ) as invalid_node_count,
  (
    select manifest.binding_count
    from product.knowledge_map_question_binding_manifest(
      release.release_id
    ) manifest
  ) as actual_binding_count,
  (
    select manifest.binding_fingerprint
    from product.knowledge_map_question_binding_manifest(
      release.release_id
    ) manifest
  ) as actual_binding_fingerprint,
  encode(
    public.digest(
      convert_to(
        jsonb_build_object(
          'contractVersion', 'knowledge-release-content@v1',
          'release', to_jsonb(release),
          'nodes', (
            select coalesce(
              jsonb_agg(
                to_jsonb(node) - 'created_at' - 'updated_at'
                order by node.node_id
              ),
              '[]'::jsonb
            )
            from product.knowledge_map_nodes node
            where node.release_id = release.release_id
          ),
          'questionBindings', (
            select coalesce(
              jsonb_agg(
                to_jsonb(binding) - 'created_at'
                order by binding.node_id, binding.question_id
              ),
              '[]'::jsonb
            )
            from product.knowledge_map_question_bindings binding
            where binding.release_id = release.release_id
          )
        )::text,
        'UTF8'
      ),
      'sha256'
    ),
    'hex'
  ) as release_content_sha256
from product.knowledge_map_releases release
where release.exam_scope = %(exam_scope)s
  and release.track = %(track)s
  and release.status = 'active'
  and release.activated_at is not null
  and release.retired_at is null
order by release.activated_at desc, release.release_id
"""


def _database_url(environment_name: str) -> str:
    value = os.environ.get(environment_name, "").strip()
    if not value:
        raise DataReleaseAuthorityError(
            f"{environment_name} must contain the database authority"
        )
    if value.startswith("postgresql+psycopg:"):
        value = value.replace("postgresql+psycopg:", "postgresql:", 1)
    _validate_database_url(value)
    return value


def _validate_database_url(database_url: str) -> None:
    overrides = sorted(
        name for name in os.environ if name.startswith("PG") and name != "PGPASSFILE"
    )
    if overrides:
        raise DataReleaseAuthorityError(
            "libpq environment may not override the data authority: "
            + ", ".join(overrides)
        )
    passfile = os.environ.get("PGPASSFILE")
    if passfile:
        path = Path(passfile)
        try:
            metadata = path.stat()
        except OSError as exc:
            raise DataReleaseAuthorityError("PGPASSFILE is unreadable") from exc
        if (
            not path.is_absolute()
            or not stat.S_ISREG(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_uid != os.geteuid()
        ):
            raise DataReleaseAuthorityError(
                "PGPASSFILE must be an absolute mode-0600 file owned by the "
                "verification process"
            )
    if not database_url.startswith(("postgresql://", "postgres://")):
        raise DataReleaseAuthorityError(
            "database authority must be an explicit libpq URL"
        )
    parameters = conninfo_to_dict(database_url)
    forbidden = sorted(
        name
        for name in (
            "password",
            "passfile",
            "hostaddr",
            "service",
            "options",
        )
        if parameters.get(name)
    )
    if forbidden:
        raise DataReleaseAuthorityError(
            "database URL contains an out-of-band authority parameter: "
            + ", ".join(forbidden)
        )
    for required in ("host", "port", "dbname", "user"):
        value = parameters.get(required)
        if not value or "," in value:
            raise DataReleaseAuthorityError(
                f"database URL must bind exactly one explicit {required}"
            )
    if not str(parameters["port"]).isdigit():
        raise DataReleaseAuthorityError("database URL port is invalid")


def _one(rows: list[dict[str, Any]], *, authority: str) -> dict[str, Any]:
    if len(rows) != 1:
        raise DataReleaseAuthorityError(
            f"expected exactly one active {authority}; found {len(rows)}"
        )
    return rows[0]


def _iso(value: Any, *, label: str) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise DataReleaseAuthorityError(f"{label} must be timezone-aware")
    return value.isoformat()


def _driver_rows(
    connection: Any,
    query: str,
    parameters: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Execute fixed SQL through psycopg or an externally-owned SQLAlchemy connection."""

    if hasattr(connection, "exec_driver_sql"):
        result = connection.exec_driver_sql(query, parameters or {})
        return [dict(row) for row in result.mappings().all()]
    result = connection.execute(query, parameters)
    return [dict(row) for row in result.fetchall()]


def _bootstrap_database_identity(
    connection: Any,
    *,
    expected_revision: str,
) -> dict[str, Any]:
    identity_rows = _driver_rows(
        connection,
        """
        select
          current_database() as database_name,
          (
            select system_identifier::text
            from pg_control_system()
          ) as system_identifier,
          transaction_timestamp() as observed_at
        """,
    )
    if len(identity_rows) != 1:
        raise DataReleaseAuthorityError("database identity query returned no row")
    revisions = [
        str(row["version_num"])
        for row in _driver_rows(
            connection,
            "select version_num from public.alembic_version order by version_num",
        )
    ]
    if revisions != [expected_revision]:
        raise DataReleaseAuthorityError(
            f"database revision must be exactly {expected_revision}; got {revisions}"
        )
    return identity_rows[0]


def _capture_bootstrap_snapshot(
    connection: Any,
    *,
    environment: str,
    expected_revision: str,
    application_commit: str,
    position_exam_code: str,
    position_exam_year: int,
    knowledge_exam_scope: str,
    knowledge_track: str,
) -> dict[str, Any]:
    """Capture the exact stopped-system authority before first adoption.

    The position digest deliberately has a legacy schema profile. It binds the
    mutable source rows consumed by migrations 78/79, including score evidence,
    rather than pretending the later immutable snapshot tables already exist.
    """

    identity = _bootstrap_database_identity(
        connection,
        expected_revision=expected_revision,
    )
    capabilities = _driver_rows(
        connection,
        """
        select
          to_regclass('product.position_release_windows') is not null
            as has_position_windows,
          exists (
            select 1
            from pg_catalog.pg_attribute attribute
            where attribute.attrelid =
                  'product.position_rule_releases'::regclass
              and attribute.attname = 'content_digest'
              and not attribute.attisdropped
          ) as has_frozen_position_digest,
          to_regclass('product.position_import_batches') is not null
            as has_position_import_batches
        """,
    )
    if len(capabilities) != 1:
        raise DataReleaseAuthorityError(
            "bootstrap schema capability query returned no row"
        )
    capability = capabilities[0]

    if bool(capability["has_frozen_position_digest"]) != bool(
        capability["has_position_import_batches"]
    ):
        raise DataReleaseAuthorityError(
            "position snapshot schema is only partially installed"
        )

    if capability["has_frozen_position_digest"]:
        position = _one(
            _driver_rows(
                connection,
                _POSITION_RELEASE_SQL,
                {
                    "exam_code": position_exam_code,
                    "exam_year": position_exam_year,
                },
            ),
            authority=(f"{position_exam_code}/{position_exam_year} position release"),
        )
        if (
            not isinstance(position["release_content_sha256"], str)
            or position["release_content_sha256"]
            != position["recomputed_content_sha256"]
        ):
            raise DataReleaseAuthorityError(
                "active position release content digest is missing or changed"
            )
        position_profile = "position-frozen-snapshots@v2"
        position_content_sha256 = str(position["release_content_sha256"])
    else:
        position = _one(
            _driver_rows(
                connection,
                _BOOTSTRAP_POSITION_RELEASE_SQL,
                {
                    "exam_code": position_exam_code,
                    "exam_year": position_exam_year,
                },
            ),
            authority=(f"{position_exam_code}/{position_exam_year} position release"),
        )
        business_windows = (
            _BOOTSTRAP_WINDOWS_CONTENT_SQL
            if capability["has_position_windows"]
            else "'[]'::jsonb"
        )
        content_rows = _driver_rows(
            connection,
            _BOOTSTRAP_POSITION_CONTENT_SQL.format(business_windows=business_windows),
            {"release_id": position["release_id"]},
        )
        if len(content_rows) != 1:
            raise DataReleaseAuthorityError(
                "active position bootstrap content is unavailable"
            )
        content = content_rows[0]
        if (
            content["position_count"] <= 0
            or content["compilation_count"] != content["position_count"]
            or not isinstance(content["release_content_sha256"], str)
            or SHA256.fullmatch(content["release_content_sha256"]) is None
        ):
            raise DataReleaseAuthorityError(
                "active position bootstrap content is incomplete"
            )
        position_profile = (
            "position-windows-pre-snapshots@v1"
            if capability["has_position_windows"]
            else "position-legacy-pre-windows@v1"
        )
        position_content_sha256 = str(content["release_content_sha256"])

    knowledge = _one(
        _driver_rows(
            connection,
            _KNOWLEDGE_RELEASE_SQL,
            {
                "exam_scope": knowledge_exam_scope,
                "track": knowledge_track,
            },
        ),
        authority=f"{knowledge_exam_scope}/{knowledge_track} knowledge release",
    )
    observed_at = identity["observed_at"]
    if (
        knowledge["node_count"] != knowledge["actual_node_count"]
        or knowledge["invalid_node_count"] != 0
        or knowledge["question_binding_count"] != knowledge["actual_binding_count"]
        or knowledge["question_binding_fingerprint"]
        != knowledge["actual_binding_fingerprint"]
        or knowledge["actual_binding_count"] <= 0
        or not isinstance(knowledge["release_content_sha256"], str)
        or knowledge["fresh_until"] <= observed_at
    ):
        raise DataReleaseAuthorityError(
            "active knowledge release content or freshness is invalid"
        )

    return {
        "schemaVersion": BOOTSTRAP_BASELINE_VERSION,
        "schemaProfile": position_profile,
        "observedAt": _iso(observed_at, label="database observation"),
        "environment": environment,
        "applicationCommit": application_commit,
        "database": {
            "databaseName": str(identity["database_name"]),
            "clusterSystemIdentifierSha256": sha256(
                str(identity["system_identifier"]).encode("utf-8")
            ).hexdigest(),
            "alembicRevision": expected_revision,
        },
        "position": {
            "releaseId": str(position["release_id"]),
            "examCode": str(position["exam_code"]),
            "examYear": int(position["exam_year"]),
            "ruleSetVersion": str(position["rule_set_version"]),
            "positionDataVersion": str(position["position_data_version"]),
            "releaseContentSha256": position_content_sha256,
        },
        "knowledge": {
            "releaseId": str(knowledge["release_id"]),
            "releaseVersion": str(knowledge["release_version"]),
            "examScope": knowledge_exam_scope,
            "track": knowledge_track,
            "sourceDataVersion": str(knowledge["source_data_version"]),
            "sourceFingerprint": str(knowledge["source_fingerprint"]),
            "releaseContentSha256": str(knowledge["release_content_sha256"]),
            "freshUntil": _iso(
                knowledge["fresh_until"],
                label="knowledge freshness",
            ),
        },
    }


def _validate_bootstrap_authority(
    authority: Any,
    *,
    environment: str,
    expected_revision: str,
    application_commit: str,
) -> None:
    if not isinstance(authority, dict) or set(authority) != {
        "schemaVersion",
        "schemaProfile",
        "observedAt",
        "environment",
        "applicationCommit",
        "database",
        "position",
        "knowledge",
    }:
        raise DataReleaseAuthorityError(
            "bootstrap baseline receipt authority is invalid"
        )
    if authority.get("schemaVersion") != BOOTSTRAP_BASELINE_VERSION:
        raise DataReleaseAuthorityError("bootstrap baseline receipt schema is invalid")
    if authority.get("schemaProfile") not in {
        "position-legacy-pre-windows@v1",
        "position-windows-pre-snapshots@v1",
        "position-frozen-snapshots@v2",
    }:
        raise DataReleaseAuthorityError("bootstrap baseline schema profile is invalid")
    if authority.get("environment") != environment:
        raise DataReleaseAuthorityError("bootstrap baseline environment does not match")
    if authority.get("applicationCommit") != application_commit:
        raise DataReleaseAuthorityError(
            "bootstrap baseline application commit does not match"
        )
    if COMMIT.fullmatch(application_commit) is None:
        raise DataReleaseAuthorityError("bootstrap application commit is invalid")
    try:
        observed_at = datetime.fromisoformat(authority["observedAt"])
    except (KeyError, TypeError, ValueError) as exc:
        raise DataReleaseAuthorityError(
            "bootstrap baseline timestamp is invalid"
        ) from exc
    if observed_at.tzinfo is None:
        raise DataReleaseAuthorityError(
            "bootstrap baseline timestamp must include a timezone"
        )
    database = authority.get("database")
    position = authority.get("position")
    knowledge = authority.get("knowledge")
    if (
        not isinstance(database, dict)
        or set(database)
        != {
            "databaseName",
            "clusterSystemIdentifierSha256",
            "alembicRevision",
        }
        or not isinstance(database.get("databaseName"), str)
        or not database["databaseName"]
        or database.get("alembicRevision") != expected_revision
        or not isinstance(database.get("clusterSystemIdentifierSha256"), str)
        or SHA256.fullmatch(database["clusterSystemIdentifierSha256"]) is None
    ):
        raise DataReleaseAuthorityError(
            "bootstrap baseline database authority is invalid"
        )
    if (
        not isinstance(position, dict)
        or set(position)
        != {
            "releaseId",
            "examCode",
            "examYear",
            "ruleSetVersion",
            "positionDataVersion",
            "releaseContentSha256",
        }
        or not isinstance(position.get("examCode"), str)
        or not position["examCode"]
        or isinstance(position.get("examYear"), bool)
        or not isinstance(position.get("examYear"), int)
        or not isinstance(position.get("ruleSetVersion"), str)
        or not position["ruleSetVersion"]
        or not isinstance(position.get("positionDataVersion"), str)
        or not position["positionDataVersion"]
        or not isinstance(position.get("releaseContentSha256"), str)
        or SHA256.fullmatch(position["releaseContentSha256"]) is None
    ):
        raise DataReleaseAuthorityError(
            "bootstrap baseline position authority is invalid"
        )
    if (
        not isinstance(knowledge, dict)
        or set(knowledge)
        != {
            "releaseId",
            "releaseVersion",
            "examScope",
            "track",
            "sourceDataVersion",
            "sourceFingerprint",
            "releaseContentSha256",
            "freshUntil",
        }
        or not isinstance(knowledge.get("releaseVersion"), str)
        or not knowledge["releaseVersion"]
        or not isinstance(knowledge.get("examScope"), str)
        or not knowledge["examScope"]
        or knowledge.get("track") not in {"xingce", "shenlun"}
        or not isinstance(knowledge.get("sourceDataVersion"), str)
        or not knowledge["sourceDataVersion"]
    ):
        raise DataReleaseAuthorityError(
            "bootstrap baseline knowledge authority is invalid"
        )
    for label, value in (
        ("knowledge source", knowledge.get("sourceFingerprint")),
        ("knowledge content", knowledge.get("releaseContentSha256")),
    ):
        if not isinstance(value, str) or SHA256.fullmatch(value) is None:
            raise DataReleaseAuthorityError(
                f"bootstrap baseline {label} digest is invalid"
            )
    try:
        UUID(str(position["releaseId"]))
        UUID(str(knowledge["releaseId"]))
        fresh_until = datetime.fromisoformat(knowledge["freshUntil"])
    except (KeyError, TypeError, ValueError) as exc:
        raise DataReleaseAuthorityError(
            "bootstrap baseline release identity is invalid"
        ) from exc
    if fresh_until.tzinfo is None or observed_at >= fresh_until:
        raise DataReleaseAuthorityError(
            "bootstrap baseline knowledge authority is stale"
        )


def capture_bootstrap_authority(
    *,
    database_url: str,
    environment: str,
    expected_revision: str,
    application_commit: str,
    position_exam_code: str,
    position_exam_year: int,
    knowledge_exam_scope: str,
    knowledge_track: str,
) -> dict[str, Any]:
    _validate_database_url(database_url)
    if COMMIT.fullmatch(application_commit) is None:
        raise DataReleaseAuthorityError("bootstrap application commit is invalid")
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        connection.execute(
            "begin transaction isolation level repeatable read read only"
        )
        try:
            authority = _capture_bootstrap_snapshot(
                connection,
                environment=environment,
                expected_revision=expected_revision,
                application_commit=application_commit,
                position_exam_code=position_exam_code,
                position_exam_year=position_exam_year,
                knowledge_exam_scope=knowledge_exam_scope,
                knowledge_track=knowledge_track,
            )
        finally:
            connection.rollback()
    _validate_bootstrap_authority(
        authority,
        environment=environment,
        expected_revision=expected_revision,
        application_commit=application_commit,
    )
    return authority


def verify_bootstrap_authority_payload(
    connection: Any,
    *,
    expected: dict[str, Any],
    environment: str,
    expected_revision: str,
    application_commit: str,
) -> dict[str, Any]:
    """Verify one parsed bootstrap receipt on the caller's transaction."""

    _validate_bootstrap_authority(
        expected,
        environment=environment,
        expected_revision=expected_revision,
        application_commit=application_commit,
    )
    actual = _capture_bootstrap_snapshot(
        connection,
        environment=environment,
        expected_revision=expected_revision,
        application_commit=application_commit,
        position_exam_code=str(expected["position"]["examCode"]),
        position_exam_year=int(expected["position"]["examYear"]),
        knowledge_exam_scope=str(expected["knowledge"]["examScope"]),
        knowledge_track=str(expected["knowledge"]["track"]),
    )
    for section in (
        "schemaProfile",
        "database",
        "position",
        "knowledge",
    ):
        if actual[section] != expected[section]:
            raise DataReleaseAuthorityError(
                f"live bootstrap {section} does not match the signed receipt"
            )
    return {
        "schemaVersion": BOOTSTRAP_BASELINE_VERSION,
        "verifiedAt": actual["observedAt"],
        "environment": environment,
        "applicationCommit": application_commit,
        "database": actual["database"],
        "positionReleaseId": actual["position"]["releaseId"],
        "knowledgeReleaseId": actual["knowledge"]["releaseId"],
    }


def verify_bootstrap_authority(
    *,
    database_url: str,
    receipt_path: Path,
    environment: str,
    expected_revision: str,
    application_commit: str,
) -> dict[str, Any]:
    _validate_database_url(database_url)
    try:
        expected = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DataReleaseAuthorityError(
            "bootstrap baseline receipt is unreadable"
        ) from exc
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        connection.execute(
            "begin transaction isolation level repeatable read read only"
        )
        try:
            result = verify_bootstrap_authority_payload(
                connection,
                expected=expected,
                environment=environment,
                expected_revision=expected_revision,
                application_commit=application_commit,
            )
        finally:
            connection.rollback()
    return result


def _capture_snapshot(
    connection: psycopg.Connection[dict[str, Any]],
    *,
    environment: str,
    expected_revision: str,
    compatible_commits: tuple[str, ...],
    position_exam_code: str,
    position_exam_year: int,
    knowledge_exam_scope: str,
    knowledge_track: str,
) -> dict[str, Any]:
    database_identity = connection.execute(
        """
        select
          current_database() as database_name,
          (
            select system_identifier::text
            from pg_control_system()
          ) as system_identifier,
          transaction_timestamp() as observed_at
        """
    ).fetchone()
    if database_identity is None:
        raise DataReleaseAuthorityError("database identity query returned no row")
    revisions = [
        str(row["version_num"])
        for row in connection.execute(
            "select version_num from public.alembic_version order by version_num"
        ).fetchall()
    ]
    if revisions != [expected_revision]:
        raise DataReleaseAuthorityError(
            f"database revision must be exactly {expected_revision}; got {revisions}"
        )

    position = _one(
        list(
            connection.execute(
                _POSITION_RELEASE_SQL,
                {
                    "exam_code": position_exam_code,
                    "exam_year": position_exam_year,
                },
            ).fetchall()
        ),
        authority=f"{position_exam_code}/{position_exam_year} position release",
    )
    if (
        not isinstance(position["release_content_sha256"], str)
        or position["release_content_sha256"] != position["recomputed_content_sha256"]
    ):
        raise DataReleaseAuthorityError(
            "active position release content digest is missing or changed"
        )
    current_windows = list(
        connection.execute(
            _POSITION_WINDOW_SQL,
            {"release_id": position["release_id"]},
        ).fetchall()
    )
    if len(current_windows) > 1:
        raise DataReleaseAuthorityError(
            "expected at most one current position business window; "
            f"found {len(current_windows)}"
        )
    window = current_windows[0] if current_windows else None

    knowledge = _one(
        list(
            connection.execute(
                _KNOWLEDGE_RELEASE_SQL,
                {
                    "exam_scope": knowledge_exam_scope,
                    "track": knowledge_track,
                },
            ).fetchall()
        ),
        authority=f"{knowledge_exam_scope}/{knowledge_track} knowledge release",
    )
    observed_at = database_identity["observed_at"]
    if (
        knowledge["node_count"] != knowledge["actual_node_count"]
        or knowledge["invalid_node_count"] != 0
        or knowledge["question_binding_count"] != knowledge["actual_binding_count"]
        or knowledge["question_binding_fingerprint"]
        != knowledge["actual_binding_fingerprint"]
        or knowledge["actual_binding_count"] <= 0
        or not isinstance(knowledge["release_content_sha256"], str)
        or knowledge["fresh_until"] <= observed_at
    ):
        raise DataReleaseAuthorityError(
            "active knowledge release content or freshness is invalid"
        )

    return {
        "schemaVersion": DATA_RELEASE_RECEIPT_VERSION,
        "observedAt": _iso(observed_at, label="database observation"),
        "environment": environment,
        "database": {
            "databaseName": str(database_identity["database_name"]),
            "clusterSystemIdentifierSha256": sha256(
                str(database_identity["system_identifier"]).encode("utf-8")
            ).hexdigest(),
            "alembicRevision": expected_revision,
        },
        "position": {
            "releaseId": str(position["release_id"]),
            "examCode": str(position["exam_code"]),
            "examYear": int(position["exam_year"]),
            "ruleSetVersion": str(position["rule_set_version"]),
            "positionDataVersion": str(position["position_data_version"]),
            "sourceSnapshotSha256": str(position["source_snapshot_sha256"]),
            "releaseContentSha256": str(position["release_content_sha256"]),
            "lifecycleState": "open" if window is not None else "closed",
            "currentBusinessWindow": (
                {
                    "windowId": str(window["window_id"]),
                    "kind": str(window["kind"]),
                    "sequenceNo": int(window["sequence_no"]),
                    "opensAt": _iso(
                        window["opens_at"],
                        label="window open",
                    ),
                    "closesAt": _iso(
                        window["closes_at"],
                        label="window close",
                    ),
                    "officialSourceSha256": str(window["official_source_sha256"]),
                }
                if window is not None
                else None
            ),
        },
        "knowledge": {
            "releaseId": str(knowledge["release_id"]),
            "releaseVersion": str(knowledge["release_version"]),
            "sourceDataVersion": str(knowledge["source_data_version"]),
            "sourceFingerprint": str(knowledge["source_fingerprint"]),
            "releaseContentSha256": str(knowledge["release_content_sha256"]),
            "freshUntil": _iso(
                knowledge["fresh_until"],
                label="knowledge freshness",
            ),
        },
        "compatibleApplicationCommits": list(compatible_commits),
    }


def capture_authority(
    *,
    database_url: str,
    environment: str,
    expected_revision: str,
    compatible_commits: tuple[str, ...],
    position_exam_code: str,
    position_exam_year: int,
    knowledge_exam_scope: str,
    knowledge_track: str,
) -> dict[str, Any]:
    _validate_database_url(database_url)
    _validate_commits(compatible_commits)
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        connection.execute(
            "begin transaction isolation level repeatable read read only"
        )
        try:
            authority = _capture_snapshot(
                connection,
                environment=environment,
                expected_revision=expected_revision,
                compatible_commits=compatible_commits,
                position_exam_code=position_exam_code,
                position_exam_year=position_exam_year,
                knowledge_exam_scope=knowledge_exam_scope,
                knowledge_track=knowledge_track,
            )
        finally:
            connection.rollback()
    for commit in compatible_commits:
        _validate_data_release_receipt(
            authority,
            source_commit=commit,
            deployment_environment=environment,
        )
    return authority


def verify_authority(
    *,
    database_url: str,
    receipt_path: Path,
    environment: str,
    expected_revision: str,
    expected_compatible_commits: tuple[str, ...],
) -> dict[str, Any]:
    _validate_database_url(database_url)
    _validate_commits(expected_compatible_commits)
    try:
        expected = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DataReleaseAuthorityError("data release receipt is unreadable") from exc
    for commit in expected_compatible_commits:
        try:
            _validate_data_release_receipt(
                expected,
                source_commit=commit,
                deployment_environment=environment,
            )
        except RuntimeError as exc:
            raise DataReleaseAuthorityError(str(exc)) from exc
    if expected["database"]["alembicRevision"] != expected_revision:
        raise DataReleaseAuthorityError(
            "data receipt database revision does not match the artifact"
        )
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        connection.execute(
            "begin transaction isolation level repeatable read read only"
        )
        try:
            actual = _capture_snapshot(
                connection,
                environment=environment,
                expected_revision=expected_revision,
                compatible_commits=tuple(expected["compatibleApplicationCommits"]),
                position_exam_code=str(expected["position"]["examCode"]),
                position_exam_year=int(expected["position"]["examYear"]),
                knowledge_exam_scope="guokao",
                knowledge_track="xingce",
            )
        finally:
            connection.rollback()
    for section in ("database", "position", "knowledge"):
        if actual[section] != expected[section]:
            raise DataReleaseAuthorityError(
                f"active {section} authority does not match the signed receipt"
            )
    return {
        "schemaVersion": DATA_RELEASE_RECEIPT_VERSION,
        "verifiedAt": actual["observedAt"],
        "environment": environment,
        "database": actual["database"],
        "positionReleaseId": actual["position"]["releaseId"],
        "knowledgeReleaseId": actual["knowledge"]["releaseId"],
        "compatibleApplicationCommits": list(expected_compatible_commits),
    }


def _validate_commits(commits: tuple[str, ...]) -> None:
    if (
        len(commits) < 2
        or len(set(commits)) != len(commits)
        or not all(COMMIT.fullmatch(value) for value in commits)
    ):
        raise DataReleaseAuthorityError(
            "at least two unique full compatible commits are required"
        )


def _write_new_json(path: Path, payload: dict[str, Any]) -> None:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise DataReleaseAuthorityError(f"refusing to overwrite data receipt {path}")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        encoded = (
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        if path.exists():
            raise DataReleaseAuthorityError(
                f"refusing to overwrite data receipt {path}"
            )
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url-env",
        default="MIGRATION_DATABASE_URL",
    )
    parser.add_argument("--environment", required=True)
    parser.add_argument("--expected-revision", required=True)
    parser.add_argument(
        "--compatible-commit",
        action="append",
        default=[],
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    capture = subparsers.add_parser("capture")
    capture.add_argument("--output", type=Path, required=True)
    capture.add_argument("--position-exam-code", default="guokao")
    capture.add_argument("--position-exam-year", type=int, required=True)
    capture.add_argument("--knowledge-exam-scope", default="guokao")
    capture.add_argument("--knowledge-track", default="xingce")

    verify = subparsers.add_parser("verify")
    verify.add_argument("--receipt", type=Path, required=True)
    bootstrap_capture = subparsers.add_parser("bootstrap-capture")
    bootstrap_capture.add_argument("--output", type=Path, required=True)
    bootstrap_capture.add_argument("--application-commit", required=True)
    bootstrap_capture.add_argument(
        "--position-exam-code",
        default="guokao",
    )
    bootstrap_capture.add_argument(
        "--position-exam-year",
        type=int,
        required=True,
    )
    bootstrap_capture.add_argument(
        "--knowledge-exam-scope",
        default="guokao",
    )
    bootstrap_capture.add_argument("--knowledge-track", default="xingce")
    bootstrap_verify = subparsers.add_parser("bootstrap-verify")
    bootstrap_verify.add_argument("--receipt", type=Path, required=True)
    bootstrap_verify.add_argument("--application-commit", required=True)
    subparsers.add_parser("validate-connection")
    return parser


def main() -> None:
    arguments = _parser().parse_args()
    try:
        database_url = _database_url(arguments.database_url_env)
        commits = tuple(arguments.compatible_commit)
        if arguments.command in {"bootstrap-capture", "bootstrap-verify"}:
            if commits:
                raise DataReleaseAuthorityError(
                    "bootstrap authority accepts one application commit, "
                    "not compatible commits"
                )
        else:
            _validate_commits(commits)
        if arguments.command == "validate-connection":
            result: dict[str, Any] = {
                "databaseAuthority": "validated",
                "databaseUrlEnvironment": arguments.database_url_env,
            }
        elif arguments.command == "capture":
            authority = capture_authority(
                database_url=database_url,
                environment=arguments.environment,
                expected_revision=arguments.expected_revision,
                compatible_commits=commits,
                position_exam_code=arguments.position_exam_code,
                position_exam_year=arguments.position_exam_year,
                knowledge_exam_scope=arguments.knowledge_exam_scope,
                knowledge_track=arguments.knowledge_track,
            )
            _write_new_json(arguments.output, authority)
            result = {
                "receipt": str(arguments.output.resolve()),
                "receiptSha256": sha256(
                    arguments.output.resolve().read_bytes()
                ).hexdigest(),
                "positionReleaseId": authority["position"]["releaseId"],
                "knowledgeReleaseId": authority["knowledge"]["releaseId"],
            }
        elif arguments.command == "verify":
            result = verify_authority(
                database_url=database_url,
                receipt_path=arguments.receipt,
                environment=arguments.environment,
                expected_revision=arguments.expected_revision,
                expected_compatible_commits=commits,
            )
        elif arguments.command == "bootstrap-capture":
            bootstrap = capture_bootstrap_authority(
                database_url=database_url,
                environment=arguments.environment,
                expected_revision=arguments.expected_revision,
                application_commit=arguments.application_commit,
                position_exam_code=arguments.position_exam_code,
                position_exam_year=arguments.position_exam_year,
                knowledge_exam_scope=arguments.knowledge_exam_scope,
                knowledge_track=arguments.knowledge_track,
            )
            _write_new_json(arguments.output, bootstrap)
            result = {
                "receipt": str(arguments.output.resolve()),
                "receiptSha256": sha256(
                    arguments.output.resolve().read_bytes()
                ).hexdigest(),
                "positionReleaseId": bootstrap["position"]["releaseId"],
                "knowledgeReleaseId": bootstrap["knowledge"]["releaseId"],
            }
        else:
            result = verify_bootstrap_authority(
                database_url=database_url,
                receipt_path=arguments.receipt,
                environment=arguments.environment,
                expected_revision=arguments.expected_revision,
                application_commit=arguments.application_commit,
            )
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    except (DataReleaseAuthorityError, psycopg.Error) as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    main()
