#!/usr/bin/env python3
from __future__ import annotations

import argparse
from getpass import getpass
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

from app.domains.ilink_channel.operator_login import (  # noqa: E402
    ILinkOperatorLoginClient,
    ILinkOperatorLoginError,
    write_credential_file,
)


DEFAULT_OUTPUT = Path.home() / ".anan/credentials/ilink.json"


def _verify_code() -> str:
    return getpass("输入手机微信显示的数字：")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Provision Anan's Tencent iLink credential without OpenClaw."
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--timeout-seconds", type=int, default=480)
    args = parser.parse_args()
    try:
        if args.output.expanduser().exists() or args.output.expanduser().is_symlink():
            raise ILinkOperatorLoginError("credential_output_exists")
        print("正在向腾讯 iLink 申请一次性二维码……")
        credential = ILinkOperatorLoginClient().provision(
            timeout_seconds=args.timeout_seconds,
            request_verify_code=_verify_code,
        )
        output = write_credential_file(
            credential,
            args.output,
            repository_root=PROJECT_ROOT,
        )
    except ILinkOperatorLoginError as exc:
        print(f"取证失败：{exc.code}", file=sys.stderr)
        return 1
    print(f"凭据已安全写入：{output}")
    print("请由密钥管理流程导入；不要打开、复制或提交该文件。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
