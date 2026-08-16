import { describe, expect, it, vi } from "vitest";

import { CaptureImportController } from "../../../miniprogram/pages/capture-import/features/external-learning/capture-controller";
import type { LocalImportFile } from "../../../miniprogram/features/external-learning/contracts";
import {
  gateway,
  importWorkspace,
  questionUnit,
  recognition,
} from "../../external-learning/fixtures";

describe("full product audit repair 033a replay path", () => {
  it("replays uncertain recognition confirmation with the same idempotency key", async () => {
    const confirmedWorkspace = importWorkspace({
      recognition: recognition({
        state: "confirmed",
        confirmedAt: "2026-08-16T02:00:00Z",
      }),
      questionUnits: [questionUnit()],
    });
    const confirmRecognition = vi
      .fn()
      .mockRejectedValueOnce({ status: 0, message: "network", requestId: "request-confirm" })
      .mockResolvedValueOnce(confirmedWorkspace);
    const controller = new CaptureImportController({
      gateway: gateway({ confirmRecognition }),
      onChange: vi.fn(),
      createCommandKey: () => Promise.resolve("stable-confirm-command"),
    });

    await controller.startImport(image());
    await controller.confirm();
    expect(controller.snapshot()).toMatchObject({
      status: "error",
      pendingRecovery: true,
      pendingRecoveryKind: "confirmation",
    });

    await controller.retryPendingOperation();

    expect(confirmRecognition).toHaveBeenCalledTimes(2);
    expect(confirmRecognition).toHaveBeenNthCalledWith(
      1,
      expect.any(Object),
      "stable-confirm-command:recognition",
    );
    expect(confirmRecognition).toHaveBeenNthCalledWith(
      2,
      expect.any(Object),
      "stable-confirm-command:recognition",
    );
    expect(controller.snapshot()).toMatchObject({
      status: "review",
      reviewStep: "match",
      pendingRecovery: false,
      pendingRecoveryKind: null,
    });
  });
});

function image(): LocalImportFile {
  return {
    localPath: "/tmp/question.jpg",
    displayName: "question.jpg",
    sourceKind: "camera_image",
    mimeType: "image/jpeg",
    byteSize: 1_024,
    pageCount: null,
  };
}
