"""生成 Swift 静态专业目录数据文件。

数据来源：教育部《学位授予和人才培养学科目录》(2022年版)
输出：MajorCatalog.swift — 14门类 / 105专业类 / 742专业
"""
from __future__ import annotations

import sys
sys.path.insert(0, ".")

from generate_major_catalog_seed import CATEGORIES, SUB_CATEGORIES  # type: ignore


def generate_swift() -> str:
    lines: list[str] = []
    lines.append("import Foundation")
    lines.append("")
    lines.append("enum MajorCatalog {")
    lines.append("")
    lines.append("    static let categories: [MajorCategory] = [")

    for cat_code, cat_name in CATEGORIES:
        subs = [(sc, sn, ms) for sc, sn, ms in SUB_CATEGORIES if sc.startswith(cat_code)]
        if not subs:
            continue
        lines.append(f"        .init(code: \"{cat_code}\", name: \"{cat_name}\", subCategories: [")
        for sub_code, sub_name, majors in subs:
            if not majors:
                continue
            lines.append(f"            .init(code: \"{sub_code}\", name: \"{sub_name}\", majors: [")
            seq = 0
            seen: set[str] = set()
            for m_name in majors:
                if m_name in seen:
                    continue
                seen.add(m_name)
                seq += 1
                m_code = f"{sub_code}{seq:02d}"
                lines.append(f"                .init(code: \"{m_code}\", name: \"{m_name}\"),")
            lines.append("            ]),")
        lines.append("        ]),")

    lines.append("    ]")
    lines.append("}")
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    swift = generate_swift()
    out_path = "/Users/zxg/Documents/新项目/apps/ios/Gongkao/Gongkao/MajorCatalog.swift"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(swift)
    print(f"Generated {out_path}")

    total = sum(
        1
        for _, subs in [
            (cat_code, [(sc, sn, ms) for sc, sn, ms in SUB_CATEGORIES if sc.startswith(cat_code)])
            for cat_code, _ in CATEGORIES
        ]
        for _, _, majors in subs
        for _ in majors
    )
    print(f"Categories: {len(CATEGORIES)}")
    print(f"Sub-categories: {len(SUB_CATEGORIES)}")
    print(f"Majors: ~{total}")
