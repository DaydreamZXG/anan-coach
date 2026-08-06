#!/usr/bin/env python3
"""把 Minium 自定义测试打成微信云测可直接上传的 ZIP。"""

from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
import runpy
import zipfile


WECHAT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASE_DIR = WECHAT_ROOT / "minitest" / "cases"
DEFAULT_OUTPUT = WECHAT_ROOT / "minitest" / "dist" / "anan-minium-full-suite.zip"


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


def registered_app_routes() -> tuple[str, ...]:
    app = json.loads((WECHAT_ROOT / "miniprogram" / "app.json").read_text(encoding="utf-8"))
    routes = ["/" + route for route in app.get("pages", [])]
    for package in app.get("subPackages", []):
        root = str(package["root"]).rstrip("/")
        routes.extend("/%s/%s" % (root, route) for route in package.get("pages", []))
    return tuple(routes)


def validate_route_inventory(case_dir: Path) -> None:
    support = runpy.run_path(str(case_dir / "minium_support.py"))
    case_routes = tuple(support.get("REGISTERED_ROUTES") or ())
    app_routes = registered_app_routes()
    if case_routes != app_routes:
        missing = sorted(set(app_routes) - set(case_routes))
        extra = sorted(set(case_routes) - set(app_routes))
        raise SystemExit(
            "Minium 路由清单与 app.json 不一致。缺少=%s，多余=%s" % (missing, extra)
        )


def generated_route_methods(case_dir: Path) -> list[str]:
    """与云测 inspect 一致，计入动态挂到测试类上的 60 条路由方法。"""

    route_case = case_dir / "test_full_route_smoke.py"
    if not route_case.is_file():
        return []
    support = runpy.run_path(str(case_dir / "minium_support.py"))
    route_name = support["route_test_name"]
    return [
        "BFullRouteRenderSmokeTest.%s" % route_name(route)
        for route in support["REGISTERED_ROUTES"]
    ]


def build_archive(case_dir: Path, output: Path) -> tuple[list[Path], list[Path], list[str]]:
    case_files = sorted(case_dir.glob("test*.py"))
    if not case_files:
        raise SystemExit(f"没有找到 Minium 用例：{case_dir}")
    package_files = sorted(case_dir.glob("*.py"))
    validate_route_inventory(case_dir)

    discovered: list[str] = []
    for case_file in case_files:
        source = case_file.read_text(encoding="utf-8")
        compile(source, str(case_file), "exec")
        discovered.extend(discover_test_methods(source, str(case_file)))
    discovered.extend(generated_route_methods(case_dir))
    if not discovered:
        raise SystemExit("用例文件中没有找到以 test 开头的测试方法。")

    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for case_file in package_files:
            # 云测要求直接压缩用例内容，不能再套一层 cases 目录。
            archive.write(case_file, arcname=case_file.name)

    return package_files, case_files, discovered


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case-dir", type=Path, default=DEFAULT_CASE_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    package_files, case_files, discovered = build_archive(
        args.case_dir.resolve(), args.output.resolve()
    )
    print(f"Minium 云测包：{args.output.resolve()}")
    print(
        f"包内 Python 文件：{len(package_files)}，用例文件：{len(case_files)}，"
        f"测试方法：{len(discovered)}"
    )
    for method in discovered:
        print(f"- {method}")


if __name__ == "__main__":
    main()
