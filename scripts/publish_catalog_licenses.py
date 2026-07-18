from __future__ import annotations

import argparse
from pathlib import Path

from app.db.session import get_session_factory
from app.domains.content_catalog.licensing.publication import (
    CatalogLicensePublicationService,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "ops/content-licenses/owner-question-bank-v1.json"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Publish immutable owner-catalog content licenses"
    )
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--batch-size", type=int, default=500)
    args = parser.parse_args()
    with get_session_factory()() as session:
        result = CatalogLicensePublicationService(session=session).publish(
            manifest_path=args.manifest.resolve(),
            apply=args.apply,
            batch_size=args.batch_size,
        )
        print(result)


if __name__ == "__main__":
    main()
