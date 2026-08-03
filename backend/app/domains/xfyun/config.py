from __future__ import annotations

from dataclasses import dataclass
import os


class XfyunConfigurationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class XfyunCredentials:
    app_id: str
    api_key: str
    api_secret: str


def credentials_from_environment() -> XfyunCredentials:
    values = {
        "app_id": os.environ.get("XFYUN_APP_ID", "").strip(),
        "api_key": os.environ.get("XFYUN_API_KEY", "").strip(),
        "api_secret": os.environ.get("XFYUN_API_SECRET", "").strip(),
    }
    if not all(values.values()):
        raise XfyunConfigurationError("讯飞能力尚未配置，请稍后再试。")
    return XfyunCredentials(**values)


def optional_credentials_from_environment() -> XfyunCredentials | None:
    try:
        return credentials_from_environment()
    except XfyunConfigurationError:
        return None
