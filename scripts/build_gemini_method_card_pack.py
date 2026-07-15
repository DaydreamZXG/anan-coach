#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
from datetime import datetime
from html import escape
from pathlib import Path


PROMPT = """你是公务员国考行测资料分析教研审计员。请审计我上传的“方法卡 + 真题样本 + 答案解析”资料。

我们的产品不是普通刷题软件，而是 AI 教练：方法卡会被用于判分反馈、错因复盘、今日学习计划生成。你的任务是复核这些卡是否能作为稳定的教学 SOP。

请严格按以下边界工作：
1. 不要重写整套体系，不要另起一个新分类法。
2. 不要输出泛泛而谈的备考建议。
3. 不要因为一道题有多种解法就随意拆卡；只有“识别信号、适用条件、解法步骤、错因签名”无法共存时才建议拆。
4. 重点抓：卡片边界混乱、题型误套、步骤不可执行、时间预算不现实、错因 code 不可聚合、卡与卡重复。
5. 每张卡都要判断它到底更像“知识点 / 解法方法 / 题型 / 错因”，如果混杂，请指出。
6. 对每张卡抽样题进行回测判断：按该卡 recognition + method_steps 做，能不能在 50-65 秒内稳定选对。
7. 若样本题明显不属于该卡，也要指出是“抽样/tag 误命中”，不要硬说卡错。

输出必须使用下面结构：

## 总体结论
- 是否建议继续按当前资料分析卡包推进：
- 最大的 3 个风险：
- 最应该先修的 5 张卡：

## 卡片审计表
每张卡一条：
- card_id:
- title:
- card_type: 知识点 / 解法方法 / 题型 / 错因 / 混合不清
- verdict: keep / revise / split / merge / delete / resample
- confidence: high / medium / low
- boundary_problem:
- method_problem:
- sample_backtest: 通过 x/样本数；主要失败题号
- suggested_fix:
- affected_cards:

## 合并/拆分建议
只列真正需要动作的卡：
- action:
- cards:
- reason:
- new_boundary:

## 错因 code 治理
检查 error_signatures.code 是否重复、过细、过粗、不可用于判分反馈。

## 可直接改 YAML 的建议
只给需要改的字段，不要整张重写。
"""


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build Gemini-friendly method-card audit HTML/PDF sources from the local review HTML."
    )
    parser.add_argument("--source-html", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--cards-per-part", type=int, default=4)
    args = parser.parse_args()

    source = Path(args.source_html)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    raw = source.read_text(encoding="utf-8")
    sections = _extract_sections(raw)
    if not sections:
        raise ValueError(f"No card sections found in {source}")

    (output_dir / "gemini_method_card_audit_prompt.md").write_text(
        PROMPT + "\n",
        encoding="utf-8",
    )

    full_html = _render_doc(
        title="Gemini 方法卡审计包：资料分析 batch01 全量",
        sections=[section for _, section in sections],
        source=source,
    )
    (output_dir / "ziliao_batch01_gemini_full.html").write_text(
        full_html,
        encoding="utf-8",
    )

    manifest_lines = [
        "# Gemini 方法卡审计包",
        "",
        f"- generated_at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- source_html: {source}",
        f"- cards: {len(sections)}",
        "",
        "推荐用法：先把 `gemini_method_card_audit_prompt.md` 粘到 Gemini，再上传 PDF。全量 PDF 如果太大，就按 part 分批上传。",
        "",
        "## Files",
        "",
        "- `gemini_method_card_audit_prompt.md`",
        "- `ziliao_batch01_gemini_full.html`",
    ]

    for part_index, chunk in enumerate(_chunks(sections, args.cards_per_part), start=1):
        first_id = chunk[0][0]
        last_id = chunk[-1][0]
        filename = f"ziliao_batch01_gemini_part{part_index:02d}.html"
        html = _render_doc(
            title=f"Gemini 方法卡审计包：资料分析 batch01 part{part_index:02d}",
            sections=[section for _, section in chunk],
            source=source,
        )
        (output_dir / filename).write_text(html, encoding="utf-8")
        manifest_lines.append(f"- `{filename}`: {first_id} ... {last_id}")

    (output_dir / "README.md").write_text(
        "\n".join(manifest_lines) + "\n",
        encoding="utf-8",
    )
    print(f"wrote Gemini pack sources to {output_dir}")
    return 0


def _extract_sections(raw: str) -> list[tuple[str, str]]:
    matches = re.findall(
        r'(<section class="card" id="([^"]+)">.*?</section>)',
        raw,
        flags=re.DOTALL,
    )
    sections: list[tuple[str, str]] = []
    for section, card_id in matches:
        cleaned = _clean_section(section)
        sections.append((card_id, cleaned))
    return sections


def _clean_section(section: str) -> str:
    section = re.sub(
        r'<aside class="review">.*?</aside>',
        "",
        section,
        flags=re.DOTALL,
    )
    section = re.sub(r"<details(?!\s+open)([^>]*)>", r"<details open\1>", section)
    section = re.sub(r'<p class="muted">图片资源：[^<]+</p>', "", section)
    return section


def _render_doc(*, title: str, sections: list[str], source: Path) -> str:
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    prompt_html = "<br>".join(escape(line) for line in PROMPT.splitlines())
    body = "\n".join(sections)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{escape(title)}</title>
  <style>
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", "Helvetica Neue", Arial, sans-serif;
      color: #1f2430;
      background: #fff;
      line-height: 1.58;
    }}
    header {{
      padding: 24px 28px;
      border-bottom: 2px solid #202635;
    }}
    header h1 {{ margin: 0 0 8px; font-size: 24px; }}
    header p {{ margin: 4px 0; color: #4f5668; font-size: 13px; }}
    .prompt {{
      margin-top: 16px;
      padding: 14px 16px;
      border: 1px solid #cfd5e3;
      background: #f8f9fc;
      border-radius: 8px;
      font-size: 13px;
    }}
    main {{ max-width: 1120px; margin: 0 auto; padding: 24px 28px; }}
    section.card {{ border-top: 3px solid #202635; margin: 0 0 30px; padding-top: 18px; }}
    .card-head {{ padding: 0 0 12px; border-bottom: 1px solid #d8dce8; }}
    .card-head h2 {{ margin: 0 0 8px; font-size: 22px; }}
    .meta {{ display: flex; flex-wrap: wrap; gap: 8px; margin: 8px 0; }}
    .pill {{
      padding: 3px 7px;
      border: 1px solid #d8dce8;
      border-radius: 6px;
      font-size: 12px;
      color: #475066;
      background: #fafbff;
    }}
    .card-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }}
    .box {{ border: 1px solid #d8dce8; border-radius: 8px; padding: 10px; background: #fbfcff; }}
    .box h3 {{ margin: 0 0 6px; font-size: 14px; }}
    .question {{ padding: 16px 0; border-top: 1px solid #d8dce8; break-inside: avoid; }}
    .question h3 {{ margin: 0 0 8px; font-size: 17px; }}
    .source {{ color: #697286; font-size: 12px; margin-bottom: 8px; }}
    .q-grid {{ display: block; }}
    details {{ margin: 8px 0; border: 1px solid #d8dce8; border-radius: 8px; padding: 8px 10px; background: #fff; }}
    summary {{ font-weight: 700; }}
    pre {{ white-space: pre-wrap; font-family: inherit; margin: 0; }}
    img {{ max-width: 100%; height: auto; border: 1px solid #d8dce8; border-radius: 6px; margin: 6px 0; }}
    ol, ul {{ margin-top: 6px; padding-left: 22px; }}
    code {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }}
    @media print {{
      header {{ break-after: page; }}
      section.card {{ break-before: page; }}
      details, .box {{ break-inside: avoid; }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>{escape(title)}</h1>
    <p>generated_at: {escape(generated_at)}</p>
    <p>source: {escape(str(source))}</p>
    <div class="prompt">{prompt_html}</div>
  </header>
  <main>
    {body}
  </main>
</body>
</html>
"""


def _chunks(items: list[tuple[str, str]], size: int) -> list[list[tuple[str, str]]]:
    return [items[index : index + size] for index in range(0, len(items), size)]


if __name__ == "__main__":
    raise SystemExit(main())
