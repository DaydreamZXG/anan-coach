import { describe, expect, it } from "vitest";
import {
  createTaskRuntime,
  reduceTaskRuntime,
  TaskTransitionError,
} from "../miniprogram/task-runtime/state/runtime";

describe("task runtime", () => {
  it("records answer evidence without mutating prior state", () => {
    const initial = createTaskRuntime({
      taskId: "task-1",
      mode: "practice",
      questionOrder: ["q1", "q2"],
    });
    const started = reduceTaskRuntime(initial, {
      type: "TASK_STARTED",
      startedAt: "2026-07-12T12:00:00Z",
    });
    const answered = reduceTaskRuntime(started, {
      type: "ANSWER_SELECTED",
      optionIds: ["B"],
    });
    const elapsed = reduceTaskRuntime(answered, { type: "TICK", seconds: 12 });

    expect(initial.phase).toBe("briefing");
    expect(started.attempts.q1?.selectedOptionIds).toEqual([]);
    expect(elapsed.attempts.q1).toMatchObject({
      selectedOptionIds: ["B"],
      elapsedSeconds: 12,
      skipped: false,
    });
    expect(elapsed.totalElapsedSeconds).toBe(12);
    expect(elapsed.syncState).toBe("pending");
  });

  it("preserves material position, draft, elimination, confidence and marking", () => {
    let state = reduceTaskRuntime(
      createTaskRuntime({ taskId: "task-2", mode: "practice", questionOrder: ["q1"] }),
      { type: "TASK_STARTED", startedAt: "2026-07-12T12:00:00Z" },
    );
    state = reduceTaskRuntime(state, {
      type: "OPTION_ELIMINATED",
      optionId: "D",
      eliminated: true,
    });
    state = reduceTaskRuntime(state, { type: "CONFIDENCE_SET", confidence: "medium" });
    state = reduceTaskRuntime(state, { type: "QUESTION_MARKED", marked: true });
    state = reduceTaskRuntime(state, { type: "DRAFT_CHANGED", text: "A = B × (1+r)" });
    state = reduceTaskRuntime(state, {
      type: "MATERIAL_POSITION_CHANGED",
      position: { anchorId: "table-1-row-3", scrollTop: 240, zoom: 1.25 },
    });

    expect(state.attempts.q1).toMatchObject({
      eliminatedOptionIds: ["D"],
      confidence: "medium",
      marked: true,
      draftText: "A = B × (1+r)",
      materialPosition: { anchorId: "table-1-row-3", scrollTop: 240, zoom: 1.25 },
    });
  });

  it("uses explicit save and revision conflict states", () => {
    const initial = createTaskRuntime({
      taskId: "task-3",
      mode: "essay",
      questionOrder: ["essay-1"],
      revision: 4,
    });
    const saving = reduceTaskRuntime(initial, { type: "SAVE_REQUESTED" });
    const saved = reduceTaskRuntime(saving, { type: "SAVE_SUCCEEDED", revision: 5 });
    const conflicted = reduceTaskRuntime(saved, {
      type: "REVISION_CONFLICT",
      requestId: "api-conflict-1",
    });

    expect(saving.syncState).toBe("saving");
    expect(saved).toMatchObject({ syncState: "saved", revision: 5 });
    expect(conflicted).toMatchObject({
      syncState: "conflict",
      requestId: "api-conflict-1",
    });
    expect(() => reduceTaskRuntime(saved, { type: "SAVE_SUCCEEDED", revision: 3 })).toThrow(
      TaskTransitionError,
    );
  });

  it("keeps the mock exam timer active when leaving and restricts submission", () => {
    const briefing = createTaskRuntime({
      taskId: "mock-1",
      mode: "mock_exam",
      questionOrder: ["q1"],
    });
    expect(briefing.exitPolicy).toBe("timer_continues");
    expect(() =>
      reduceTaskRuntime(briefing, {
        type: "TASK_SUBMITTED",
        submittedAt: "2026-07-12T14:00:00Z",
      }),
    ).toThrow(TaskTransitionError);

    const answering = reduceTaskRuntime(briefing, {
      type: "TASK_STARTED",
      startedAt: "2026-07-12T12:00:00Z",
    });
    const submitted = reduceTaskRuntime(answering, {
      type: "TASK_SUBMITTED",
      submittedAt: "2026-07-12T14:00:00Z",
    });
    expect(submitted).toMatchObject({ phase: "submitted", exitPolicy: "immediate" });
  });

  it("rejects invalid question and phase transitions", () => {
    const initial = createTaskRuntime({
      taskId: "task-4",
      mode: "verification",
      questionOrder: ["q1"],
    });
    expect(() => reduceTaskRuntime(initial, { type: "ANSWER_SELECTED", optionIds: ["A"] })).toThrow(
      TaskTransitionError,
    );
    expect(() => reduceTaskRuntime(initial, { type: "PHASE_CHANGED", phase: "summary" })).toThrow(
      TaskTransitionError,
    );
    expect(() =>
      createTaskRuntime({
        taskId: "duplicate",
        mode: "practice",
        questionOrder: ["q1", "q1"],
      }),
    ).toThrow(TaskTransitionError);
  });

  it("restores a server session and keeps the restored checkpoint immutable", () => {
    const restoredAttempt = {
      questionId: "q1",
      selectedOptionIds: ["B"],
      eliminatedOptionIds: ["D"],
      confidence: "medium" as const,
      marked: true,
      skipped: false,
      elapsedSeconds: 18,
      draftText: "先排除 D",
      materialPosition: { anchorId: "material-2", scrollTop: 160, zoom: 1.2 },
    };
    const state = createTaskRuntime({
      taskId: "session-1",
      serverSessionId: "session-1",
      planTaskId: "plan-task-1",
      contentVersionId: "bundle-v3",
      mode: "practice",
      phase: "answering",
      questionOrder: ["q1"],
      attempts: { q1: restoredAttempt },
      totalElapsedSeconds: 18,
      revision: 6,
    });

    restoredAttempt.selectedOptionIds.push("A");
    restoredAttempt.materialPosition.scrollTop = 999;

    expect(state).toMatchObject({
      serverSessionId: "session-1",
      planTaskId: "plan-task-1",
      contentVersionId: "bundle-v3",
      revision: 6,
      syncState: "saved",
    });
    expect(state.attempts.q1).toMatchObject({
      selectedOptionIds: ["B"],
      materialPosition: { scrollTop: 160 },
    });
  });

  it("preserves local answer evidence when a CAS save conflicts", () => {
    let state = createTaskRuntime({
      taskId: "session-cas",
      serverSessionId: "session-cas",
      mode: "practice",
      phase: "answering",
      questionOrder: ["q1"],
      revision: 4,
    });
    state = reduceTaskRuntime(state, { type: "ANSWER_SELECTED", optionIds: ["C"] });
    state = reduceTaskRuntime(state, { type: "CONFIDENCE_SET", confidence: "low" });
    const saving = reduceTaskRuntime(state, { type: "SAVE_REQUESTED" });
    expect(reduceTaskRuntime(saving, { type: "SAVE_REQUESTED" })).toBe(saving);

    const conflicted = reduceTaskRuntime(saving, {
      type: "REVISION_CONFLICT",
      requestId: "checkpoint-command-1",
    });

    expect(conflicted).toMatchObject({
      revision: 4,
      syncState: "conflict",
      requestId: "checkpoint-command-1",
    });
    expect(conflicted.attempts.q1).toMatchObject({
      selectedOptionIds: ["C"],
      confidence: "low",
    });
    expect(() => reduceTaskRuntime(conflicted, { type: "SAVE_SUCCEEDED", revision: 3 })).toThrow(
      TaskTransitionError,
    );
    expect(reduceTaskRuntime(conflicted, { type: "SAVE_SUCCEEDED", revision: 5 })).toMatchObject({
      revision: 5,
      syncState: "saved",
    });
  });

  it("requires every objective question to be answered or explicitly skipped", () => {
    let state = reduceTaskRuntime(
      createTaskRuntime({
        taskId: "session-submit",
        mode: "practice",
        phase: "briefing",
        questionOrder: ["q1", "q2"],
        revision: 1,
      }),
      { type: "TASK_STARTED", startedAt: "2026-07-15T04:00:00Z" },
    );
    state = reduceTaskRuntime(state, { type: "ANSWER_SELECTED", optionIds: ["A"] });
    state = reduceTaskRuntime(state, { type: "QUESTION_CHANGED", index: 1 });

    expect(() =>
      reduceTaskRuntime(state, {
        type: "TASK_RESULT_RECEIVED",
        receivedAt: "2026-07-15T04:05:00Z",
        reviewId: "review-1",
        causeDecisionRequired: false,
        revision: 2,
      }),
    ).toThrow(TaskTransitionError);

    state = reduceTaskRuntime(state, { type: "QUESTION_SKIPPED" });
    state = reduceTaskRuntime(state, {
      type: "TASK_RESULT_RECEIVED",
      receivedAt: "2026-07-15T04:05:00Z",
      reviewId: "review-1",
      causeDecisionRequired: false,
      revision: 2,
    });
    expect(state).toMatchObject({
      phase: "feedback",
      serverState: "reflection_required",
      revision: 2,
      syncState: "saved",
      reflectionRequired: true,
      exitPolicy: "blocked",
    });
  });

  it("advances all server writer revisions through reflection and cause decision", () => {
    let state = reduceTaskRuntime(
      createTaskRuntime({
        taskId: "session-review",
        serverSessionId: "session-review",
        mode: "practice",
        questionOrder: ["q1"],
        revision: 10,
      }),
      { type: "TASK_STARTED", startedAt: "2026-07-15T04:00:00Z" },
    );
    state = reduceTaskRuntime(state, { type: "QUESTION_SKIPPED" });
    state = reduceTaskRuntime(state, {
      type: "TASK_RESULT_RECEIVED",
      receivedAt: "2026-07-15T04:05:00Z",
      reviewId: "review-cause",
      causeDecisionRequired: true,
      revision: 11,
    });
    expect(state.revision).toBe(11);

    state = reduceTaskRuntime(state, { type: "PHASE_CHANGED", phase: "reflection" });
    expect(() =>
      reduceTaskRuntime(state, {
        type: "REFLECTION_COMMITTED",
        causeDecisionRequired: true,
        revision: 9,
      }),
    ).toThrow(TaskTransitionError);
    state = reduceTaskRuntime(state, {
      type: "REFLECTION_COMMITTED",
      causeDecisionRequired: true,
      revision: 12,
    });
    expect(state).toMatchObject({
      phase: "candidate_review",
      serverState: "cause_decision_required",
      revision: 12,
      syncState: "saved",
      reflectionCompleted: true,
      causeDecisionRequired: true,
      exitPolicy: "blocked",
    });
    expect(() =>
      reduceTaskRuntime(state, {
        type: "TASK_SUBMITTED",
        submittedAt: "2026-07-15T04:06:00Z",
      }),
    ).toThrow(TaskTransitionError);

    expect(() =>
      reduceTaskRuntime(state, { type: "CAUSE_DECISION_COMMITTED", revision: 11 }),
    ).toThrow(TaskTransitionError);
    state = reduceTaskRuntime(state, { type: "CAUSE_DECISION_COMMITTED", revision: 13 });
    expect(state).toMatchObject({
      phase: "summary",
      serverState: "completed",
      revision: 13,
      syncState: "saved",
      causeDecisionCompleted: true,
    });
    const completed = reduceTaskRuntime(state, {
      type: "TASK_SUBMITTED",
      submittedAt: "2026-07-15T04:06:00Z",
    });
    expect(completed).toMatchObject({
      phase: "submitted",
      serverState: "completed",
      revision: 13,
      syncState: "saved",
      exitPolicy: "immediate",
    });
  });

  it("still requires reflection when the result needs no cause decision", () => {
    let state = reduceTaskRuntime(
      createTaskRuntime({
        taskId: "session-correct",
        mode: "practice",
        questionOrder: ["q1"],
        revision: 1,
      }),
      { type: "TASK_STARTED", startedAt: "2026-07-15T04:00:00Z" },
    );
    state = reduceTaskRuntime(state, { type: "ANSWER_SELECTED", optionIds: ["B"] });
    state = reduceTaskRuntime(state, {
      type: "TASK_RESULT_RECEIVED",
      receivedAt: "2026-07-15T04:01:00Z",
      reviewId: "review-correct",
      causeDecisionRequired: false,
      revision: 2,
    });
    expect(() =>
      reduceTaskRuntime(state, {
        type: "TASK_SUBMITTED",
        submittedAt: "2026-07-15T04:02:00Z",
      }),
    ).toThrow(TaskTransitionError);

    state = reduceTaskRuntime(state, { type: "PHASE_CHANGED", phase: "reflection" });
    state = reduceTaskRuntime(state, {
      type: "REFLECTION_COMMITTED",
      causeDecisionRequired: false,
      revision: 3,
    });
    expect(state).toMatchObject({
      phase: "summary",
      serverState: "completed",
      revision: 3,
      reflectionCompleted: true,
      causeDecisionRequired: false,
    });
  });

  it("keeps selected and eliminated options disjoint and validates material positions", () => {
    let state = reduceTaskRuntime(
      createTaskRuntime({ taskId: "task-tools", mode: "practice", questionOrder: ["q1"] }),
      { type: "TASK_STARTED", startedAt: "2026-07-15T04:00:00Z" },
    );
    state = reduceTaskRuntime(state, { type: "ANSWER_SELECTED", optionIds: ["B"] });
    state = reduceTaskRuntime(state, {
      type: "OPTION_ELIMINATED",
      optionId: "B",
      eliminated: true,
    });
    expect(state.attempts.q1).toMatchObject({
      selectedOptionIds: [],
      eliminatedOptionIds: ["B"],
    });
    expect(() =>
      reduceTaskRuntime(state, {
        type: "MATERIAL_POSITION_CHANGED",
        position: { anchorId: "", scrollTop: 0, zoom: 1 },
      }),
    ).toThrow(TaskTransitionError);
  });
});
