import { describe, expect, it } from "vitest";

import { buildAnswerSheetProjection } from "../../../miniprogram/pages/practice/shared/task-runtime/answer-sheet/model";
// @ts-expect-error Vite exposes mini-program templates through its raw asset loader.
import answerSheetTemplate from "../../../miniprogram/pages/practice/shared/task-runtime/answer-sheet/index.wxml?raw";
// @ts-expect-error Vite exposes mini-program styles through its raw asset loader.
import answerSheetStyles from "../../../miniprogram/pages/practice/shared/task-runtime/answer-sheet/index.wxss?raw";

describe("RU-REAL-DEVICE-PRACTICE-WORKSPACE-035 happy path", () => {
  it("groups a 135-question paper by the real adjacent modules and focuses the current item", () => {
    const modules = [
      ["政治理论", 20],
      ["常识判断", 15],
      ["言语理解与表达", 30],
      ["数量关系", 15],
      ["判断推理", 35],
      ["资料分析", 20],
    ] as const;
    let index = 1;
    const items = modules.flatMap(([moduleName, count]) =>
      Array.from({ length: count }, () => {
        const current = index;
        index += 1;
        return {
          index: current,
          label: String(current),
          moduleName,
          state: current < 22 ? ("answered" as const) : ("unanswered" as const),
          marked: current === 11,
          active: current === 21,
        };
      }),
    );

    const projection = buildAnswerSheetProjection(items);

    expect(projection.subtitle).toBe("6 个模块 · 共 135 题");
    expect(projection.sections.map((section) => section.moduleName)).toEqual(
      modules.map(([name]) => name),
    );
    expect(projection.sections.map((section) => section.items.length)).toEqual(
      modules.map(([, count]) => count),
    );
    expect(projection.sections.flatMap((section) => section.items)).toHaveLength(135);
    expect(projection.currentAnchorId).toBe("answer-question-21");
    expect(projection.sections[1]?.counts).toBe("1 已答 · 14 未答");
  });

  it("uses a native vertical scroll container instead of clipping the answer grid", () => {
    expect(String(answerSheetTemplate)).toContain("<scroll-view");
    expect(String(answerSheetTemplate)).toContain("scroll-y");
    expect(String(answerSheetTemplate)).toContain('scroll-into-view="{{scrollTarget}}"');
    expect(String(answerSheetTemplate)).not.toContain("catchtouchmove");
    expect(String(answerSheetStyles)).toMatch(/\.answer-card__sheet\s*\{[\s\S]*?height:\s*88vh;/);
    expect(String(answerSheetStyles)).toMatch(/\.answer-card__scroll\s*\{[\s\S]*?flex:\s*1;/);
  });
});
