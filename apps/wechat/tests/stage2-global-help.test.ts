import { describe, expect, it, vi } from "vitest";

import type { ApiClient } from "../miniprogram/api/client";
import {
  TaskHelpApi,
  taskHelpWirePayload,
  type TaskHelpCommand,
} from "../miniprogram/api/task-help";
import { createTaskHelpCommand } from "../miniprogram/task-runtime/global-help/contracts";

const mockReference = {
  pageCode: "F02",
  route: "/pages/mock-exam/index",
  track: "xingce",
  objectType: "task_session",
  objectId: "mock-session-1",
  taskMode: "mock",
  lifecycle: "answering",
} as const;

describe("stage 2 global task help", () => {
  it("builds pre-submit mock help from operation/fault codes and page object references", () => {
    const command = createTaskHelpCommand(
      { pageObjectReference: mockReference, syncState: "pending", requestId: "request-1" },
      "fault_timer",
      "2026-07-15T02:00:00.000Z",
    );

    expect(command).toEqual({
      category: "fault",
      issueCode: "fault_timer",
      pageObjectReference: mockReference,
      diagnostics: {
        clientTime: "2026-07-15T02:00:00.000Z",
        syncState: "pending",
        requestId: "request-1",
      },
    });
  });

  it("serializes an explicit allowlist and strips question or answer-shaped extras", () => {
    const unsafeRuntimeObject = {
      ...createTaskHelpCommand(
        { pageObjectReference: mockReference, syncState: "saved" },
        "operation_submit",
        "2026-07-15T02:00:00.000Z",
      ),
      stem: "不应上传的题干",
      options: ["A", "B"],
      selectedAnswer: "A",
      correctAnswer: "B",
      explanation: "不应上传的解析",
      draftText: "不应上传的草稿",
    } as unknown as TaskHelpCommand;

    const wire = taskHelpWirePayload(unsafeRuntimeObject);
    const serialized = JSON.stringify(wire);

    expect(serialized).not.toContain("题干");
    expect(serialized).not.toContain("options");
    expect(serialized).not.toContain("selectedAnswer");
    expect(serialized).not.toContain("correctAnswer");
    expect(serialized).not.toContain("explanation");
    expect(serialized).not.toContain("draftText");
    expect(wire).toEqual({ sessionId: "mock-session-1", mode: "mock", scope: "operation" });
  });

  it("sends only the sanitized wire payload to the help service", async () => {
    const post = vi.fn().mockResolvedValue({
      data: {
        sessionId: "mock-session-1",
        mode: "mock",
        scope: "operation",
        sessionState: "assigned",
        policyVersion: "task-help-learner-safe-v1",
        title: "交卷操作",
        steps: ["点击底部交卷并确认。"],
        permittedTools: ["navigation_help"],
        blockedCapabilities: [
          "current_question_answer",
          "answer_analysis",
          "formula_card",
          "solution_ai",
        ],
        contentLookupPerformed: false,
      },
      requestId: "request-help",
      generatedAt: "2026-07-15T02:00:00.000Z",
    });
    const api = new TaskHelpApi({ post } as unknown as ApiClient);
    const command = createTaskHelpCommand(
      { pageObjectReference: mockReference, syncState: "saved" },
      "operation_submit",
      "2026-07-15T02:00:00.000Z",
    );

    await api.submit(command, "help-command-1");

    expect(post).toHaveBeenCalledWith(
      "/api/v2/task-help",
      { sessionId: "mock-session-1", mode: "mock", scope: "operation" },
      { idempotencyKey: "help-command-1" },
    );
  });
});
