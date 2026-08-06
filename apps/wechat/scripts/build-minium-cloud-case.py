#!/usr/bin/env python3
"""把 Minium 自定义测试打成微信云测可直接上传的 ZIP。"""

from __future__ import annotations

import argparse
import ast
from pathlib import Path
import zipfile


WECHAT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASE_DIR = WECHAT_ROOT / "minitest" / "cases"
DEFAULT_OUTPUT = WECHAT_ROOT / "minitest" / "dist" / "anan-minium-cloud-tests.zip"


def discover_test_methods(source: str, filename: str) -> list[str]:
    tree = ast.parse(source, filename=filename)
    methods: list[str] = []
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        for member in node.body:
            if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)) and member.name.startswith(
                "test"
            ):
                methods.append(f"{node.name}.{member.name}")
    return methods


def build_archive(case_dir: Path, output: Path) -> tuple[list[Path], list[str]]:
    case_files = sorted(case_dir.glob("test*.py"))
    if not case_files:
        raise SystemExit(f"没有找到 Minium 用例：{case_dir}")

    discovered: list[str] = []
    for case_file in case_files:
        source = case_file.read_text(encoding="utf-8")
        compile(source, str(case_file), "exec")
        discovered.extend(discover_test_methods(source, str(case_file)))
    if not discovered:
        raise SystemExit("用例文件中没有找到以 test 开头的测试方法。")

    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for case_file in case_files:
            # 云测要求直接压缩用例内容，不能再套一层 cases 目录。
            archive.write(case_file, arcname=case_file.name)

    return case_files, discovered


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case-dir", type=Path, default=DEFAULT_CASE_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    case_files, discovered = build_archive(args.case_dir.resolve(), args.output.resolve())
    print(f"Minium 云测包：{args.output.resolve()}")
    print(f"用例文件：{len(case_files)}，测试方法：{len(discovered)}")
    for method in discovered:
        print(f"- {method}")


if __name__ == "__main__":
    main()
