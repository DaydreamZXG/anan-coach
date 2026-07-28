from __future__ import annotations

from argparse import ArgumentParser
from dataclasses import dataclass
from functools import lru_cache
import os
from pathlib import Path
from typing import Sequence

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text


ARTIFACT_DATABASE_COMPATIBILITY_CONTRACT = "artifact-db-compatibility@v1"


class DatabaseCompatibilityError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ArtifactDatabaseCompatibility:
    migration_head: str
    compatible_revisions: frozenset[str]


def _default_backend_root() -> Path:
    source_root = Path(__file__).resolve().parents[2]
    working_root = Path.cwd().resolve()
    for candidate in (source_root, working_root, working_root / "backend"):
        if (candidate / "alembic.ini").is_file() and (candidate / "migrations").is_dir():
            return candidate
    raise DatabaseCompatibilityError(
        "artifact backend root containing alembic.ini and migrations is unavailable"
    )


@lru_cache(maxsize=1)
def artifact_database_compatibility() -> ArtifactDatabaseCompatibility:
    backend_root = _default_backend_root()
    return load_artifact_database_compatibility(
        backend_root.parent / "ops" / "release-db-compatibility.env",
        backend_root=backend_root,
    )


@lru_cache(maxsize=1)
def artifact_database_revision() -> str:
    return artifact_database_compatibility().migration_head


@lru_cache(maxsize=1)
def artifact_database_compatible_revisions() -> frozenset[str]:
    return artifact_database_compatibility().compatible_revisions


def database_revision_for_backend(backend_root: Path) -> str:
    config_path = backend_root / "alembic.ini"
    if not config_path.is_file():
        raise DatabaseCompatibilityError(
            f"artifact Alembic configuration is missing: {config_path}"
        )
    script = ScriptDirectory.from_config(Config(str(config_path)))
    heads = tuple(script.get_heads())
    if len(heads) != 1:
        rendered = ", ".join(heads) if heads else "none"
        raise DatabaseCompatibilityError(
            f"release artifact must contain exactly one Alembic head; found {rendered}"
        )
    return heads[0]


def load_artifact_database_compatibility(
    contract_path: Path,
    *,
    backend_root: Path | None = None,
) -> ArtifactDatabaseCompatibility:
    values = _read_contract(contract_path)
    contract = values.get("ARTIFACT_DATABASE_COMPATIBILITY_CONTRACT")
    if contract != ARTIFACT_DATABASE_COMPATIBILITY_CONTRACT:
        raise DatabaseCompatibilityError(
            "artifact database compatibility contract is missing or unsupported"
        )

    graph_head = database_revision_for_backend(
        backend_root if backend_root is not None else _default_backend_root()
    )
    declared_head = values.get("ARTIFACT_DATABASE_MIGRATION_HEAD", "")
    if declared_head != graph_head:
        raise DatabaseCompatibilityError(
            "artifact database metadata does not match its single Alembic head"
        )

    compatible_revisions = frozenset(
        revision.strip()
        for revision in values.get("ARTIFACT_DATABASE_COMPATIBLE_REVISIONS", "").split(",")
        if revision.strip()
    )
    if graph_head not in compatible_revisions:
        raise DatabaseCompatibilityError(
            "artifact database compatibility set must include its Alembic head"
        )
    return ArtifactDatabaseCompatibility(
        migration_head=graph_head,
        compatible_revisions=compatible_revisions,
    )


def current_database_revisions(database_url: str) -> tuple[str, ...]:
    if not database_url:
        raise DatabaseCompatibilityError("DATABASE_URL is required")
    engine = create_engine(database_url, pool_pre_ping=True, future=True)
    try:
        with engine.connect() as connection:
            revisions = tuple(
                str(value)
                for value in connection.execute(
                    text("select version_num from alembic_version order by version_num")
                ).scalars()
            )
    finally:
        engine.dispose()
    if len(revisions) != 1:
        rendered = ", ".join(revisions) if revisions else "none"
        raise DatabaseCompatibilityError(
            f"database must report exactly one Alembic revision; found {rendered}"
        )
    return revisions


def assert_database_is_compatible(
    compatibility: ArtifactDatabaseCompatibility,
    *,
    database_url: str,
) -> str:
    revision = current_database_revisions(database_url)[0]
    if revision not in compatibility.compatible_revisions:
        raise DatabaseCompatibilityError(
            f"database revision {revision} is not compatible with artifact head "
            f"{compatibility.migration_head}"
        )
    return revision


def assert_revision_is_compatible(
    compatibility: ArtifactDatabaseCompatibility,
    *,
    revision: str,
) -> str:
    if not revision:
        raise DatabaseCompatibilityError("database revision is required")
    if revision not in compatibility.compatible_revisions:
        raise DatabaseCompatibilityError(
            f"database revision {revision} is not compatible with artifact head "
            f"{compatibility.migration_head}"
        )
    return revision


def _read_contract(path: Path) -> dict[str, str]:
    if not path.is_file():
        raise DatabaseCompatibilityError(
            f"artifact database compatibility metadata is missing: {path}"
        )
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or not key:
            raise DatabaseCompatibilityError(
                f"invalid artifact database compatibility metadata line: {raw_line}"
            )
        values[key] = value
    return values


def _parser() -> ArgumentParser:
    parser = ArgumentParser()
    parser.add_argument(
        "command",
        choices=(
            "head",
            "compatible-revisions",
            "assert-current",
            "assert-revision",
        ),
    )
    parser.add_argument("--backend-root", type=Path)
    parser.add_argument("--contract", type=Path)
    parser.add_argument("--revision")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    backend_root = args.backend_root or _default_backend_root()
    if args.command == "head":
        print(database_revision_for_backend(backend_root))
        return 0
    if args.contract is None:
        raise DatabaseCompatibilityError("--contract is required")
    compatibility = load_artifact_database_compatibility(
        args.contract,
        backend_root=backend_root,
    )
    if args.command == "compatible-revisions":
        print(",".join(sorted(compatibility.compatible_revisions)))
        return 0
    if args.command == "assert-revision":
        print(
            assert_revision_is_compatible(
                compatibility,
                revision=args.revision or "",
            )
        )
        return 0
    revision = assert_database_is_compatible(
        compatibility,
        database_url=os.getenv("DATABASE_URL", ""),
    )
    print(revision)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
