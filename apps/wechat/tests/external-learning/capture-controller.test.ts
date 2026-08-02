import { afterEach, describe, expect, it, vi } from "vitest";
import {
  CaptureImportController,
  MAX_IMPORT_IMAGE_BYTES,
  type CaptureImportState,
} from "../../miniprogram/features/external-learning/capture-controller";
import type { LocalImportFile } from "../../miniprogram/features/external-learning/contracts";
import { contentMatch, gateway, importWorkspace, questionUnit, recognition } from "./fixtures";

afterEach(() => {
  vi.useRealTimers();
});

describe("capture import controller", () => {
  it("prefers the confirmed catalog question over OCR segments", async () => {
    const getImport = vi.fn(() =>
      Promise.resolve(
        importWorkspace({
          state: "ready",
          recognition: recognition({ state: "confirmed", confirmedAt: "2026-07-15T02:00:00Z" }),
          recognitionSegments: [
            {
              segmentId: "prompt",
              pageNo: 1,
              ordinal: 1,
              text: "题干",
              confidence: 0.9,
              state: "confirmed",
              requiresCorrection: false,
            },
            {
              segmentId: "options",
              pageNo: 1,
              ordinal: 2,
              text: "A 选项一\nB 选项二",
              confidence: 0.9,
              state: "confirmed",
              requiresCorrection: false,
            },
            {
              segmentId: "material",
              pageNo: 1,
              ordinal: 3,
              text: "材料",
              confidence: 0.9,
              state: "confirmed",
              requiresCorrection: false,
            },
          ],
          questionUnits: [
            questionUnit({
              state: "ready",
              segmentIds: ["material", "prompt", "options"],
              matchedContent: {
                contentVersionId: "content-version-1",
                promptText: "题库完整题干",
                optionTexts: ["A、题库选项一", "B、题库选项二"],
                materialTexts: ["题库材料"],
                solutionExplanation: "题库解析",
              },
            }),
          ],
          diagnosticEligibility: "eligible_confirmed",
        }),
      ),
    );
    const controller = new CaptureImportController({
      gateway: gateway({ getImport }),
      onChange: vi.fn(),
    });

    await controller.load("import-1");

    expect(controller.snapshot().solutionSegments.map((segment) => segment.segmentId)).toEqual([
      "material",
      "prompt",
      "options",
    ]);
    expect(controller.snapshot().activeQuestionUnit?.matchedContent?.promptText).toBe(
      "题库完整题干",
    );
    expect(controller.snapshot().activeQuestionUnit?.matchedContent?.solutionExplanation).toBe(
      "题库解析",
    );
  });

  it("rejects oversized images before upload", async () => {
    const api = gateway();
    const beginImport = vi.spyOn(api, "beginImport");
    let current: CaptureImportState | undefined;
    const controller = new CaptureImportController({
      gateway: api,
      onChange: (state) => {
        current = state;
      },
    });

    await controller.startImport(image({ byteSize: MAX_IMPORT_IMAGE_BYTES + 1 }));

    expect(beginImport).not.toHaveBeenCalled();
    expect(current?.validationError).toContain("20 MB");
  });

  it("publishes progress, polls processing, and keeps candidates blocked from diagnosis", async () => {
    vi.useFakeTimers();
    const getImport = vi.fn(() => Promise.resolve(importWorkspace()));
    const api = gateway({
      beginImport: (_command, _key, onProgress) => {
        onProgress({ sentBytes: 512, totalBytes: 1_024, percent: 50 });
        return Promise.resolve(
          importWorkspace({
            state: "processing",
            recognition: null,
            recognitionSegments: [],
            questionUnits: [],
            processingLabel: "正在执行印刷体 OCR",
            pollAfterMs: 800,
          }),
        );
      },
      getImport,
    });
    const history: CaptureImportState[] = [];
    const controller = new CaptureImportController({
      gateway: api,
      onChange: (state) => history.push(state),
      createCommandKey: () => Promise.resolve("import-command-1"),
    });

    await controller.startImport(image());
    expect(history.some((state) => state.uploadProgress?.percent === 50)).toBe(true);
    expect(controller.snapshot()).toMatchObject({ status: "processing" });

    await vi.advanceTimersByTimeAsync(800);
    expect(getImport).toHaveBeenCalledWith("import-1");
    expect(controller.snapshot()).toMatchObject({
      status: "review",
      workspace: { diagnosticEligibility: "blocked_unconfirmed" },
    });
  });

  it("confirms corrected OCR before exposing and separately confirming a content match", async () => {
    const reviseRecognition = vi.fn(() =>
      Promise.resolve(
        importWorkspace({
          recognition: recognition({ recognitionRevisionId: "recognition-2", revisionNo: 2 }),
        }),
      ),
    );
    const confirmRecognition = vi.fn(() =>
      Promise.resolve(
        importWorkspace({
          recognition: recognition({
            recognitionRevisionId: "recognition-2",
            revisionNo: 2,
            state: "confirmed",
            confirmedAt: "2026-07-15T02:00:00Z",
          }),
          questionUnits: [questionUnit()],
        }),
      ),
    );
    const confirmMatch = vi.fn(() =>
      Promise.resolve(
        importWorkspace({
          state: "ready",
          recognition: recognition({ state: "confirmed", confirmedAt: "2026-07-15T02:00:00Z" }),
          questionUnits: [
            questionUnit({
              state: "ready",
              match: contentMatch({
                state: "confirmed",
                confirmedContentVersionId: "content-version-1",
              }),
              referenceAnswer: {
                sourceType: "authorized_reference",
                matchKind: "exact",
                answerText: "A",
                contentVersionId: "content-version-1",
                authorizationState: "authorized_current",
                learningImpact: "reference_only",
              },
            }),
          ],
          diagnosticEligibility: "eligible_confirmed",
        }),
      ),
    );
    const createCommandKey = vi.fn((prefix: string) => Promise.resolve(`${prefix}-key`));
    const controller = new CaptureImportController({
      gateway: gateway({ reviseRecognition, confirmRecognition, confirmMatch }),
      onChange: vi.fn(),
      createCommandKey,
    });

    await controller.startImport(image());
    controller.updateSegment("segment-1", "修正后的题目文本");
    await controller.confirm();

    expect(reviseRecognition).toHaveBeenCalledWith(
      expect.objectContaining({
        recognitionRevisionId: "recognition-1",
        expectedRevisionNo: 1,
        segments: [{ segmentId: "segment-1", text: "修正后的题目文本" }],
      }),
      "external-confirm-key:revision",
    );
    expect(confirmRecognition).toHaveBeenCalledWith(
      expect.objectContaining({ recognitionRevisionId: "recognition-2", expectedRevisionNo: 2 }),
      "external-confirm-key:recognition",
    );
    expect(confirmMatch).not.toHaveBeenCalled();
    expect(controller.snapshot()).toMatchObject({
      status: "review",
      reviewStep: "match",
      workspace: { diagnosticEligibility: "blocked_unconfirmed" },
    });

    await controller.confirm();

    expect(confirmMatch).toHaveBeenCalledWith(
      expect.objectContaining({ confirmedContentVersionId: "content-version-1" }),
      "external-confirm-key:match",
    );
    expect(
      createCommandKey.mock.calls.filter(([prefix]) => prefix === "external-confirm"),
    ).toHaveLength(1);
    expect(controller.snapshot()).toMatchObject({
      status: "success",
      workspace: {
        diagnosticEligibility: "eligible_confirmed",
        questionUnits: [{ referenceAnswer: { answerText: "A", learningImpact: "reference_only" } }],
      },
    });
  });

  it("requests and confirms an unmatched AI answer without treating it as diagnostic evidence", async () => {
    const unmatched = questionUnit({
      state: "unmatched",
      match: contentMatch({ candidates: [], evaluationReason: "no_eligible_candidate" }),
    });
    const candidate = questionUnit({
      ...unmatched,
      aiAnswer: {
        answerVersionId: "ai-answer-version-1",
        state: "candidate",
        answerText: "A",
        explanation: "依据上传题面给出估算解析。",
        keyPoints: ["核对题干条件"],
        confidence: "medium",
        limitations: ["不是官方答案"],
        outputFingerprint: "e".repeat(64),
        failureCode: null,
        sourceType: "ai_estimated",
        learningImpact: "reference_only",
      },
    });
    const candidateAnswer = candidate.aiAnswer;
    if (!candidateAnswer) throw new Error("Expected an AI answer candidate in test setup");
    const confirmed = questionUnit({
      ...candidate,
      state: "ready",
      aiAnswer: { ...candidateAnswer, state: "confirmed" },
    });
    const requestAIAnswer = vi.fn(() => Promise.resolve());
    const confirmAIAnswer = vi.fn(() => Promise.resolve());
    const getImport = vi
      .fn()
      .mockResolvedValueOnce(
        importWorkspace({
          recognition: recognition({ state: "confirmed", confirmedAt: "2026-07-15T02:00:00Z" }),
          questionUnits: [candidate],
        }),
      )
      .mockResolvedValueOnce(
        importWorkspace({
          state: "ready",
          recognition: recognition({ state: "confirmed", confirmedAt: "2026-07-15T02:00:00Z" }),
          questionUnits: [confirmed],
          diagnosticEligibility: "blocked_unconfirmed",
        }),
      );
    const controller = new CaptureImportController({
      gateway: gateway({
        beginImport: () =>
          Promise.resolve(
            importWorkspace({
              recognition: recognition({
                state: "confirmed",
                confirmedAt: "2026-07-15T02:00:00Z",
              }),
              questionUnits: [unmatched],
            }),
          ),
        requestAIAnswer,
        confirmAIAnswer,
        getImport,
      }),
      onChange: vi.fn(),
      createCommandKey: (prefix) => Promise.resolve(`${prefix}-key`),
    });

    await controller.startImport(image());
    await controller.requestAIAnswer();
    expect(requestAIAnswer).toHaveBeenCalledWith(
      {
        importId: "import-1",
        questionUnitId: "d".repeat(64),
        learningTrack: "xingce",
      },
      "external-ai-answer-key",
    );
    expect(controller.snapshot().activeQuestionUnit?.aiAnswer?.state).toBe("candidate");

    await controller.confirmAIAnswer();
    expect(confirmAIAnswer).toHaveBeenCalledWith(
      {
        answerVersionId: "ai-answer-version-1",
        expectedOutputFingerprint: "e".repeat(64),
      },
      "external-ai-answer-confirm-key",
    );
    expect(controller.snapshot()).toMatchObject({
      status: "success",
      workspace: { diagnosticEligibility: "blocked_unconfirmed" },
      activeQuestionUnit: { state: "ready", aiAnswer: { state: "confirmed" } },
    });
  });

  it("reuses the exact upload command after an uncertain result", async () => {
    const beginImport = vi
      .fn()
      .mockRejectedValueOnce({ status: 0, message: "network", requestId: "request-1" })
      .mockResolvedValueOnce(importWorkspace());
    const controller = new CaptureImportController({
      gateway: gateway({ beginImport }),
      onChange: vi.fn(),
      createCommandKey: () => Promise.resolve("stable-import-command"),
    });

    await controller.startImport(image());
    expect(controller.snapshot()).toMatchObject({ status: "error", pendingRecovery: true });
    await controller.retryPendingImport();

    expect(beginImport).toHaveBeenCalledTimes(2);
    expect(beginImport.mock.calls[1]?.[0]).toEqual(beginImport.mock.calls[0]?.[0]);
    expect(beginImport.mock.calls[1]?.[1]).toBe("stable-import-command");
    expect(controller.snapshot()).toMatchObject({ status: "review", pendingRecovery: false });
  });

  it("starts a new upload command when the user selects a different file after uncertainty", async () => {
    const beginImport = vi
      .fn()
      .mockRejectedValueOnce({ status: 0, message: "network", requestId: "request-1" })
      .mockResolvedValueOnce(importWorkspace());
    let key = 0;
    const controller = new CaptureImportController({
      gateway: gateway({ beginImport }),
      onChange: vi.fn(),
      createCommandKey: () => Promise.resolve(`import-command-${String(++key)}`),
    });

    await controller.startImport(image());
    const replacement = {
      ...image(),
      localPath: "/tmp/question-retaken.jpg",
      displayName: "question-retaken.jpg",
    };
    await controller.startImport(replacement);

    expect(beginImport).toHaveBeenCalledTimes(2);
    expect(beginImport.mock.calls[0]?.[1]).toBe("import-command-1");
    expect(beginImport.mock.calls[1]?.[0]).toEqual({
      file: replacement,
      learningTrack: "xingce",
    });
    expect(beginImport.mock.calls[1]?.[1]).toBe("import-command-2");
    expect(controller.snapshot()).toMatchObject({ status: "review", pendingRecovery: false });
  });

  it("starts a clean capture after reset and ignores the previous operation result", async () => {
    let resolve!: (value: ReturnType<typeof importWorkspace>) => void;
    const pending = new Promise<ReturnType<typeof importWorkspace>>((complete) => {
      resolve = complete;
    });
    const controller = new CaptureImportController({
      gateway: gateway({ beginImport: () => pending }),
      onChange: vi.fn(),
      createCommandKey: () => Promise.resolve("import-command-reset"),
    });
    controller.setTrack("essay");

    const submission = controller.startImport(image());
    await Promise.resolve();
    controller.reset();
    expect(controller.snapshot()).toMatchObject({
      status: "idle",
      learningTrack: "essay",
      selectedFile: null,
      workspace: null,
    });

    resolve(importWorkspace({ state: "ready" }));
    await submission;
    expect(controller.snapshot()).toMatchObject({ status: "idle", workspace: null });
  });

  it("does not publish a late upload response after disposal", async () => {
    let resolve!: (value: ReturnType<typeof importWorkspace>) => void;
    const pending = new Promise<ReturnType<typeof importWorkspace>>((complete) => {
      resolve = complete;
    });
    const onChange = vi.fn();
    const controller = new CaptureImportController({
      gateway: gateway({ beginImport: () => pending }),
      onChange,
      createCommandKey: () => Promise.resolve("import-command-late"),
    });
    const submission = controller.startImport(image());
    await Promise.resolve();
    controller.dispose();
    const callsAtDispose = onChange.mock.calls.length;
    resolve(importWorkspace());
    await submission;

    expect(onChange).toHaveBeenCalledTimes(callsAtDispose);
  });
});

function image(overrides: Partial<LocalImportFile> = {}): LocalImportFile {
  return {
    localPath: "/tmp/question.jpg",
    displayName: "question.jpg",
    sourceKind: "camera_image",
    mimeType: "image/jpeg",
    byteSize: 1_024,
    pageCount: null,
    ...overrides,
  };
}
