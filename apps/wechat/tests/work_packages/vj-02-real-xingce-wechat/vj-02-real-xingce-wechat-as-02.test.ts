import { describe, expect, it } from "vitest";

import {
  checkpointCommand,
  runtimeFromXingceSession,
} from "../../../miniprogram/features/xingce/real_attempt/controller";
import { assertLearnerSafeSession } from "../../../miniprogram/features/xingce/real_attempt/contracts";
import {
  presentXingceResult,
  presentXingceTask,
} from "../../../miniprogram/features/xingce/real_attempt/presentation";
import {
  reduceTaskRuntime,
  unansweredQuestionIds,
} from "../../../miniprogram/task-runtime/state/runtime";
import { activeXingceSession, answer } from "./harness";

describe("VJ-02 real xingce WeChat AS-02", () => {
  it("renders single and shared-material bundles without exposing answers before submission", () => {
    const single = activeXingceSession();
    const singleRuntime = runtimeFromXingceSession(single);
    const singleView = presentXingceTask(single, singleRuntime);
    expect(singleView.materialAvailable).toBe(false);
    expect(singleView.sharedMaterial).toBe(false);
    expect(singleView.unansweredCount).toBe(2);
    expect(presentXingceResult(single)).toBeNull();

    const shared = activeXingceSession({ sharedMaterial: true });
    const sharedView = presentXingceTask(shared, runtimeFromXingceSession(shared));
    expect(sharedView.materialAvailable).toBe(true);
    expect(sharedView.sharedMaterial).toBe(true);
    expect(sharedView.question.material).toMatchObject({
      materialId: "material-1",
      title: "2025 年公共服务材料",
    });
    expect(sharedView.question.material?.assets[0]?.altText).toBe("两地公共服务数据表");
    expect(Object.keys(shared.bundle.questions[0] ?? {})).not.toEqual(
      expect.arrayContaining(["answer", "correctAnswer", "correctOptionIds", "explanation"]),
    );
  });

  it("keeps unanswered and explicit skipped answers distinct in runtime and checkpoint", () => {
    const session = activeXingceSession({ answers: [answer("q-1", ["B"])] });
    let runtime = runtimeFromXingceSession(session);
    expect(unansweredQuestionIds(runtime)).toEqual(["q-2"]);
    runtime = reduceTaskRuntime(runtime, { type: "QUESTION_CHANGED", index: 1 });
    runtime = reduceTaskRuntime(runtime, { type: "QUESTION_SKIPPED" });

    expect(unansweredQuestionIds(runtime)).toEqual([]);
    const view = presentXingceTask(session, runtime);
    expect(view).toMatchObject({ answeredCount: 1, skippedCount: 1, unansweredCount: 0 });
    expect(checkpointCommand(runtime).answers).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ questionId: "q-1", selectedOptionIds: ["B"], skipped: false }),
        expect.objectContaining({ questionId: "q-2", selectedOptionIds: [], skipped: true }),
      ]),
    );
  });

  it("stops rendering when a pre-submit projection contains an answer or explanation", () => {
    const contaminated = activeXingceSession();
    Object.assign(contaminated.bundle.questions[0] ?? {}, {
      correctOptionIds: ["A"],
      explanation: "不应在交卷前出现",
    });

    expect(() => assertLearnerSafeSession(contaminated)).toThrow("答案或解析");
  });
});
