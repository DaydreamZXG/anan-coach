#!/usr/bin/env python3
"""把 peipeishua 知识点(knowledge_point)映射到国考行测六大模块。

规则按关键词归类（覆盖 247 个知识点的绝大多数）：
- 政治理论：时政/重要文件/会议讲话/新思想/中国特色社会主义/党建
- 常识判断：法律/常识/历史/地理/科技/文化/经济/管理/公文/道德/中国*
- 言语理解：词/成语/关联词/逻辑填空/细节判断/主旨/意图/语句
- 数量关系：和差倍比/工程/行程/排列/概率/利润/牛吃草/数字推理
- 判断推理：图形/定义/类比/逻辑/削弱/加强/翻译/真假/集合
- 资料分析：资料/比重/增长率/平均数/倍数/统计表/统计图/直接找数
"""
import sys

import psycopg

DB_URL = sys.argv[1] if len(sys.argv) > 1 else "postgresql:///gongkao_dazi_staging?host=/var/run/postgresql"

# 关键词 -> 模块 code（顺序敏感：先匹配更具体的）
_RULES = [
    # 政治理论
    ("political_theory", ("时政", "重要文件", "会议讲话", "新思想", "中国特色社会主义", "党建", "马克思主义", "二十大", "主题教育", "治国理政", "宪法修正")),
    # 常识判断
    ("general_knowledge", (
        "常识", "历史", "地理", "科技", "生物", "物理", "化学", "文化", "法律", "行政",
        "公文", "管理", "经济", "道德", "民法", "刑法", "宪法", "法理", "环保", "前沿",
        "文明", "传统", "文学", "国情", "总则", "时政", "建设", "哲学", "辩证法", "认识论",
        "政治", "新思想", "重要文件", "会议讲话", "中共", "中国", "世界",
    )),
    # 言语理解
    ("verbal_comprehension", (
        "词", "成语", "关联词", "填空", "细节判断", "主旨", "意图", "语句", "标题",
        "逻辑填空", "混搭", "实词", "对应关系", "转折", "并列", "递进", "因果", "对策",
        "承接", "排序", "接语", "词语理解",
    )),
    # 数量关系
    ("quantitative", (
        "和差倍比", "工程", "行程", "排列", "组合", "概率", "利润", "牛吃草", "数字推理",
        "容斥", "浓度", "周期", "几何", "年龄", "日期", "比例", "统筹", "最值",
    )),
    # 判断推理
    ("reasoning", (
        "图形", "定义", "类比", "逻辑", "削弱", "加强", "翻译", "真假", "集合",
        "论证", "前提", "结论", "归纳", "演绎", "因果推理", "位置规律", "数量规律",
        "属性规律", "立体",
    )),
    # 资料分析
    ("data_analysis", (
        "资料", "比重", "增长率", "平均数", "倍数", "统计表", "统计图", "直接找数",
        "现期", "基期", "增长量", "隔年", "指数", "比例", "百分点",
    )),
]


def map_module(knowledge_point: str) -> str | None:
    for code, keywords in _RULES:
        for kw in keywords:
            if kw in knowledge_point:
                return code
    return None


def main() -> None:
    conn = psycopg.connect(DB_URL)
    conn.autocommit = False
    cur = conn.cursor()

    # 取所有不同知识点
    cur.execute(
        "select distinct tag_value from product.question_tags where tag_type = 'knowledge_point'"
    )
    points = [row[0] for row in cur.fetchall()]
    print(f"知识点总数: {len(points)}")

    mapped = 0
    unmapped: list[str] = []
    for point in points:
        module = map_module(point)
        if module is None:
            unmapped.append(point)
            continue
        # 给每个有此知识点的题写 module 标签（先查重）
        cur.execute(
            """
            select distinct question_id from product.question_tags
            where tag_type = 'knowledge_point' and tag_value = %s
            """,
            (point,),
        )
        qids = [row[0] for row in cur.fetchall()]
        for qid in qids:
            cur.execute(
                """
                select count(*) from product.question_tags
                where question_id = %s and tag_type = 'module' and tag_value = %s
                """,
                (qid, module),
            )
            if cur.fetchone()[0] > 0:
                continue
            cur.execute(
                """
                insert into product.question_tags (
                    question_tag_id, question_id, tag_type, tag_value
                ) values (%s, %s, 'module', %s)
                """,
                (f"modtag_{qid[-12:]}_{module[:8]}_{point[:6]}", qid, module),
            )
        mapped += 1
        if mapped % 20 == 0:
            conn.commit()

    # 归一化：把中文/变体模块名合并到标准 code
    _NORMALIZE = {
        "判断推理": "reasoning",
        "言语理解与表达": "verbal_comprehension",
        "言语理解与表达能力": "verbal_comprehension",
        "言语理解": "verbal_comprehension",
        "资料分析": "data_analysis",
        "常识判断": "general_knowledge",
        "数量关系": "quantitative",
        "常识应用能力": "general_knowledge",
        "判断推理能力": "reasoning",
        "数理能力": "quantitative",
        "科学素养": "general_knowledge",
        "综合知识": "general_knowledge",
        "政治理论": "political_theory",
        "政治": "political_theory",
    }
    cur.execute("select distinct tag_value from product.question_tags where tag_type = 'module'")
    norm_count = 0
    for (value,) in cur.fetchall():
        target = _NORMALIZE.get(value)
        if target is None or value == target:
            continue
        # 把 value 标签改为 target（先查重后更新）
        cur.execute(
            """
            update product.question_tags set tag_value = %s
            where tag_type = 'module' and tag_value = %s
              and not exists (
                select 1 from product.question_tags t2
                where t2.question_id = product.question_tags.question_id
                  and t2.tag_type = 'module' and t2.tag_value = %s
              )
            """,
            (target, value, target),
        )
        cur.execute("delete from product.question_tags where tag_type = 'module' and tag_value = %s", (value,))
        norm_count += 1

    conn.commit()
    cur.close()
    conn.close()
    print(f"映射: {mapped} 个知识点")
    print(f"归一化: {norm_count} 个变体模块名")
    print(f"未映射: {len(unmapped)} -> {unmapped[:20]}")


if __name__ == "__main__":
    main()
