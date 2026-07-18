import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiClientError } from "../miniprogram/api/client";
import type {
  RetakeEnrollmentResult,
  RetakeWorkspace,
} from "../miniprogram/api/generated/types.gen";
import type { RetakeApi } from "../miniprogram/api/retake";
import {
  RetakeController,
  type RetakeControllerState,
} from "../miniprogram/features/retake/controller";
import { loadPendingRetakeProgram } from "../miniprogram/features/retake/pending-store";

type Api = Pick<RetakeApi, "getWorkspace" | "startProgram">;
let storage: Map<string, unknown>;

beforeEach(() => {
  storage = new Map();
  vi.stubGlobal("wx", {
    getStorageSync: (key: string) => storage.get(key) ?? "",
    setStorageSync: (key: string, value: unknown) => storage.set(key, clone(value)),
    removeStorageSync: (key: string) => storage.delete(key),
  });
});

describe("retake controller", () => {
  it("selects owned official evidence and persists uncertain program creation", async () => {
    const startProgram = vi
      .fn()
      .mockRejectedValueOnce(new ApiClientError("网络请求失败。", 0, "request-1"))
      .mockResolvedValueOnce(envelope(created()));
    let current: RetakeControllerState | undefined;
    const controller = new RetakeController({
      userId: "user-a",
      api: api({ startProgram }),
      onChange: (state) => {
        current = state;
      },
      createCommandKey: () => Promise.resolve("retake-command-0001"),
    });

    await controller.load();
    expect(current?.selectedEvidenceId).toBe("evidence-2024");
    await controller.startProgram();
    const pending = loadPendingRetakeProgram("user-a");
    expect(current?.pendingRecovery).toBe(true);
    expect(pending?.evidenceId).toBe("evidence-2024");

    await controller.startProgram();
    expect(startProgram).toHaveBeenNthCalledWith(
      2,
      { sourceScoreEvidenceId: "evidence-2024" },
      "retake-command-0001",
    );
    expect(current?.program?.targetExamYear).toBe(2026);
    expect(loadPendingRetakeProgram("user-a")).toBeNull();
  });
});

function api(overrides: Partial<Api> = {}): Api {
  return {
    getWorkspace: () => Promise.resolve(envelope(workspace())),
    startProgram: () => Promise.resolve(envelope(created())),
    ...overrides,
  };
}

function workspace(): RetakeWorkspace {
  return {
    targetExamYear: 2026,
    eligibleScoreEvidence: [
      {
        evidenceId: "evidence-2024",
        examYear: 2024,
        sourceType: "self_report",
        xingceScore: 61,
        essayScore: 54,
        totalScore: 115,
        occurredAt: "2024-12-01T00:00:00Z",
      },
    ],
    program: null,
    scoreUseBoundary: "只用于失分结构，不参与退款。",
  };
}

function created(): RetakeEnrollmentResult {
  return {
    program: {
      programVersionId: "program-1",
      sourceScoreEvidenceId: "evidence-2024",
      sourceExamYear: 2024,
      targetExamYear: 2026,
      state: "active",
      lossStructure: {
        xingceScore: 61,
        essayScore: 54,
        availableTracks: ["essay", "xingce"],
        trainingFocus: ["先复原申论失分", "再拆行测六模块"],
        limitation: "不直接计算跨年提分。",
      },
      refundUsesScore: false,
      rewardUsesScore: false,
      trainingUsesScore: true,
      createdAt: "2026-07-18T00:00:00Z",
    },
  };
}

function envelope<T>(data: T) {
  return { data, requestId: "request-test", generatedAt: "2026-07-18T00:00:00Z" };
}

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}
