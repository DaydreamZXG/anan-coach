import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { QualificationProfile } from "../miniprogram/api/generated/types.gen";
import type { QualificationApi } from "../miniprogram/api/qualification";
import {
  qualificationFactsFromDraft,
  type QualificationDraft,
} from "../miniprogram/features/qualification/onboarding";
import { loadPendingQualificationProfileEdit } from "../miniprogram/features/qualification/profile-edit-pending";
import { QualificationProfileEditorController } from "../miniprogram/pages/onboarding/features/qualification/profile-editor-controller";

type ProfileApi = Pick<QualificationApi, "getProfile" | "updateProfile">;

let storage: Map<string, unknown>;

beforeEach(() => {
  storage = new Map();
  vi.stubGlobal("wx", {
    getStorageSync: (key: string) => storage.get(key) ?? "",
    setStorageSync: (key: string, value: unknown) => storage.set(key, clone(value)),
    removeStorageSync: (key: string) => storage.delete(key),
    getStorageInfoSync: () => ({ keys: [...storage.keys()], currentSize: 0, limitSize: 10_240 }),
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("qualification profile editor controller", () => {
  it("saves a changed existing profile whose required certificates are a string list", async () => {
    const existing = validDraft({ major: "法学", certifications: ["无"] });
    const changed = validDraft({
      major: "新闻学",
      certifications: ["大学英语六级425分及以上", "雅思6.5分及以上"],
    });
    const updateProfile = vi.fn<ProfileApi["updateProfile"]>(() =>
      Promise.resolve(envelope(profile(changed, 4))),
    );
    const createCommandKey = vi.fn(() => Promise.resolve("qualification-profile-edit-command-2"));
    const controller = new QualificationProfileEditorController({
      userId: "user-a",
      profileApi: {
        getProfile: () => Promise.resolve(envelope(profile(existing, 3))),
        updateProfile,
      },
      onChange: vi.fn(),
      createCommandKey,
    });

    await controller.load();
    controller.changeDraft({ major: changed.major, certifications: changed.certifications });
    await controller.submit();

    expect(updateProfile).toHaveBeenCalledWith(
      qualificationFactsFromDraft(changed),
      "qualification-profile-edit-command-2",
    );
    expect(createCommandKey).toHaveBeenCalledOnce();
    expect(controller.snapshot()).toMatchObject({
      saved: true,
      saving: false,
      pendingRecovery: false,
      profileVersion: 4,
      draft: { major: "新闻学", certifications: changed.certifications },
    });
    expect(loadPendingQualificationProfileEdit("user-a")).toBeNull();
  });
});

function validDraft(changes: Partial<QualificationDraft> = {}): QualificationDraft {
  return {
    education: "本科",
    degree: "学士",
    major: "法学",
    graduationYear: 2026,
    freshGraduate: "true",
    gender: "女",
    politicalStatus: "群众",
    certifications: ["无"],
    workYears: 0,
    grassrootsYears: 0,
    serviceProject: "无",
    hukou: "江西省赣州市",
    ...changes,
  };
}

function profile(draft: QualificationDraft, profileVersion: number): QualificationProfile {
  return {
    profileVersion,
    facts: qualificationFactsFromDraft(draft).map((fact, index) => ({
      ...fact,
      factId: `fact-${String(profileVersion)}-${String(index)}`,
      verificationStatus: "reported" as const,
      effectiveAt: "2026-08-07T00:00:00Z",
    })),
  };
}

function envelope<T>(data: T) {
  return {
    data,
    requestId: "request-profile-editor",
    generatedAt: "2026-08-07T00:00:00Z",
  };
}

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}
