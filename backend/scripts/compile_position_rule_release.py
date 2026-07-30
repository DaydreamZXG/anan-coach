from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime
import fcntl
import json
import os
from pathlib import Path
import re
import stat
import sys

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.domains.qualification.rule_releases import (
    PositionReleaseWindowInput,
    PositionRuleReleaseService,
    RuleReleaseContractError,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compile an immutable, auditable position-rule release."
    )
    parser.add_argument("--exam-code", default="guokao")
    parser.add_argument("--exam-year", type=int, required=True)
    parser.add_argument("--rule-set-version", required=True)
    parser.add_argument("--position-data-version", required=True)
    parser.add_argument(
        "--activate",
        action="store_true",
        help="Activate only after every position passes compilation audit.",
    )
    parser.add_argument(
        "--window-manifest",
        type=Path,
        help=(
            "JSON manifest containing the audited primary and optional supplemental "
            "business windows with complete official-source receipts."
        ),
    )
    parser.add_argument(
        "--adopt-legacy-unwindowed-active",
        action="store_true",
        help=(
            "Atomically adopt the exact pre-migration active release, but only when "
            "it has no window rows yet."
        ),
    )
    args = parser.parse_args()
    if args.activate and args.window_manifest is None:
        parser.error("--activate requires --window-manifest")
    if args.activate:
        try:
            _validate_data_release_coordination_lock()
        except (OSError, ValueError) as exc:
            parser.error(f"--activate requires the data publication lock: {exc}")
    try:
        windows = (
            _load_window_manifest(args.window_manifest)
            if args.window_manifest is not None
            else None
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(f"invalid --window-manifest: {exc}")

    settings = get_settings()
    if not settings.database_url:
        parser.error("DATABASE_URL is required")
    engine = create_engine(settings.database_url, pool_pre_ping=True, future=True)
    service = PositionRuleReleaseService()
    with Session(engine) as session, session.begin():
        release_id = service.ensure_draft(
            session,
            exam_code=args.exam_code,
            exam_year=args.exam_year,
            rule_set_version=args.rule_set_version,
            position_data_version=args.position_data_version,
        )
        requires_activation = service.prepare_window_activation(
            session,
            release_id=release_id,
            expected_windows=windows or [],
            allow_legacy_unwindowed_active=args.adopt_legacy_unwindowed_active,
        )
        if windows is not None and requires_activation:
            service.configure_windows(
                session,
                release_id=release_id,
                windows=windows,
            )
        summary = (
            service.compile_draft(session, release_id=release_id) if requires_activation else None
        )
        try:
            if args.activate and requires_activation:
                service.activate(session, release_id=release_id)
        except RuleReleaseContractError as exc:
            parser.error(str(exc))

    print(
        json.dumps(
            {
                "releaseId": release_id,
                "activated": args.activate,
                "windowCount": len(windows or []),
                "summary": asdict(summary) if summary is not None else None,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


def _validate_data_release_coordination_lock() -> None:
    if os.environ.get("DATA_RELEASE_COORDINATION_LOCK_HELD") != "1":
        raise ValueError("DATA_RELEASE_COORDINATION_LOCK_HELD must equal 1")
    raw_path = os.environ.get("DATA_RELEASE_COORDINATION_LOCK_PATH", "")
    if re.fullmatch(r"/[A-Za-z0-9._/-]+", raw_path) is None:
        raise ValueError("DATA_RELEASE_COORDINATION_LOCK_PATH must be a safe absolute path")

    lock_path = Path(raw_path)
    path_stat = lock_path.lstat()
    if stat.S_ISLNK(path_stat.st_mode) or not stat.S_ISREG(path_stat.st_mode):
        raise ValueError("the coordination lock must be a regular non-symlink file")
    if path_stat.st_uid != 0 or stat.S_IMODE(path_stat.st_mode) != 0o600:
        raise ValueError("the coordination lock must be root-owned with mode 0600")
    fd_stat = os.fstat(9)
    if not stat.S_ISREG(fd_stat.st_mode) or (
        fd_stat.st_dev,
        fd_stat.st_ino,
    ) != (
        path_stat.st_dev,
        path_stat.st_ino,
    ):
        raise ValueError("file descriptor 9 does not identify the coordination lock")

    resolved_path = lock_path.resolve(strict=True)
    if sys.platform.startswith("linux"):
        proc_fd = Path("/proc/self/fd/9")
        if not proc_fd.exists() or proc_fd.resolve(strict=True) != resolved_path:
            raise ValueError("/proc/self/fd/9 does not identify the coordination lock")

    probe_flags = os.O_RDWR
    if hasattr(os, "O_CLOEXEC"):
        probe_flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        probe_flags |= os.O_NOFOLLOW
    probe_fd = os.open(resolved_path, probe_flags)
    try:
        probe_stat = os.fstat(probe_fd)
        if (probe_stat.st_dev, probe_stat.st_ino) != (
            path_stat.st_dev,
            path_stat.st_ino,
        ):
            raise ValueError("the coordination lock changed during validation")
        try:
            fcntl.flock(probe_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            pass
        else:
            fcntl.flock(probe_fd, fcntl.LOCK_UN)
            raise ValueError(
                "the coordination lock was not already held by the inherited descriptor"
            )
        try:
            fcntl.flock(9, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("file descriptor 9 does not own the coordination lock") from exc
    finally:
        os.close(probe_fd)


def _load_window_manifest(path: Path) -> list[PositionReleaseWindowInput]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or set(payload) != {"windows"}:
        raise ValueError("manifest must contain exactly one 'windows' array")
    raw_windows = payload["windows"]
    if not isinstance(raw_windows, list):
        raise ValueError("'windows' must be an array")
    expected_keys = {
        "kind",
        "sequenceNo",
        "opensAt",
        "closesAt",
        "officialSourceUrl",
        "officialSourceTitle",
        "officialSourcePublishedAt",
        "officialSourceReceivedAt",
        "officialSourceSha256",
        "officialSourceReceipt",
    }
    windows: list[PositionReleaseWindowInput] = []
    for index, item in enumerate(raw_windows):
        if not isinstance(item, dict) or set(item) != expected_keys:
            raise ValueError(f"windows[{index}] must contain exactly {sorted(expected_keys)}")
        kind = item["kind"]
        sequence_no = item["sequenceNo"]
        if kind not in {"primary", "supplemental"}:
            raise ValueError(f"windows[{index}].kind is invalid")
        if isinstance(sequence_no, bool) or not isinstance(sequence_no, int):
            raise ValueError(f"windows[{index}].sequenceNo must be an integer")
        string_values = {key: item[key] for key in expected_keys if key not in {"sequenceNo"}}
        if not all(isinstance(value, str) for value in string_values.values()):
            raise ValueError(f"windows[{index}] string fields must be strings")
        windows.append(
            PositionReleaseWindowInput(
                kind=kind,
                sequence_no=sequence_no,
                opens_at=datetime.fromisoformat(str(item["opensAt"])),
                closes_at=datetime.fromisoformat(str(item["closesAt"])),
                official_source_url=str(item["officialSourceUrl"]),
                official_source_title=str(item["officialSourceTitle"]),
                official_source_published_at=datetime.fromisoformat(
                    str(item["officialSourcePublishedAt"])
                ),
                official_source_received_at=datetime.fromisoformat(
                    str(item["officialSourceReceivedAt"])
                ),
                official_source_sha256=str(item["officialSourceSha256"]),
                official_source_receipt=str(item["officialSourceReceipt"]),
            )
        )
    return windows


if __name__ == "__main__":
    main()
