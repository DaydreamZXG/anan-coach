#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime
from html import escape
from pathlib import Path
import re
import sys
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
import yaml


@dataclass(frozen=True)
class ReviewCard:
    id: str
    title: str
    module_name: str
    tag_path: str
    recognition: str
    applicability: dict[str, list[str]]
    method_steps: list[str]
    error_signatures: list[dict[str, str]]
    time_budget_sec: int
    accuracy_rule: str
    pitfall: str | None
    priority: int
    status: str


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export a local HTML review pack for draft method cards."
    )
    parser.add_argument("--cards", required=True, help="Method-card YAML file")
    parser.add_argument("--database-url", required=True, help="SQLAlchemy DB URL")
    parser.add_argument("--output", required=True, help="Output HTML path")
    parser.add_argument(
        "--local-project-root",
        default=str(Path.cwd()),
        help="Absolute local project root used for file:// image URLs",
    )
    parser.add_argument("--questions-per-card", type=int, default=10)
    args = parser.parse_args()

    cards = _load_cards(Path(args.cards))
    engine = create_engine(args.database_url, pool_pre_ping=True, future=True)
    try:
        with Session(engine, future=True) as session:
            blocks = [
                _card_block(
                    session=session,
                    card=card,
                    limit=args.questions_per_card,
                    local_project_root=Path(args.local_project_root),
                )
                for card in cards
            ]
    finally:
        engine.dispose()

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        _render_html(cards=cards, blocks=blocks),
        encoding="utf-8",
    )
    print(f"wrote {output} with {len(cards)} cards")
    return 0


def _load_cards(path: Path) -> list[ReviewCard]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    items = raw if isinstance(raw, list) else [raw]
    cards: list[ReviewCard] = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError(f"{path}: expected YAML objects")
        cards.append(
            ReviewCard(
                id=str(item["id"]),
                title=str(item["title"]),
                module_name=str(item["module_name"]),
                tag_path=str(item["tag_path"]),
                recognition=str(item["recognition"]),
                applicability=item["applicability"],
                method_steps=[str(step) for step in item["method_steps"]],
                error_signatures=list(item["error_signatures"]),
                time_budget_sec=int(item["time_budget_sec"]),
                accuracy_rule=str(item["accuracy_rule"]),
                pitfall=item.get("pitfall"),
                priority=int(item["priority"]),
                status=str(item["status"]),
            )
        )
    return cards


def _card_block(
    *,
    session: Session,
    card: ReviewCard,
    limit: int,
    local_project_root: Path,
) -> dict[str, Any]:
    questions, match_note = _sample_questions(session=session, card=card, limit=limit)
    question_ids = [row["question_id"] for row in questions]
    options = _group_by_question(
        session=session,
        question_ids=question_ids,
        sql="""
        select question_id, option_index, label, option_text, option_html
        from product.question_options
        where question_id = any(:question_ids)
        order by question_id, option_index
        """,
    )
    explanations = _dict_by_question(
        session=session,
        question_ids=question_ids,
        sql="""
        select question_id, preferred_analysis, original_analysis, analysis_source, analysis_status
        from product.question_explanations
        where question_id = any(:question_ids)
        """,
    )
    occurrences = _group_by_question(
        session=session,
        question_ids=question_ids,
        sql="""
        select question_id, province, year, number, coalesce(paper_title, occurrence_paper_title) as paper_title
        from product.question_occurrences
        where question_id = any(:question_ids)
        order by question_id, canonical_occurrence_rank nulls last, year desc nulls last
        """,
    )
    assets = _group_by_question(
        session=session,
        question_ids=question_ids,
        sql="""
        select question_id, image_src, object_key
        from product.question_assets
        where question_id = any(:question_ids)
        order by question_id, part_index nulls last, image_ordinal nulls last
        """,
    )

    rendered_questions = []
    for index, question in enumerate(questions, start=1):
        asset_urls = _asset_url_map(
            rows=assets.get(question["question_id"], []),
            local_project_root=local_project_root,
        )
        rendered_questions.append(
            {
                "index": index,
                "question": question,
                "material": _render_rich_text(
                    html_value=question.get("material_html"),
                    text_value=question.get("material_text"),
                    asset_urls=asset_urls,
                ),
                "stem": _render_rich_text(
                    html_value=question.get("stem_html"),
                    text_value=question.get("stem"),
                    asset_urls=asset_urls,
                ),
                "options": [
                    {
                        "label": option["label"],
                        "body": _render_rich_text(
                            html_value=option.get("option_html"),
                            text_value=option.get("option_text"),
                            asset_urls=asset_urls,
                        ),
                    }
                    for option in options.get(question["question_id"], [])
                ],
                "explanation": explanations.get(question["question_id"]),
                "occurrences": occurrences.get(question["question_id"], []),
                "assets": assets.get(question["question_id"], []),
            }
        )

    return {
        "card": card,
        "match_note": match_note,
        "questions": rendered_questions,
    }


def _sample_questions(
    *,
    session: Session,
    card: ReviewCard,
    limit: int,
) -> tuple[list[dict[str, Any]], str]:
    parts = card.tag_path.split("/")
    level1 = parts[0] if len(parts) > 0 else card.module_name
    level2 = parts[1] if len(parts) > 1 else None
    level3 = parts[2] if len(parts) > 2 else None
    rows = session.execute(
        text(
            """
            select
              q.question_id,
              q.module_name,
              q.display_level1,
              q.display_level2,
              q.display_level3,
              q.stem,
              q.stem_html,
              q.material_text,
              q.material_html,
              q.answer,
              q.visual_required,
              q.needs_multimodal,
              q.latest_year,
              q.occurrence_count
            from product.questions q
            where q.status = 'active'
              and q.domain = 'objective'
              and q.module_name = :module_name
              and q.display_level1 = :level1
              and (cast(:level2 as text) is null or q.display_level2 = cast(:level2 as text))
              and (cast(:level3 as text) is null or q.display_level3 = cast(:level3 as text))
            order by q.latest_year desc nulls last,
                     q.occurrence_count desc nulls last,
                     md5(q.question_id)
            limit :limit
            """
        ),
        {
            "module_name": card.module_name,
            "level1": level1,
            "level2": level2,
            "level3": level3,
            "limit": limit,
        },
    ).mappings().all()
    if len(rows) >= limit:
        return [dict(row) for row in rows], "exact_display_path"

    leaf = parts[-1]
    fallback = session.execute(
        text(
            """
            select
              q.question_id,
              q.module_name,
              q.display_level1,
              q.display_level2,
              q.display_level3,
              q.stem,
              q.stem_html,
              q.material_text,
              q.material_html,
              q.answer,
              q.visual_required,
              q.needs_multimodal,
              q.latest_year,
              q.occurrence_count
            from product.questions q
            where q.status = 'active'
              and q.domain = 'objective'
              and q.module_name = :module_name
              and exists (
                select 1
                from product.question_tags t
                where t.question_id = q.question_id
                  and t.tag_value = :leaf
              )
            order by q.latest_year desc nulls last,
                     q.occurrence_count desc nulls last,
                     md5(q.question_id)
            limit :limit
            """
        ),
        {"module_name": card.module_name, "leaf": leaf, "limit": limit},
    ).mappings().all()
    return [dict(row) for row in fallback], f"tag_leaf:{leaf}"


def _group_by_question(
    *,
    session: Session,
    question_ids: list[str],
    sql: str,
) -> dict[str, list[dict[str, Any]]]:
    if not question_ids:
        return {}
    rows = session.execute(text(sql), {"question_ids": question_ids}).mappings().all()
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row["question_id"]), []).append(dict(row))
    return grouped


def _dict_by_question(
    *,
    session: Session,
    question_ids: list[str],
    sql: str,
) -> dict[str, dict[str, Any]]:
    if not question_ids:
        return {}
    rows = session.execute(text(sql), {"question_ids": question_ids}).mappings().all()
    return {str(row["question_id"]): dict(row) for row in rows}


def _asset_url_map(
    *,
    rows: list[dict[str, Any]],
    local_project_root: Path,
) -> dict[str, str]:
    result: dict[str, str] = {}
    for row in rows:
        image_src = row.get("image_src")
        object_key = row.get("object_key")
        if not isinstance(image_src, str) or not isinstance(object_key, str):
            continue
        if not object_key.startswith("data/"):
            continue
        result[image_src] = (local_project_root / object_key).as_uri()
    return result


def _render_rich_text(
    *,
    html_value: object,
    text_value: object,
    asset_urls: dict[str, str],
) -> str:
    if isinstance(html_value, str) and html_value.strip():
        return _rewrite_image_sources(html_value, asset_urls)
    if isinstance(text_value, str) and text_value.strip():
        return f"<pre>{escape(text_value.strip())}</pre>"
    return "<p class=\"muted\">无文本</p>"


def _rewrite_image_sources(raw_html: str, asset_urls: dict[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        src = match.group(1)
        return f'src="{escape(asset_urls.get(src, src), quote=True)}"'

    return re.sub(r'src="([^"]+)"', replace, raw_html)


def _render_html(*, cards: list[ReviewCard], blocks: list[dict[str, Any]]) -> str:
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    nav = "\n".join(
        f'<a href="#{escape(card.id)}">{escape(card.title)}</a>' for card in cards
    )
    body = "\n".join(_render_card_block(block) for block in blocks)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>考公搭子方法卡回测包</title>
  <style>
    :root {{ color-scheme: light; --brand: #3347d3; --line: #d8dce8; --bg: #f6f7fb; --text: #1f2430; }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", "Helvetica Neue", Arial, sans-serif; color: var(--text); background: var(--bg); }}
    header {{ position: sticky; top: 0; z-index: 10; padding: 14px 22px; background: rgba(255,255,255,.96); border-bottom: 1px solid var(--line); }}
    header h1 {{ margin: 0 0 6px; font-size: 20px; }}
    header p {{ margin: 0; color: #5d6472; font-size: 13px; }}
    .layout {{ display: grid; grid-template-columns: 270px minmax(0, 1fr); gap: 18px; max-width: 1440px; margin: 0 auto; padding: 18px; }}
    nav {{ position: sticky; top: 82px; align-self: start; max-height: calc(100vh - 100px); overflow: auto; padding: 12px; background: #fff; border: 1px solid var(--line); border-radius: 12px; }}
    nav a {{ display: block; padding: 8px 10px; color: #263046; text-decoration: none; border-radius: 8px; font-size: 13px; }}
    nav a:hover {{ background: #eef1ff; color: var(--brand); }}
    main {{ min-width: 0; }}
    section.card {{ background: #fff; border: 1px solid var(--line); border-radius: 14px; margin-bottom: 18px; overflow: hidden; }}
    .card-head {{ padding: 18px; border-bottom: 1px solid var(--line); }}
    .card-head h2 {{ margin: 0 0 8px; font-size: 20px; }}
    .meta {{ display: flex; flex-wrap: wrap; gap: 8px; margin: 8px 0; }}
    .pill {{ padding: 4px 8px; border: 1px solid var(--line); border-radius: 999px; font-size: 12px; color: #475066; background: #fafbff; }}
    .card-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }}
    .box {{ border: 1px solid var(--line); border-radius: 10px; padding: 12px; background: #fbfcff; }}
    .box h3 {{ margin: 0 0 8px; font-size: 14px; }}
    ol, ul {{ margin-top: 6px; padding-left: 22px; }}
    .question {{ padding: 18px; border-top: 1px solid var(--line); }}
    .question h3 {{ margin: 0 0 10px; font-size: 17px; }}
    .source {{ color: #697286; font-size: 12px; margin-bottom: 8px; }}
    .q-grid {{ display: grid; grid-template-columns: minmax(0, 1fr) 300px; gap: 14px; align-items: start; }}
    .review {{ border: 1px solid var(--line); border-radius: 10px; padding: 12px; background: #fff; position: sticky; top: 88px; }}
    .review label {{ display: block; padding: 6px 0; font-size: 14px; }}
    textarea {{ width: 100%; min-height: 90px; margin-top: 8px; resize: vertical; border: 1px solid var(--line); border-radius: 8px; padding: 8px; font: inherit; }}
    details {{ margin-top: 12px; border: 1px solid var(--line); border-radius: 10px; padding: 10px 12px; background: #fff; }}
    summary {{ cursor: pointer; font-weight: 600; }}
    pre {{ white-space: pre-wrap; line-height: 1.65; font-family: inherit; margin: 0; }}
    img {{ max-width: 100%; height: auto; border: 1px solid var(--line); border-radius: 8px; margin: 6px 0; }}
    .muted {{ color: #6b7280; }}
    .export {{ margin-left: auto; float: right; border: 0; background: var(--brand); color: #fff; border-radius: 8px; padding: 8px 12px; cursor: pointer; }}
    @media (max-width: 980px) {{ .layout {{ display: block; }} nav {{ position: static; margin-bottom: 16px; }} .q-grid, .card-grid {{ display: block; }} .review {{ position: static; margin-top: 12px; }} }}
  </style>
</head>
<body>
  <header>
    <button class="export" onclick="exportResults()">导出回测结果 JSON</button>
    <h1>考公搭子方法卡回测包</h1>
    <p>生成时间：{escape(generated_at)}。判定：10 题中至少 8 题 pass 才能发布；revise/fail 都要写一句原因。</p>
  </header>
  <div class="layout">
    <nav>{nav}</nav>
    <main>{body}</main>
  </div>
  <script>
    const KEY = "method-card-review-results-v1";
    const state = JSON.parse(localStorage.getItem(KEY) || "{{}}");
    function saveResult(cardId, questionId, field, value) {{
      state[cardId] ||= {{}};
      state[cardId][questionId] ||= {{}};
      state[cardId][questionId][field] = value;
      localStorage.setItem(KEY, JSON.stringify(state, null, 2));
    }}
    function restore() {{
      document.querySelectorAll("[data-review-radio]").forEach(input => {{
        const card = input.dataset.card, q = input.dataset.question;
        if (state[card]?.[q]?.result === input.value) input.checked = true;
      }});
      document.querySelectorAll("[data-review-note]").forEach(input => {{
        const card = input.dataset.card, q = input.dataset.question;
        if (state[card]?.[q]?.note) input.value = state[card][q].note;
      }});
    }}
    function exportResults() {{
      const blob = new Blob([JSON.stringify(state, null, 2)], {{type: "application/json"}});
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = "method-card-review-results.json";
      a.click();
      URL.revokeObjectURL(a.href);
    }}
    restore();
  </script>
</body>
</html>
"""


def _render_card_block(block: dict[str, Any]) -> str:
    card: ReviewCard = block["card"]
    include = "".join(f"<li>{escape(item)}</li>" for item in card.applicability["include"])
    exclude = "".join(f"<li>{escape(item)}</li>" for item in card.applicability["exclude"])
    steps = "".join(f"<li>{escape(item)}</li>" for item in card.method_steps)
    errors = "".join(
        f"<li><code>{escape(str(item['code']))}</code>：{escape(str(item['description']))}</li>"
        for item in card.error_signatures
    )
    questions = "\n".join(_render_question(card, item) for item in block["questions"])
    return f"""
<section class="card" id="{escape(card.id)}">
  <div class="card-head">
    <h2>{escape(card.title)}</h2>
    <div class="meta">
      <span class="pill">{escape(card.id)}</span>
      <span class="pill">{escape(card.tag_path)}</span>
      <span class="pill">限时 {card.time_budget_sec} 秒/题</span>
      <span class="pill">抽样：{escape(str(block["match_note"]))}</span>
      <span class="pill">状态：{escape(card.status)}</span>
    </div>
    <div class="card-grid">
      <div class="box"><h3>识别信号</h3><p>{escape(card.recognition)}</p></div>
      <div class="box"><h3>精度规则</h3><p>{escape(card.accuracy_rule)}</p><p class="muted">{escape(card.pitfall or "")}</p></div>
      <div class="box"><h3>适用</h3><ul>{include}</ul><h3>排除</h3><ul>{exclude}</ul></div>
      <div class="box"><h3>步骤</h3><ol>{steps}</ol><h3>常见错因</h3><ul>{errors}</ul></div>
    </div>
  </div>
  {questions}
</section>
"""


def _render_question(card: ReviewCard, item: dict[str, Any]) -> str:
    question = item["question"]
    qid = str(question["question_id"])
    occurrences = item["occurrences"]
    occurrence_text = "；".join(
        " ".join(
            str(part)
            for part in [
                occ.get("year"),
                occ.get("province"),
                f"第{occ.get('number')}题" if occ.get("number") is not None else None,
            ]
            if part
        )
        for occ in occurrences[:3]
    )
    option_html = "".join(
        f"<li><strong>{escape(str(option['label']))}.</strong> {option['body']}</li>"
        for option in item["options"]
    )
    explanation = item["explanation"] or {}
    analysis = explanation.get("preferred_analysis") or explanation.get("original_analysis") or ""
    asset_note = ""
    if item["assets"]:
        asset_note = f"<p class=\"muted\">图片资源：{len(item['assets'])} 个；若图片未显示，说明本机缺少对应 asset 文件。</p>"
    return f"""
<article class="question">
  <h3>{item['index']}. {escape(qid)}</h3>
  <div class="source">{escape(occurrence_text)} · latestYear={escape(str(question.get('latest_year')))} · visual={escape(str(question.get('visual_required')))}</div>
  <div class="q-grid">
    <div>
      <details open><summary>材料</summary>{item['material']}{asset_note}</details>
      <details open><summary>题干</summary>{item['stem']}</details>
      <details open><summary>选项</summary><ol>{option_html}</ol></details>
      <details><summary>答案与解析</summary><p><strong>答案：{escape(str(question.get('answer') or ''))}</strong></p><pre>{escape(str(analysis))}</pre></details>
    </div>
    <aside class="review">
      <strong>回测判定</strong>
      <label><input data-review-radio data-card="{escape(card.id)}" data-question="{escape(qid)}" name="{escape(card.id)}-{escape(qid)}" value="pass" type="radio" onchange="saveResult('{escape(card.id)}','{escape(qid)}','result',this.value)"> pass：能识别、能做对、耗时合理</label>
      <label><input data-review-radio data-card="{escape(card.id)}" data-question="{escape(qid)}" name="{escape(card.id)}-{escape(qid)}" value="revise" type="radio" onchange="saveResult('{escape(card.id)}','{escape(qid)}','result',this.value)"> revise：能做但步骤/识别要改</label>
      <label><input data-review-radio data-card="{escape(card.id)}" data-question="{escape(qid)}" name="{escape(card.id)}-{escape(qid)}" value="fail" type="radio" onchange="saveResult('{escape(card.id)}','{escape(qid)}','result',this.value)"> fail：按卡会错或误套</label>
      <textarea data-review-note data-card="{escape(card.id)}" data-question="{escape(qid)}" placeholder="一句话原因，例如：识别信号缺“混合/合计”；步骤第2步太慢。" oninput="saveResult('{escape(card.id)}','{escape(qid)}','note',this.value)"></textarea>
    </aside>
  </div>
</article>
"""


if __name__ == "__main__":
    raise SystemExit(main())
