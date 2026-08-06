import { describe, expect, it } from "vitest";

import {
  createMaterialReaderState,
  MaterialReaderStateError,
  nextFontScale,
  reduceMaterialReader,
  restoreMaterialReaderState,
} from "../miniprogram/pages/practice/shared/task-runtime/material-reader/state";

// @ts-expect-error Vite exposes mini-program templates through its raw asset loader.
import materialReaderTemplate from "../miniprogram/pages/practice/shared/task-runtime/material-reader/index.wxml?raw";
// @ts-expect-error Vite exposes mini-program templates through its raw asset loader.
import practiceTemplate from "../miniprogram/pages/practice/index.wxml?raw";
// @ts-expect-error Vite exposes mini-program templates through its raw asset loader.
import mockExamTemplate from "../miniprogram/pages/mock-exam/index.wxml?raw";

describe("stage 2 shared material reader", () => {
  it("starts question-first and supports the 72% drawer and fullscreen state sequence", () => {
    const initial = createMaterialReaderState();
    const drawer = reduceMaterialReader(initial, { type: "OPEN_DRAWER" });
    const fullscreen = reduceMaterialReader(drawer, { type: "OPEN_FULLSCREEN" });
    const collapsed = reduceMaterialReader(fullscreen, { type: "RETURN_TO_QUESTION" });

    expect(initial.mode).toBe("question_first");
    expect(drawer.mode).toBe("drawer");
    expect(fullscreen.mode).toBe("fullscreen");
    expect(collapsed.mode).toBe("question_first");
    expect(collapsed.position).toEqual(initial.position);
  });

  it("restores anchor, exact scroll position, table zoom, font size and eye-care mode", () => {
    let questionOne = createMaterialReaderState({
      anchorId: "policy-background",
      scrollTop: 0,
      zoom: 1,
    });
    questionOne = reduceMaterialReader(questionOne, { type: "OPEN_DRAWER" });
    questionOne = reduceMaterialReader(questionOne, { type: "SCROLLED", scrollTop: 1847.4 });
    questionOne = reduceMaterialReader(questionOne, {
      type: "ANCHOR_CHANGED",
      anchorId: "regional-table",
    });
    questionOne = reduceMaterialReader(questionOne, { type: "ZOOM_CHANGED", zoom: 1.5 });
    questionOne = reduceMaterialReader(questionOne, {
      type: "FONT_SCALE_CHANGED",
      fontScale: 1.2,
    });
    questionOne = reduceMaterialReader(questionOne, {
      type: "COLOR_MODE_CHANGED",
      colorMode: "eye_care",
    });

    const questionTwo = createMaterialReaderState({
      anchorId: "growth-trend",
      scrollTop: 320,
      zoom: 0.9,
    });
    expect(questionTwo.position).not.toEqual(questionOne.position);

    const restoredQuestionOne = restoreMaterialReaderState(
      JSON.parse(JSON.stringify(questionOne)) as typeof questionOne,
    );
    expect(restoredQuestionOne).toEqual({
      mode: "drawer",
      position: { anchorId: "regional-table", scrollTop: 1847, zoom: 1.5 },
      preferences: { fontScale: 1.2, colorMode: "eye_care", lineSpacing: 1 },
    });
  });

  it("uses bounded display controls and rejects corrupt persisted positions", () => {
    expect(nextFontScale(0.9, "decrease")).toBe(0.9);
    expect(nextFontScale(1.3, "increase")).toBe(1.3);
    expect(nextFontScale(1, "increase")).toBe(1.1);

    const state = createMaterialReaderState();
    expect(reduceMaterialReader(state, { type: "ZOOM_CHANGED", zoom: 9 }).position.zoom).toBe(2);
    expect(() => reduceMaterialReader(state, { type: "SCROLLED", scrollTop: -1 })).toThrow(
      MaterialReaderStateError,
    );
    expect(() => reduceMaterialReader(state, { type: "ANCHOR_CHANGED", anchorId: "   " })).toThrow(
      MaterialReaderStateError,
    );
    expect(
      reduceMaterialReader(state, { type: "ANCHOR_CHANGED", anchorId: "表一-地区结构" }).position
        .anchorId,
    ).toBe("表一-地区结构");
  });

  it("shows shared material inline before the question and keeps immersive reading available", () => {
    expect(materialReaderTemplate).toContain("material-reader__inline");
    expect(materialReaderTemplate).toContain("material-reader__inline-content");
    // Figma 2:75：作答态材料使用真正的纵向滚动容器，而不是裁切预览。
    expect(materialReaderTemplate).toContain("material-reader__inline-scroll--{{appearance}}");
    expect(materialReaderTemplate).not.toContain("material-reader__inline-preview");
    expect(materialReaderTemplate).toContain("inlineHeaderAvailable");
    expect(materialReaderTemplate).toContain('aria-label="沉浸阅读材料"');
    expect(materialReaderTemplate).not.toContain(">查看材料</text>");
    // Figma 80:2：沉浸态通过页面级 material-reader-shell--immersive 修饰符可判定。
    expect(practiceTemplate).toContain('class="material-reader-shell ');
    expect(practiceTemplate).toContain("material-reader-shell--immersive");
    expect(practiceTemplate).toContain('appearance="answering"');
    // Figma 2:76：上下文动作行并入材料卡标题行。
    expect(practiceTemplate).toContain('class="practice-material-head"');
    expect(practiceTemplate).not.toContain('class="practice-context-bar"');
    expect(practiceTemplate).toContain('draftAvailable="{{false}}"');
    expect(practiceTemplate).toContain('markAvailable="{{false}}"');
    expect(mockExamTemplate).toContain('wx:if="{{question.material}}"');
    expect(mockExamTemplate).not.toContain('wx:if="{{materialOpen && question.material}}"');
  });
});
