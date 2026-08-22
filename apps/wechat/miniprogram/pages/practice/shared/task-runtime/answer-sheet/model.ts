export type AnswerSheetState = "answered" | "skipped" | "unanswered";

export interface AnswerSheetSourceItem {
  index: number;
  label: string;
  moduleName: string;
  state: AnswerSheetState;
  marked: boolean;
  active: boolean;
}

export interface AnswerSheetSection {
  id: string;
  moduleName: string;
  counts: string;
  items: Array<AnswerSheetSourceItem & { anchorId: string }>;
}

export interface AnswerSheetProjection {
  sections: AnswerSheetSection[];
  currentAnchorId: string;
  subtitle: string;
}

function safeModuleName(value: string): string {
  const name = value.trim();
  return name || "未分组";
}

/** 把相邻同模块题目按试卷真实顺序归组，同时保留全卷题号 index。 */
export function buildAnswerSheetProjection(
  sourceItems: readonly AnswerSheetSourceItem[],
): AnswerSheetProjection {
  const sections: AnswerSheetSection[] = [];
  let currentAnchorId = "";

  for (const source of sourceItems) {
    const moduleName = safeModuleName(source.moduleName);
    const previous = sections.at(-1);
    const section =
      previous?.moduleName === moduleName
        ? previous
        : (() => {
            const created: AnswerSheetSection = {
              id: `answer-module-${String(sections.length)}`,
              moduleName,
              counts: "",
              items: [],
            };
            sections.push(created);
            return created;
          })();
    const anchorId = `answer-question-${String(source.index)}`;
    section.items.push({ ...source, moduleName, anchorId });
    if (source.active) currentAnchorId = anchorId;
  }

  for (const section of sections) {
    const answered = section.items.filter((item) => item.state === "answered").length;
    const unanswered = section.items.length - answered;
    const marked = section.items.filter((item) => item.marked).length;
    section.counts = `${String(answered)} 已答 · ${String(unanswered)} 未答${
      marked > 0 ? ` · ${String(marked)} 存疑` : ""
    }`;
  }

  const subtitle =
    sections.length === 1
      ? `${sections[0]?.moduleName ?? "未分组"} · 共 ${String(sourceItems.length)} 题`
      : `${String(sections.length)} 个模块 · 共 ${String(sourceItems.length)} 题`;
  return { sections, currentAnchorId, subtitle };
}
