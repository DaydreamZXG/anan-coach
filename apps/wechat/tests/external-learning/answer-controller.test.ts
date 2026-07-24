import { describe, expect, it, vi } from "vitest";
import {
  AnswerIngestionController,
  MAX_ANSWER_IMAGE_BYTES,
  parseAnswerString,
  validateAnswerImage,
} from "../../miniprogram/features/external-learning/answer-controller";
import type { LocalAnswerImageFile } from "../../miniprogram/features/external-learning/contracts";
import { answerWorkspace, gateway, ingestion } from "./fixtures";

describe("answer ingestion controller", () => {
  it("parses deterministic answer strings and rejects invalid or duplicate choices", () => {
    expect(parseAnswerString("acbe")).toEqual(["A", "C", "B", "E"]);
    expect(parseAnswerString("a, C; b / e")).toEqual(["A", "C", "B", "E"]);
    expect(parseAnswerString("A, AX")).toContain("第 2 个");
  });

  it("enforces answer image type and size before upload", () => {
    expect(validateAnswerImage(answerImage())).toBe("");
    expect(validateAnswerImage(answerImage({ byteSize: MAX_ANSWER_IMAGE_BYTES + 1 }))).toContain(
      "20 MB",
    );
  });

  it("creates a blocked candidate, supports correction, then atomically confirms and commits", async () => {
    const createAnswerString = vi.fn(() => Promise.resolve(answerWorkspace()));
    const reviseAnswer = vi.fn(() =>
      Promise.resolve(answerWorkspace({ ingestion: ingestion({ versionNo: 2 }) })),
    );
    const confirmAnswer = vi.fn(() =>
      Promise.resolve(
        answerWorkspace({
          ingestion: ingestion({
            versionNo: 3,
            state: "committed",
            confirmedAt: "2026-07-15T04:00:00Z",
            committedFactRef: "xingce-fact-1",
          }),
          diagnosticEligibility: "eligible_committed",
        }),
      ),
    );
    const controller = createController(
      gateway({ createAnswerString, reviseAnswer, confirmAnswer }),
    );
    await controller.loadTargets();
    controller.setAnswerText("A");
    await controller.createCandidate();

    expect(controller.snapshot()).toMatchObject({
      status: "correction",
      workspace: { diagnosticEligibility: "blocked_unconfirmed" },
    });
    controller.updateCandidate("answer-item-1", "B");
    controller.requestConfirmation();
    await controller.confirmAndCommit();

    expect(reviseAnswer).toHaveBeenCalledWith(
      {
        ingestionId: "ingestion-1",
        expectedVersionNo: 1,
        answers: [{ itemId: "answer-item-1", answerBindingCode: "answer-binding-1", answer: "B" }],
      },
      "external-answer-revision-command",
    );
    expect(confirmAnswer).toHaveBeenCalledWith(
      { ingestionId: "ingestion-1", expectedVersionNo: 2 },
      "external-answer-confirm-command",
    );
    expect(controller.snapshot()).toMatchObject({
      status: "success",
      workspace: {
        diagnosticEligibility: "eligible_committed",
        ingestion: { committedFactRef: "xingce-fact-1" },
      },
    });
  });

  it("reuses the candidate command after an uncertain create response", async () => {
    const createAnswerString = vi
      .fn()
      .mockRejectedValueOnce({ status: 0, message: "network", requestId: "request-answer" })
      .mockResolvedValueOnce(answerWorkspace());
    const controller = createController(gateway({ createAnswerString }));
    await controller.loadTargets();
    controller.setAnswerText("A, B");

    await controller.createCandidate();
    expect(controller.snapshot()).toMatchObject({ status: "error", pendingRecovery: true });
    controller.setAnswerText("C, D");
    await controller.createCandidate();

    expect(createAnswerString).toHaveBeenCalledTimes(2);
    expect(createAnswerString.mock.calls[1]?.[0]).toEqual(createAnswerString.mock.calls[0]?.[0]);
    expect(createAnswerString.mock.calls[1]?.[1]).toBe(createAnswerString.mock.calls[0]?.[1]);
    expect(controller.snapshot()).toMatchObject({ status: "correction", pendingRecovery: false });
  });

  it("locks duplicate submission while the first candidate request is in flight", async () => {
    let complete!: (value: ReturnType<typeof answerWorkspace>) => void;
    const pending = new Promise<ReturnType<typeof answerWorkspace>>((resolve) => {
      complete = resolve;
    });
    const createAnswerString = vi.fn(() => pending);
    const controller = createController(gateway({ createAnswerString }));
    await controller.loadTargets();
    controller.setAnswerText("A");

    const first = controller.createCandidate();
    await Promise.resolve();
    const second = controller.createCandidate();
    complete(answerWorkspace());
    await Promise.all([first, second]);

    expect(createAnswerString).toHaveBeenCalledTimes(1);
  });

  it("keeps a rejected ingestion out of diagnosis and exposes an expired recovery state", async () => {
    const controller = createController(
      gateway({
        getAnswerIngestion: () =>
          Promise.resolve(
            answerWorkspace({
              ingestion: ingestion({ state: "rejected" }),
              diagnosticEligibility: "blocked_unconfirmed",
            }),
          ),
      }),
    );

    await controller.loadIngestion("ingestion-1");

    expect(controller.snapshot()).toMatchObject({
      status: "expired",
      workspace: { diagnosticEligibility: "blocked_unconfirmed" },
    });
  });
});

function createController(api = gateway()) {
  return new AnswerIngestionController({
    gateway: api,
    onChange: vi.fn(),
    createCommandKey: (prefix) => Promise.resolve(`${prefix}-command`),
  });
}

function answerImage(overrides: Partial<LocalAnswerImageFile> = {}): LocalAnswerImageFile {
  return {
    localPath: "/tmp/answer.jpg",
    displayName: "answer.jpg",
    sourceKind: "answer_sheet",
    mimeType: "image/jpeg",
    byteSize: 1_024,
    ...overrides,
  };
}
