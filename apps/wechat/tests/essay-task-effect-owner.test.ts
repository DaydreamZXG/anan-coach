import { describe, expect, it, vi } from "vitest";

import {
  EssayTaskEffectOwner,
  type EssayTaskEffectLane,
} from "../miniprogram/pages/essay-task/features/page-host/effect-owner";
import { cancelledVisibleEffectPatch } from "../miniprogram/pages/essay-task/features/page-host/transient-state";

const sessionA = {
  userId: "user-a",
  accessToken: "token-a",
  generation: "generation-a",
};

describe("essay task per-lane effect owner", () => {
  it("keeps review polling and voice effects current concurrently", () => {
    const page = {};
    const owner = visibleOwner(page);
    const poll = owner.begin(page, "review-poll", sessionA);
    const voice = owner.begin(page, "voice", sessionA);

    expect(owner.owns(page, poll, sessionA)).toBe(true);
    expect(owner.owns(page, voice, sessionA)).toBe(true);
  });

  it("replaces only an older effect in the same lane", () => {
    const page = {};
    const owner = visibleOwner(page);
    const workspace = owner.begin(page, "workspace", sessionA);
    const firstVoice = owner.begin(page, "voice", sessionA);
    const latestVoice = owner.begin(page, "voice", sessionA);

    expect(owner.owns(page, firstVoice, sessionA)).toBe(false);
    expect(owner.owns(page, latestVoice, sessionA)).toBe(true);
    expect(owner.owns(page, workspace, sessionA)).toBe(true);
  });

  it("settles only the current lane lease", () => {
    const page = {};
    const owner = visibleOwner(page);
    const poll = owner.begin(page, "review-poll", sessionA);
    const voice = owner.begin(page, "voice", sessionA);

    expect(owner.finish(page, poll, sessionA)).toBe(true);
    expect(owner.hasActive("review-poll")).toBe(false);
    expect(owner.hasActive("voice")).toBe(true);
    expect(owner.finish(page, poll, sessionA)).toBe(false);
    expect(owner.owns(page, voice, sessionA)).toBe(true);
  });

  it("invalidates visible-only lanes on hide while allowing an active submission to settle", () => {
    const page = {};
    const owner = visibleOwner(page);
    const leases = new Map<EssayTaskEffectLane, ReturnType<typeof owner.begin>>();
    for (const lane of [
      "workspace",
      "submission",
      "review-poll",
      "voice",
      "handwriting",
    ] as const) {
      leases.set(lane, owner.begin(page, lane, sessionA));
    }

    const invalidated = owner.hide();

    expect(invalidated).toEqual(
      new Set<EssayTaskEffectLane>(["workspace", "review-poll", "voice", "handwriting"]),
    );
    expect(owner.owns(page, leases.get("submission") ?? null, sessionA)).toBe(true);
    for (const lane of ["workspace", "review-poll", "voice", "handwriting"] as const) {
      expect(owner.owns(page, leases.get(lane) ?? null, sessionA)).toBe(false);
    }
  });

  it("rejects unload, another page, account changes and same-token generation changes", () => {
    const page = {};
    const otherPage = {};
    const owner = visibleOwner(page);
    const voice = owner.begin(page, "voice", sessionA);

    expect(owner.owns(otherPage, voice, sessionA)).toBe(false);
    expect(owner.owns(page, voice, { ...sessionA, userId: "user-b" })).toBe(false);
    expect(owner.owns(page, voice, { ...sessionA, accessToken: "token-b" })).toBe(false);
    expect(owner.owns(page, voice, { ...sessionA, generation: "generation-b" })).toBe(false);

    owner.unload();
    expect(owner.owns(page, voice, sessionA)).toBe(false);
  });

  it("blocks preview and handwriting callbacks after visibility or session invalidation", () => {
    const page = {};
    const owner = visibleOwner(page);
    const preview = owner.begin(page, "voice", sessionA);
    const handwriting = owner.begin(page, "handwriting", sessionA);
    const writes = vi.fn();
    const previewComplete = () => {
      if (owner.owns(page, preview, sessionA)) writes("preview-complete");
    };
    const handwritingProgress = () => {
      if (owner.owns(page, handwriting, { ...sessionA, generation: "generation-b" })) {
        writes("handwriting-progress");
      }
    };

    owner.hide();
    previewComplete();
    handwritingProgress();

    expect(writes).not.toHaveBeenCalled();
  });

  it("lets only the active originating lease handle the 401 that cleared its session", () => {
    const page = {};
    const owner = visibleOwner(page);
    const expired = owner.begin(page, "submission", sessionA);

    expect(owner.owns(page, expired, undefined)).toBe(false);
    expect(owner.ownsOrigin(page, expired)).toBe(true);

    owner.begin(page, "submission", sessionA);
    expect(owner.ownsOrigin(page, expired)).toBe(false);
  });
});

describe("essay task cancelled transient state", () => {
  it("resets voice and handwriting work without leaving submissionStatus submitting", () => {
    expect(
      cancelledVisibleEffectPatch(
        {
          submissionStatus: "submitting",
          submissionMessage: "正在上传复盘录音，等检查通过…",
          voiceRecording: true,
          voicePlayingSegmentId: "voice-a",
          handwriting: {
            phase: "uploading",
            localImagePath: "/tmp/answer.jpg",
            progressPercent: 48,
            message: "正在安全上传原图、认手写的字…",
            revision: null,
            spans: [],
          },
        },
        {
          reviewPoll: false,
          voice: true,
          handwriting: true,
          submissionContinues: false,
        },
      ),
    ).toMatchObject({
      submissionStatus: "idle",
      submissionMessage: "",
      voiceRecording: false,
      voicePlayingSegmentId: "",
      handwriting: { phase: "idle", progressPercent: 0 },
    });
  });

  it("preserves a submission that is explicitly allowed to finish in the background", () => {
    const patch = cancelledVisibleEffectPatch(
      {
        submissionStatus: "submitting",
        submissionMessage: "正在存答案…",
        voiceRecording: false,
        voicePlayingSegmentId: "",
        handwriting: {
          phase: "idle",
          localImagePath: "",
          progressPercent: 0,
          message: "",
          revision: null,
          spans: [],
        },
      },
      {
        reviewPoll: true,
        voice: false,
        handwriting: false,
        submissionContinues: true,
      },
    );

    expect(patch).not.toHaveProperty("submissionStatus");
    expect(patch).toMatchObject({ voiceRecording: false, voicePlayingSegmentId: "" });
  });

  it("resets a submitting state when an account change invalidates the submission lane", () => {
    const patch = cancelledVisibleEffectPatch(
      {
        submissionStatus: "submitting",
        submissionMessage: "正在存答案…",
        voiceRecording: false,
        voicePlayingSegmentId: "",
        handwriting: emptyHandwriting(),
      },
      {
        reviewPoll: false,
        voice: false,
        handwriting: false,
        submission: true,
        submissionContinues: false,
      },
    );

    expect(patch).toMatchObject({ submissionStatus: "idle", submissionMessage: "" });
  });
});

function visibleOwner(page: object): EssayTaskEffectOwner {
  const owner = new EssayTaskEffectOwner(page, sessionA);
  expect(owner.show(page, sessionA)).toBe(true);
  return owner;
}

function emptyHandwriting() {
  return {
    phase: "idle" as const,
    localImagePath: "",
    progressPercent: 0,
    message: "",
    revision: null,
    spans: [],
  };
}
