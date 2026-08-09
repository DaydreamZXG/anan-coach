import { describe, expect, it } from "vitest";

import {
  createTaskInteractionState,
  projectTaskInteraction,
  reduceTaskInteraction,
} from "../subpackage-shared-src/task-runtime/interaction/state";

describe("task interaction authority", () => {
  it("keeps overlays and annotation surfaces mutually exclusive", () => {
    const drawing = reduceTaskInteraction(createTaskInteractionState(), {
      type: "ANNOTATION_OPENED",
      surface: "question",
    });
    expect(projectTaskInteraction(drawing)).toMatchObject({
      annotationOpen: true,
      annotationSurface: "question",
      annotationMode: "annotate",
      touchOwner: "annotation",
      swipeBlocked: true,
    });

    const tools = reduceTaskInteraction(drawing, {
      type: "OVERLAY_OPENED",
      overlay: "tools",
    });
    expect(projectTaskInteraction(tools)).toMatchObject({
      overlay: "tools",
      annotationOpen: false,
      annotationSurface: "",
      annotationMode: "static",
      touchOwner: "overlay",
      swipeBlocked: true,
      toolsOpen: true,
    });
  });

  it("closes the complete interaction surface on question and lifecycle boundaries", () => {
    const materialDraft = reduceTaskInteraction(createTaskInteractionState(), {
      type: "ANNOTATION_OPENED",
      surface: "material",
    });
    const afterQuestion = reduceTaskInteraction(materialDraft, { type: "QUESTION_CHANGED" });
    expect(projectTaskInteraction(afterQuestion)).toMatchObject({
      annotationOpen: false,
      annotationSurface: "",
      touchOwner: "question",
      swipeBlocked: false,
    });

    const answerSheet = reduceTaskInteraction(afterQuestion, {
      type: "OVERLAY_OPENED",
      overlay: "answer_sheet",
    });
    const hidden = reduceTaskInteraction(answerSheet, { type: "PAGE_HIDDEN" });
    expect(projectTaskInteraction(hidden)).toMatchObject({
      overlay: "none",
      interactionLocked: true,
      touchOwner: "none",
      swipeBlocked: true,
    });

    const shown = reduceTaskInteraction(hidden, { type: "PAGE_SHOWN" });
    expect(projectTaskInteraction(shown)).toMatchObject({
      interactionLocked: false,
      touchOwner: "question",
      swipeBlocked: false,
    });

    const unloaded = reduceTaskInteraction(shown, { type: "PAGE_UNLOADED" });
    expect(projectTaskInteraction(unloaded)).toMatchObject({
      interactionLocked: true,
      touchOwner: "none",
      swipeBlocked: true,
    });
  });

  it("owns pause capture and releases it while the page is hidden or unloaded", () => {
    const paused = reduceTaskInteraction(createTaskInteractionState(), { type: "TASK_PAUSED" });
    expect(projectTaskInteraction(paused)).toMatchObject({
      interactionMode: "paused",
      taskPaused: true,
      materialOpen: false,
      pauseCaptureActive: true,
      materialCaptureActive: false,
      captureLayer: "pause",
      touchOwner: "overlay",
      interactionLocked: true,
      swipeBlocked: true,
    });

    const hidden = reduceTaskInteraction(paused, { type: "PAGE_HIDDEN" });
    expect(projectTaskInteraction(hidden)).toMatchObject({
      interactionMode: "paused",
      taskPaused: true,
      pauseCaptureActive: false,
      captureLayer: "none",
      touchOwner: "none",
      swipeBlocked: true,
    });

    const shown = reduceTaskInteraction(hidden, { type: "PAGE_SHOWN" });
    expect(projectTaskInteraction(shown)).toMatchObject({
      taskPaused: true,
      pauseCaptureActive: true,
      captureLayer: "pause",
      touchOwner: "overlay",
    });

    const resumed = reduceTaskInteraction(shown, { type: "TASK_RESUMED" });
    expect(projectTaskInteraction(resumed)).toMatchObject({
      interactionMode: "question",
      taskPaused: false,
      pauseCaptureActive: false,
      captureLayer: "none",
      touchOwner: "question",
      interactionLocked: false,
      swipeBlocked: false,
    });

    const unloaded = reduceTaskInteraction(paused, { type: "PAGE_UNLOADED" });
    expect(projectTaskInteraction(unloaded)).toMatchObject({
      interactionMode: "question",
      taskPaused: false,
      pauseCaptureActive: false,
      captureLayer: "none",
      touchOwner: "none",
    });
  });

  it("owns the material surface and closes it through question and session transitions", () => {
    const material = reduceTaskInteraction(createTaskInteractionState(), {
      type: "MATERIAL_OPENED",
    });
    expect(projectTaskInteraction(material)).toMatchObject({
      interactionMode: "material",
      materialOpen: true,
      taskPaused: false,
      materialCaptureActive: true,
      captureLayer: "material",
      touchOwner: "material",
      swipeBlocked: true,
    });

    const tools = reduceTaskInteraction(material, {
      type: "OVERLAY_OPENED",
      overlay: "tools",
    });
    expect(projectTaskInteraction(tools)).toMatchObject({
      materialOpen: true,
      materialCaptureActive: true,
      captureLayer: "overlay",
      touchOwner: "overlay",
    });

    const hidden = reduceTaskInteraction(tools, { type: "PAGE_HIDDEN" });
    expect(projectTaskInteraction(hidden)).toMatchObject({
      materialOpen: true,
      materialCaptureActive: false,
      captureLayer: "none",
      touchOwner: "none",
    });

    const changed = reduceTaskInteraction(hidden, { type: "QUESTION_CHANGED" });
    expect(projectTaskInteraction(changed)).toMatchObject({
      interactionMode: "question",
      materialOpen: false,
      materialCaptureActive: false,
      captureLayer: "none",
    });

    const ready = reduceTaskInteraction(material, { type: "SESSION_READY" });
    expect(projectTaskInteraction(ready)).toMatchObject({
      interactionMode: "question",
      materialOpen: false,
      touchOwner: "question",
    });
  });

  it("fails closed for account changes and submit until the session is ready again", () => {
    const help = reduceTaskInteraction(createTaskInteractionState(), {
      type: "OVERLAY_OPENED",
      overlay: "help",
    });
    const changedOwner = reduceTaskInteraction(help, { type: "ACCOUNT_CHANGED" });
    expect(projectTaskInteraction(changedOwner)).toMatchObject({
      overlay: "none",
      interactionLocked: true,
      touchOwner: "none",
    });
    expect(
      reduceTaskInteraction(changedOwner, {
        type: "ANNOTATION_OPENED",
        surface: "question",
      }),
    ).toEqual(changedOwner);

    const ready = reduceTaskInteraction(changedOwner, { type: "SESSION_READY" });
    const submitting = reduceTaskInteraction(ready, { type: "SUBMIT_STARTED" });
    expect(projectTaskInteraction(submitting)).toMatchObject({
      interactionLocked: true,
      annotationOpen: false,
      overlay: "none",
      touchOwner: "none",
    });

    const retryable = reduceTaskInteraction(submitting, { type: "SUBMIT_FAILED" });
    expect(projectTaskInteraction(retryable)).toMatchObject({
      interactionLocked: false,
      touchOwner: "question",
      swipeBlocked: false,
    });
  });

  it("toggles only the requested overlay and closes it through one transition", () => {
    const open = reduceTaskInteraction(createTaskInteractionState(), {
      type: "OVERLAY_TOGGLED",
      overlay: "draft",
    });
    expect(projectTaskInteraction(open)).toMatchObject({ draftOpen: true, overlay: "draft" });

    const closed = reduceTaskInteraction(open, {
      type: "OVERLAY_TOGGLED",
      overlay: "draft",
    });
    expect(projectTaskInteraction(closed)).toMatchObject({
      draftOpen: false,
      overlay: "none",
      touchOwner: "question",
    });
  });

  it("preserves the active surface across a temporary checkpoint lock", () => {
    const drawing = reduceTaskInteraction(createTaskInteractionState(), {
      type: "ANNOTATION_OPENED",
      surface: "question",
    });
    const saving = reduceTaskInteraction(drawing, { type: "LOCK_CHANGED", locked: true });
    expect(projectTaskInteraction(saving)).toMatchObject({
      annotationOpen: true,
      annotationSurface: "question",
      touchOwner: "annotation",
      interactionLocked: true,
      swipeBlocked: true,
    });

    const suspended = reduceTaskInteraction(saving, { type: "SESSION_SUSPENDED" });
    expect(projectTaskInteraction(suspended)).toMatchObject({
      annotationOpen: false,
      annotationSurface: "",
      touchOwner: "none",
      interactionLocked: true,
    });
  });
});
