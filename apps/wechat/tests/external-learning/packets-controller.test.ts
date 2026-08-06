import { describe, expect, it, vi } from "vitest";
import type { OfflinePacketVersion } from "../../miniprogram/api/external-learning-contracts";
import {
  LearningPacketsController,
  type LearningPacketsState,
} from "../../miniprogram/xingce/shared/features/external-learning/packets-controller";
import { gateway, packet, packetList } from "./fixtures";

describe("learning packets controller", () => {
  it("renders an explicit empty state when neither confirmed content nor packets exist", async () => {
    const controller = createController(
      gateway({
        listPackets: () => Promise.resolve(packetList({ packets: [], eligibleContent: [] })),
      }),
    );

    await controller.load();

    expect(controller.snapshot()).toMatchObject({
      status: "empty",
      packets: [],
      eligibleContent: [],
    });
  });

  it("creates only from selected confirmed versions and reuses the command after uncertainty", async () => {
    const createPacket = vi
      .fn()
      .mockRejectedValueOnce({ status: 0, message: "network", requestId: "request-create" })
      .mockResolvedValueOnce(packet({ state: "generating", artifactRef: null }));
    const controller = createController(gateway({ createPacket }));
    await controller.load();

    await controller.create();
    expect(controller.snapshot()).toMatchObject({ pendingRecovery: true, creating: false });
    await controller.create();

    expect(createPacket).toHaveBeenCalledTimes(2);
    expect(createPacket.mock.calls[0]?.[0]).toEqual({
      renderFormat: "pdf",
      expiresInDays: 14,
      items: [
        {
          questionId: "question-1",
          contentVersionId: "content-version-1",
          contentLicenseId: "11111111-1111-4111-8111-111111111111",
          expectedAuthorizationEpoch: 1,
          track: "xingce",
          components: ["question_prompt", "question_options"],
        },
      ],
    });
    expect(createPacket.mock.calls[1]?.[0]).toEqual(createPacket.mock.calls[0]?.[0]);
    expect(createPacket.mock.calls[1]?.[1]).toBe(createPacket.mock.calls[0]?.[1]);
    expect(controller.snapshot()).toMatchObject({
      pendingRecovery: false,
      packets: [{ state: "generating" }],
    });
  });

  it("locks packet creation to the confirmed content selected by the capture result", async () => {
    const createPacket = vi.fn(() => Promise.resolve(packet({ state: "generating" })));
    const [firstEligibleContent] = packetList().eligibleContent;
    expect(firstEligibleContent).toBeDefined();
    if (!firstEligibleContent) return;
    const controller = createController(
      gateway({
        createPacket,
        listPackets: () =>
          Promise.resolve(
            packetList({
              eligibleContent: [
                ...packetList().eligibleContent,
                {
                  ...firstEligibleContent,
                  questionId: "question-2",
                  contentVersionId: "content-version-2",
                  track: "essay",
                  title: "申论概括题",
                },
              ],
            }),
          ),
      }),
    );
    await controller.load();

    expect(controller.selectOnlyContent("content-version-2")).toBe(true);
    expect(controller.snapshot()).toMatchObject({
      track: "essay",
      selectedContentVersionIds: ["content-version-2"],
    });
    await controller.create();

    expect(createPacket).toHaveBeenCalledWith(
      expect.objectContaining({
        items: [expect.objectContaining({ contentVersionId: "content-version-2", track: "essay" })],
      }),
      expect.any(String),
    );
  });

  it("does not silently replace an unavailable capture result with unrelated content", async () => {
    const controller = createController();
    await controller.load();

    expect(controller.selectOnlyContent("missing-content-version")).toBe(false);
    expect(controller.snapshot().validationError).toContain("不能生成学习包");
  });

  it("creates a new version for failed and expired packets with distinct legal commands", async () => {
    const failed = packet({ state: "failed", artifactRef: null });
    const expired = packet({
      packetId: "packet-2",
      packetVersionId: "packet-version-2",
      state: "expired",
      artifactRef: null,
    });
    const retryPacket = vi.fn(() => Promise.resolve(nextVersion(failed)));
    const renewPacket = vi.fn(() => Promise.resolve(nextVersion(expired)));
    const controller = createController(
      gateway({
        listPackets: () => Promise.resolve(packetList({ packets: [failed, expired] })),
        retryPacket,
        renewPacket,
      }),
    );
    await controller.load();

    await controller.operate("packet-version-1", "retry");
    await controller.operate("packet-version-2", "renew");

    expect(retryPacket).toHaveBeenCalledWith(
      { packetId: "packet-1", failedPacketVersionId: "packet-version-1", expectedVersionNo: 1 },
      expect.stringContaining("external-packet-retry"),
    );
    expect(renewPacket).toHaveBeenCalledWith(
      { packetId: "packet-2", expiredPacketVersionId: "packet-version-2", expectedVersionNo: 1 },
      expect.stringContaining("external-packet-renew"),
    );
  });

  it("opens only ready packet versions through a short-lived access grant", async () => {
    const openFile = vi.fn(() => Promise.resolve());
    const getPacketAccess = vi.fn(() =>
      Promise.resolve({
        packetVersionId: "packet-version-1",
        localFilePath: "/tmp/packet.pdf",
        fileType: "pdf" as const,
        expiresAt: "2026-07-15T03:00:00Z",
      }),
    );
    const controller = createController(gateway({ getPacketAccess }), openFile);
    await controller.load();
    await controller.open("packet-version-1");

    expect(getPacketAccess).toHaveBeenCalledWith("packet-version-1");
    expect(openFile).toHaveBeenCalledWith("/tmp/packet.pdf", "pdf");
  });
});

function createController(
  api = gateway(),
  openFile: (path: string, type: "pdf" | "note_app_file") => Promise<void> = () =>
    Promise.resolve(),
) {
  let current: LearningPacketsState | undefined;
  return new LearningPacketsController({
    gateway: api,
    onOpenFile: openFile,
    onChange: (state) => {
      current = state;
      void current;
    },
    createCommandKey: (prefix) => Promise.resolve(`${prefix}-command`),
  });
}

function nextVersion(value: OfflinePacketVersion): OfflinePacketVersion {
  return {
    ...value,
    packetVersionId: `${value.packetVersionId}-next`,
    versionNo: value.versionNo + 1,
    state: "generating",
    artifactRef: null,
  };
}
