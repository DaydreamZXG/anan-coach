import { describe, expect, it } from "vitest";

import {
  createMaterialReaderState,
  MaterialReaderStateError,
  nextFontScale,
  reduceMaterialReader,
  restoreMaterialReaderState,
} from "../miniprogram/task-runtime/material-reader/state";

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
      preferences: { fontScale: 1.2, colorMode: "eye_care" },
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
});
