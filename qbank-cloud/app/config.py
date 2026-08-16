from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    database_path: str = ":memory:"
    catalog_mode: str = "demo"
    api_key_secret: str = "development-only-secret-change-before-deploy"
    rate_limit_per_minute: int = 60
    skillhub_developer_id: str | None = None
    skillhub_public_key_id: str | None = None
    skillhub_private_key_path: str | None = None

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            database_path=os.getenv("QBANK_DATABASE_PATH", ":memory:"),
            catalog_mode=os.getenv("QBANK_CATALOG_MODE", "demo"),
            api_key_secret=os.getenv(
                "QBANK_API_KEY_SECRET",
                "development-only-secret-change-before-deploy",
            ),
            rate_limit_per_minute=int(os.getenv("QBANK_RATE_LIMIT_PER_MINUTE", "60")),
            skillhub_developer_id=os.getenv("SKILLHUB_DEVELOPER_ID"),
            skillhub_public_key_id=os.getenv("SKILLHUB_PUBLIC_KEY_ID"),
            skillhub_private_key_path=os.getenv("SKILLHUB_PRIVATE_KEY_PATH"),
        )

    def private_key_pem(self) -> bytes | None:
        if not self.skillhub_private_key_path:
            return None
        return Path(self.skillhub_private_key_path).read_bytes()
