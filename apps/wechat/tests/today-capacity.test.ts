import { afterEach, describe, expect, it, vi } from "vitest";
import type { DailyCapacityCommand, TodayDashboard } from "../miniprogram/api/generated/types.gen";
import {
  OperationAttemptStore,
  operationAttemptStoragePrefix,
  type OperationAttemptRecord,
} from "../miniprogram/platform/operation-attempt-store";
import { prototypeToday } from "./fixtures/prototype-data";
import {
  capacityCommandFingerprint,
  capacityDraftFromVersion,
  capacityScaleLabel,
  capacityWritePolicy,
  createCapacityIdempotencyKey,
  formatCapacityIdempotencyKey,
  prepareCapacityCommand,
} from "../miniprogram/pages/today/capacity";

const CAPACITY_ATTEMPT_SCOPE = "today-capacity:update@v1";
const ATTEMPT_WINDOW_MS = 7 * 24 * 60 * 60 * 1000;

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.resetModules();
});

describe("today capacity command", () => {
  it("creates a version-aware command from all four user inputs", () => {
    const draft = capacityDraftFromVersion(prototypeToday.capacity);
    const prepared = prepareCapacityCommand(
      { ...draft, availableMinutes: "65", energy: 2, stress: 4, confidenceLevel: 3 },
      prototypeToday.capacity.capacityVersionId,
    );

    expect(prepared).toEqual({
      command: {
        basedOnCapacityVersionId: prototypeToday.capacity.capacityVersionId,
        availableMinutes: 65,
        energy: 2,
        stress: 4,
        confidenceLevel: 3,
        preferredXingceModule: null,
        xingceOnly: false,
      },
      error: "",
    });
    if (!prepared.command) throw new Error("expected a valid capacity command");
    expect(capacityCommandFingerprint(prepared.command)).toContain(
      prototypeToday.capacity.capacityVersionId,
    );
  });

  it.each(["", "12.5", "-1", "1441", "12m"])(
    "rejects invalid available minutes %s before writing",
    (availableMinutes) => {
      const prepared = prepareCapacityCommand(
        { ...capacityDraftFromVersion(prototypeToday.capacity), availableMinutes },
        prototypeToday.capacity.capacityVersionId,
      );

      expect(prepared.command).toBeNull();
      expect(prepared.error).not.toBe("");
    },
  );

  it("rejects invalid scales and missing immutable base versions", () => {
    const draft = capacityDraftFromVersion(prototypeToday.capacity);

    expect(prepareCapacityCommand({ ...draft, energy: 0 }, "capacity-v1").error).toContain("精力");
    expect(prepareCapacityCommand(draft, "").error).toContain("重新生成");
  });

  it("enables writes only for an authenticated account", () => {
    expect(capacityWritePolicy()).toMatchObject({ allowed: false });
    expect(capacityWritePolicy().notice).toContain("登录");
    expect(capacityWritePolicy("token")).toMatchObject({ allowed: true });
  });

  it("describes the current energy, stress, and confidence scales", () => {
    expect(capacityScaleLabel("energy", 1)).toBe("极低");
    expect(capacityScaleLabel("stress", 5)).toBe("很高");
    expect(capacityScaleLabel("confidence", 4)).toBe("把握较高");
  });

  it("formats a cryptographically sourced UUID v4 idempotency key", async () => {
    const entropy = new Uint8Array(16);
    expect(formatCapacityIdempotencyKey(entropy.buffer)).toBe(
      "capacity-00000000-0000-4000-8000-000000000000",
    );
    const getRandomValues = vi.fn().mockResolvedValue({ randomValues: entropy.buffer });
    vi.stubGlobal("wx", { getRandomValues });

    await expect(createCapacityIdempotencyKey()).resolves.toMatch(
      /^capacity-[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
    );
    expect(getRandomValues).toHaveBeenCalledWith({ length: 16 });
  });
});

describe("day adjustment capacity operation attempt", () => {
  it("replays the old command and key after a simulated lost response when reload returns V2", async () => {
    const nextDashboard = dashboardWithVersion("capacity-version-v2");
    let writeCount = 0;
    const scenario = await createCapacityPage((request) => {
      if (isTodayRead(request)) {
        respondToday(request, nextDashboard);
        return;
      }
      if (!isCapacityWrite(request)) throw new Error("unexpected capacity test request");
      writeCount += 1;
      if (writeCount === 1) {
        respondTransportFailure(request);
        return;
      }
      respondSuccess(request);
    });

    await scenario.page.confirmAdjustment();
    const firstWrite = capacityWrites(scenario.requests)[0];
    const firstKey = requestIdempotencyKey(firstWrite);
    expect(storedAttempts(scenario.storage)).toMatchObject([{ state: "unknown" }]);

    const reenteredPage = scenario.instantiatePage();
    await reenteredPage.loadToday();

    const writes = capacityWrites(scenario.requests);
    expect(writes).toHaveLength(2);
    expect(capacityWriteCommand(writes[1])).toEqual(capacityWriteCommand(writes[0]));
    expect(capacityWriteCommand(writes[1]).basedOnCapacityVersionId).toBe(
      prototypeToday.capacity.capacityVersionId,
    );
    expect(requestIdempotencyKey(writes[1])).toBe(firstKey);
    expect(scenario.getRandomValues).toHaveBeenCalledTimes(1);
    expect(storedAttempts(scenario.storage)).toMatchObject([{ state: "succeeded" }]);
    expect(scenario.switchTab).toHaveBeenCalledWith({ url: "/pages/today/index" });
  });

  it.each(["pending", "unknown"] as const)(
    "automatically replays an exact %s attempt on same-day reentry",
    async (state) => {
      const scenario = await createCapacityPage((request) => {
        if (isTodayRead(request)) {
          respondToday(request, dashboardWithVersion("capacity-version-v2"));
          return;
        }
        if (!isCapacityWrite(request)) throw new Error("unexpected capacity test request");
        respondSuccess(request);
      });
      const seeded = await seedAttempt(scenario, state);

      const reenteredPage = scenario.instantiatePage();
      await reenteredPage.loadToday();

      const writes = capacityWrites(scenario.requests);
      expect(writes).toHaveLength(1);
      expect(capacityWriteCommand(writes[0])).toEqual(expectedCapacityCommand());
      expect(requestIdempotencyKey(writes[0])).toBe(seeded.idempotencyKey);
      expect(scenario.getRandomValues).toHaveBeenCalledTimes(1);
      expect(storedAttempts(scenario.storage)).toMatchObject([{ state: "succeeded" }]);
    },
  );

  it.each([408, 500])(
    "keeps an HTTP %s outcome unknown and retains its key",
    async (statusCode) => {
      const scenario = await createCapacityPage((request) => {
        respondHttpError(request, statusCode);
      });

      await scenario.page.confirmAdjustment();

      expect(storedAttempts(scenario.storage)).toMatchObject([{ state: "unknown" }]);
      expect(scenario.page.data.errorMessage).toContain("结果还在确认中");
      expect(scenario.page.data.requestId).toBe(`capacity-${String(statusCode)}`);
      expect(scenario.switchTab).not.toHaveBeenCalled();
    },
  );

  it.each([
    {
      label: "transport failure",
      settleFirst: respondTransportFailure,
    },
    {
      label: "HTTP 408",
      settleFirst: (request: WechatMiniprogram.RequestOption) => {
        respondHttpError(request, 408);
      },
    },
    {
      label: "HTTP 500",
      settleFirst: (request: WechatMiniprogram.RequestOption) => {
        respondHttpError(request, 500);
      },
    },
  ])("shows Retry after a $label recovery and replays the same key", async ({ settleFirst }) => {
    let writeCount = 0;
    const scenario = await createCapacityPage((request) => {
      if (isTodayRead(request)) {
        respondToday(request, dashboardWithVersion("capacity-version-v2"));
        return;
      }
      if (!isCapacityWrite(request)) throw new Error("unexpected capacity test request");
      writeCount += 1;
      if (writeCount === 1) {
        settleFirst(request);
        return;
      }
      respondSuccess(request);
    });
    const seeded = await seedAttempt(scenario, "unknown");
    const reenteredPage = scenario.instantiatePage();

    await reenteredPage.loadToday();

    expect(reenteredPage.data.status).toBe("error");
    expect(reenteredPage.data.submitting).toBe(false);
    expect(reenteredPage.data.errorMessage).toContain("结果还在确认中");
    expect(storedAttempts(scenario.storage)).toMatchObject([{ state: "unknown" }]);
    expect(scenario.switchTab).not.toHaveBeenCalled();

    reenteredPage.retry();
    await vi.waitFor(() => {
      expect(scenario.switchTab).toHaveBeenCalledWith({ url: "/pages/today/index" });
    });

    const writes = capacityWrites(scenario.requests);
    expect(writes).toHaveLength(2);
    expect(requestIdempotencyKey(writes[0])).toBe(seeded.idempotencyKey);
    expect(requestIdempotencyKey(writes[1])).toBe(seeded.idempotencyKey);
    expect(capacityWriteCommand(writes[1])).toEqual(capacityWriteCommand(writes[0]));
    expect(scenario.getRandomValues).toHaveBeenCalledTimes(1);
    expect(storedAttempts(scenario.storage)).toMatchObject([{ state: "succeeded" }]);
  });

  it("marks a deterministic HTTP 409 response terminal", async () => {
    const scenario = await createCapacityPage((request) => {
      respondHttpError(request, 409);
    });

    await scenario.page.confirmAdjustment();

    expect(storedAttempts(scenario.storage)).toMatchObject([{ state: "terminal" }]);
    expect(scenario.page.data.errorMessage).toContain("容量调整没有完成");
    expect(scenario.page.data.requestId).toBe("capacity-409");
    expect(scenario.switchTab).not.toHaveBeenCalled();
  });

  it("cancels an unfinished attempt after a current-page edit and uses a new key", async () => {
    const scenario = await createCapacityPage((request) => {
      respondTransportFailure(request);
    });

    await scenario.page.confirmAdjustment();
    const firstKey = requestIdempotencyKey(capacityWrites(scenario.requests)[0]);

    scenario.page.backToEdit();
    scenario.page.updateMinutes(90);
    scenario.page.data.preview = {};
    await scenario.page.confirmAdjustment();

    const writes = capacityWrites(scenario.requests);
    expect(writes).toHaveLength(2);
    expect(requestIdempotencyKey(writes[1])).not.toBe(firstKey);
    expect(capacityWriteCommand(writes[1]).availableMinutes).toBe(90);
    expect(scenario.getRandomValues).toHaveBeenCalledTimes(2);
    expect(storedAttempts(scenario.storage)).toMatchObject([
      {
        state: "unknown",
        canonicalInput: {
          localDate: prototypeToday.localDate,
          command: { availableMinutes: 90 },
        },
      },
    ]);
  });

  it("does not replay across local dates, terminates the old attempt, and allows a new write", async () => {
    const nextDashboard = dashboardWithLocalDate("2026-07-13", "capacity-next-day");
    const scenario = await createCapacityPage((request) => {
      if (isTodayRead(request)) {
        respondToday(request, nextDashboard);
        return;
      }
      if (!isCapacityWrite(request)) throw new Error("unexpected capacity test request");
      respondSuccess(request);
    });
    const seeded = await seedAttempt(scenario, "unknown");

    const reenteredPage = scenario.instantiatePage();
    await reenteredPage.loadToday();

    expect(capacityWrites(scenario.requests)).toHaveLength(0);
    expect(storedAttempts(scenario.storage)).toMatchObject([{ state: "terminal" }]);
    expect(reenteredPage.data.errorMessage).toContain("昨天调的计划已经过期啦");
    expect(reenteredPage.data.status).toBe("ready");

    reenteredPage.data.preview = {};
    await reenteredPage.confirmAdjustment();

    const writes = capacityWrites(scenario.requests);
    expect(writes).toHaveLength(1);
    expect(requestIdempotencyKey(writes[0])).not.toBe(seeded.idempotencyKey);
    expect(storedAttempts(scenario.storage)).toMatchObject([
      {
        state: "succeeded",
        canonicalInput: { localDate: "2026-07-13" },
      },
    ]);
  });

  it("rejects malformed persisted canonical input instead of replaying it", async () => {
    const scenario = await createCapacityPage((request) => {
      if (!isTodayRead(request)) throw new Error("malformed attempts must not be replayed");
      respondToday(request, prototypeToday);
    });
    const store = new OperationAttemptStore("user-a");
    const malformed = await store.begin({
      scope: CAPACITY_ATTEMPT_SCOPE,
      canonicalInput: {
        localDate: prototypeToday.localDate,
        command: expectedCapacityCommand(),
        accessToken: "token-a",
      },
      idempotencyKeyPrefix: "capacity",
      expiresAtMs: Date.now() + ATTEMPT_WINDOW_MS,
    });
    store.markTransportUnknown(CAPACITY_ATTEMPT_SCOPE, malformed.idempotencyKey, Date.now());

    const reenteredPage = scenario.instantiatePage();
    await reenteredPage.loadToday();

    expect(capacityWrites(scenario.requests)).toHaveLength(0);
    expect(storedAttempts(scenario.storage)).toMatchObject([{ state: "terminal" }]);
    expect(reenteredPage.data.errorMessage).toContain("没记录好");
  });

  it("persists only the fixed scope and strict canonical payload without session secrets", async () => {
    const scenario = await createCapacityPage((request) => {
      respondSuccess(request);
    });

    await scenario.page.confirmAdjustment();

    const attempts = storedAttempts(scenario.storage);
    expect(attempts).toHaveLength(1);
    const attempt = attempts[0];
    if (!attempt) throw new Error("expected one persisted capacity attempt");
    expect(attempt).toMatchObject({
      ownerUserId: "user-a",
      scope: CAPACITY_ATTEMPT_SCOPE,
      state: "succeeded",
      canonicalInput: {
        localDate: prototypeToday.localDate,
        command: expectedCapacityCommand(),
      },
    });
    expect(Object.keys(recordValue(attempt, "canonicalInput")).sort()).toEqual([
      "command",
      "localDate",
    ]);
    expect(
      Object.keys(recordValue(recordValue(attempt, "canonicalInput"), "command")).sort(),
    ).toEqual([
      "availableMinutes",
      "basedOnCapacityVersionId",
      "confidenceLevel",
      "energy",
      "preferredXingceModule",
      "stress",
      "xingceOnly",
    ]);
    expect(JSON.stringify(attempt)).not.toContain("token-a");
  });

  it("isolates the fixed operation scope by authenticated account", async () => {
    const scenario = await createCapacityPage((request) => {
      respondTransportFailure(request);
    });

    await scenario.page.confirmAdjustment();
    const firstKey = requestIdempotencyKey(capacityWrites(scenario.requests)[0]);
    scenario.activeSession.userId = "user-b";
    scenario.activeSession.accessToken = "token-b";
    scenario.activeSession.generation = "session-b";

    const secondPage = scenario.instantiatePage();
    await secondPage.confirmAdjustment();

    const writes = capacityWrites(scenario.requests);
    expect(requestIdempotencyKey(writes[1])).not.toBe(firstKey);
    expect(
      storedAttempts(scenario.storage)
        .map((attempt) => attempt.ownerUserId)
        .sort(),
    ).toEqual(["user-a", "user-b"]);
  });

  it("collapses a second same-page load after the first replay starts", async () => {
    const pendingReplays: WechatMiniprogram.RequestOption[] = [];
    const scenario = await createCapacityPage((request) => {
      if (isTodayRead(request)) {
        respondToday(request, dashboardWithVersion("capacity-version-v2"));
        return;
      }
      pendingReplays.push(request);
    });
    const seeded = await seedAttempt(scenario, "unknown");
    const reenteredPage = scenario.instantiatePage();

    const firstLoad = reenteredPage.loadToday();
    await flushMicrotasks();
    expect(pendingReplays).toHaveLength(1);

    const overlappingLoad = reenteredPage.loadToday();
    await flushMicrotasks();
    expect(pendingReplays).toHaveLength(1);
    expect(requestIdempotencyKey(pendingReplays[0])).toBe(seeded.idempotencyKey);
    const firstReplay = pendingReplays[0];
    if (!firstReplay) throw new Error("expected one current replay");
    respondSuccess(firstReplay);

    await expect(Promise.all([firstLoad, overlappingLoad])).resolves.toBeDefined();
    expect(storedAttempts(scenario.storage)).toMatchObject([{ state: "succeeded" }]);
    expect(reenteredPage.data.status).toBe("ready");
    expect(reenteredPage.data.submitting).toBe(false);
    expect(reenteredPage.data.errorMessage).not.toContain("结果还在确认中");
    expect(scenario.switchTab).toHaveBeenCalledTimes(1);
  });

  it("shares one exact replay across two page instances in the same session", async () => {
    const pendingReplays: WechatMiniprogram.RequestOption[] = [];
    const scenario = await createCapacityPage((request) => {
      if (isTodayRead(request)) {
        respondToday(request, dashboardWithVersion("capacity-version-v2"));
        return;
      }
      pendingReplays.push(request);
    });
    const seeded = await seedAttempt(scenario, "unknown");
    const firstPage = scenario.instantiatePage();
    const secondPage = scenario.instantiatePage();

    const firstLoad = firstPage.loadToday();
    const secondLoad = secondPage.loadToday();
    await flushMicrotasks();

    expect(pendingReplays).toHaveLength(1);
    const sharedReplay = pendingReplays[0];
    if (!sharedReplay) throw new Error("expected one shared page replay request");
    expect(requestIdempotencyKey(sharedReplay)).toBe(seeded.idempotencyKey);
    respondSuccess(sharedReplay);

    await expect(Promise.all([firstLoad, secondLoad])).resolves.toBeDefined();
    expect(storedAttempts(scenario.storage)).toMatchObject([{ state: "succeeded" }]);
    expect(firstPage.data).toMatchObject({ status: "ready", submitting: false });
    expect(secondPage.data).toMatchObject({ status: "ready", submitting: false });
    expect(scenario.switchTab).toHaveBeenCalledTimes(2);
  });

  it.each([
    {
      label: "transport failure",
      settleLate: respondTransportFailure,
    },
    {
      label: "HTTP 409",
      settleLate: (request: WechatMiniprogram.RequestOption) => {
        respondHttpError(request, 409);
      },
    },
    {
      label: "duplicate success",
      settleLate: respondSuccess,
    },
  ])(
    "keeps succeeded authoritative across session-isolated page writes when late result is $label",
    async ({ settleLate }) => {
      const pendingReplays: WechatMiniprogram.RequestOption[] = [];
      const scenario = await createCapacityPage((request) => {
        if (isTodayRead(request)) {
          respondToday(request, dashboardWithVersion("capacity-version-v2"));
          return;
        }
        pendingReplays.push(request);
      });
      await seedAttempt(scenario, "unknown");
      const firstPage = scenario.instantiatePage();

      const firstLoad = firstPage.loadToday();
      await flushMicrotasks();
      expect(pendingReplays).toHaveLength(1);

      scenario.activeSession.generation = "session-b";
      const secondPage = scenario.instantiatePage();
      const secondLoad = secondPage.loadToday();
      await flushMicrotasks();
      const firstReplay = pendingReplays[0];
      const secondReplay = pendingReplays[1];
      if (!firstReplay || !secondReplay) {
        throw new Error("expected one independent replay request per session");
      }

      scenario.activeSession.generation = "session-a";
      respondSuccess(firstReplay);
      await firstLoad;

      scenario.activeSession.generation = "session-b";
      settleLate(secondReplay);

      await expect(secondLoad).resolves.toBeUndefined();
      expect(storedAttempts(scenario.storage)).toMatchObject([{ state: "succeeded" }]);
      expect(firstPage.data.submitting).toBe(false);
      expect(secondPage.data.submitting).toBe(false);
      expect(firstPage.data.status).toBe("ready");
      expect(secondPage.data.status).toBe("ready");
      expect(secondPage.data.errorMessage).not.toContain("结果还在确认中");
      expect(scenario.switchTab).toHaveBeenCalledTimes(2);
    },
  );

  it("updates the current session when its transport failure precedes a stale session success", async () => {
    const pendingReplays: WechatMiniprogram.RequestOption[] = [];
    const scenario = await createCapacityPage((request) => {
      if (isTodayRead(request)) {
        respondToday(request, dashboardWithVersion("capacity-version-v2"));
        return;
      }
      pendingReplays.push(request);
    });
    await seedAttempt(scenario, "unknown");
    const stalePage = scenario.instantiatePage();

    const staleLoad = stalePage.loadToday();
    await flushMicrotasks();
    expect(pendingReplays).toHaveLength(1);
    stalePage.onHide();

    scenario.activeSession.generation = "session-b";
    const currentPage = scenario.instantiatePage();
    const currentLoad = currentPage.loadToday();
    await flushMicrotasks();
    const staleReplay = pendingReplays[0];
    const currentReplay = pendingReplays[1];
    if (!staleReplay || !currentReplay) {
      throw new Error("expected one independent replay request per session");
    }

    respondTransportFailure(currentReplay);
    await currentLoad;
    expect(currentPage.data).toMatchObject({
      status: "error",
      submitting: false,
    });
    expect(storedAttempts(scenario.storage)).toMatchObject([{ state: "unknown" }]);

    respondSuccess(staleReplay);
    await staleLoad;

    expect(storedAttempts(scenario.storage)).toMatchObject([{ state: "succeeded" }]);
    expect(currentPage.data).toMatchObject({
      status: "ready",
      submitting: false,
    });
    expect(currentPage.data.errorMessage).not.toContain("结果还在确认中");
    expect(scenario.switchTab).toHaveBeenCalledTimes(1);
  });

  it("does not publish a recovery response into a replacement session with the same token", async () => {
    let pendingReplay: WechatMiniprogram.RequestOption | undefined;
    const scenario = await createCapacityPage((request) => {
      if (isTodayRead(request)) {
        respondToday(request, prototypeToday);
        return;
      }
      pendingReplay = request;
    });
    await seedAttempt(scenario, "unknown");
    const reenteredPage = scenario.instantiatePage();

    const loading = reenteredPage.loadToday();
    await flushMicrotasks();
    if (!pendingReplay) throw new Error("expected a pending capacity recovery request");

    scenario.activeSession.generation = "replacement-session";
    reenteredPage.setData({
      status: "ready",
      submitting: false,
      errorMessage: "replacement session state",
      requestId: "replacement-request",
    });
    respondHttpError(pendingReplay, 409);
    await loading;

    expect(reenteredPage.data.errorMessage).toBe("replacement session state");
    expect(reenteredPage.data.requestId).toBe("replacement-request");
    expect(reenteredPage.data.submitting).toBe(false);
    expect(scenario.switchTab).not.toHaveBeenCalled();
    expect(storedAttempts(scenario.storage)).toMatchObject([{ state: "terminal" }]);
  });

  it("does not publish or navigate on a confirm success after hide and safely reloads on show", async () => {
    let pendingWrite: WechatMiniprogram.RequestOption | undefined;
    const scenario = await createCapacityPage((request) => {
      if (isTodayRead(request)) {
        respondToday(request, prototypeToday);
        return;
      }
      pendingWrite = request;
    });

    const saving = scenario.page.confirmAdjustment();
    await flushMicrotasks();
    if (!pendingWrite) throw new Error("expected a pending capacity write");
    scenario.page.onHide();
    const hiddenData = clonePageData(scenario.page.data);
    const setData = vi.spyOn(scenario.page, "setData");

    respondSuccess(pendingWrite);
    await saving;

    expect(setData).not.toHaveBeenCalled();
    expect(scenario.page.data).toEqual(hiddenData);
    expect(scenario.switchTab).not.toHaveBeenCalled();
    expect(storedAttempts(scenario.storage)).toMatchObject([{ state: "succeeded" }]);

    scenario.page.onShow();
    await vi.waitFor(() => {
      expect(scenario.page.data.status).toBe("ready");
      expect(scenario.page.data.submitting).toBe(false);
    });
    expect(capacityWrites(scenario.requests)).toHaveLength(1);
    expect(scenario.switchTab).not.toHaveBeenCalled();
  });

  it("shares a pending recovery across hide and show so the visible generation converges on success", async () => {
    const pendingReplays: WechatMiniprogram.RequestOption[] = [];
    const scenario = await createCapacityPage((request) => {
      if (isTodayRead(request)) {
        respondToday(request, dashboardWithVersion("capacity-version-v2"));
        return;
      }
      pendingReplays.push(request);
    });
    const seeded = await seedAttempt(scenario, "unknown");
    const reenteredPage = scenario.instantiatePage();

    const hiddenGenerationLoad = reenteredPage.loadToday();
    await flushMicrotasks();
    expect(pendingReplays).toHaveLength(1);

    reenteredPage.onHide();
    reenteredPage.onShow();
    await flushMicrotasks();

    expect(capacityWrites(scenario.requests)).toHaveLength(1);
    const sharedReplay = pendingReplays[0];
    if (!sharedReplay) throw new Error("expected one shared lifecycle replay request");
    expect(requestIdempotencyKey(sharedReplay)).toBe(seeded.idempotencyKey);
    respondSuccess(sharedReplay);
    await hiddenGenerationLoad;

    await vi.waitFor(() => {
      expect(reenteredPage.data).toMatchObject({ status: "ready", submitting: false });
      expect(scenario.switchTab).toHaveBeenCalledTimes(1);
    });
    expect(storedAttempts(scenario.storage)).toMatchObject([{ state: "succeeded" }]);
    expect(reenteredPage.data.errorMessage).not.toContain("结果还在确认中");
  });

  it("invalidates the page before exit so a late confirm success cannot force navigation", async () => {
    let pendingWrite: WechatMiniprogram.RequestOption | undefined;
    const scenario = await createCapacityPage((request) => {
      pendingWrite = request;
    });

    const saving = scenario.page.confirmAdjustment();
    await flushMicrotasks();
    if (!pendingWrite) throw new Error("expected a pending capacity write");
    scenario.page.exit();
    const exitedData = clonePageData(scenario.page.data);
    const setData = vi.spyOn(scenario.page, "setData");

    respondSuccess(pendingWrite);
    await saving;

    expect(setData).not.toHaveBeenCalled();
    expect(scenario.page.data).toEqual(exitedData);
    expect(scenario.switchTab).not.toHaveBeenCalled();
    expect(scenario.navigateBack).toHaveBeenCalledTimes(1);
    expect(storedAttempts(scenario.storage)).toMatchObject([{ state: "succeeded" }]);
  });

  it("does not publish a late recovery error after unload", async () => {
    let pendingReplay: WechatMiniprogram.RequestOption | undefined;
    const scenario = await createCapacityPage((request) => {
      if (isTodayRead(request)) {
        respondToday(request, prototypeToday);
        return;
      }
      pendingReplay = request;
    });
    await seedAttempt(scenario, "unknown");
    const reenteredPage = scenario.instantiatePage();

    const loading = reenteredPage.loadToday();
    await flushMicrotasks();
    if (!pendingReplay) throw new Error("expected a pending capacity replay");
    reenteredPage.onUnload();
    const unloadedData = clonePageData(reenteredPage.data);
    const setData = vi.spyOn(reenteredPage, "setData");

    respondHttpError(pendingReplay, 500);
    await loading;

    expect(setData).not.toHaveBeenCalled();
    expect(reenteredPage.data).toEqual(unloadedData);
    expect(scenario.switchTab).not.toHaveBeenCalled();
    expect(storedAttempts(scenario.storage)).toMatchObject([{ state: "unknown" }]);
  });

  it("collapses duplicate confirmation taps into one write", async () => {
    let pendingRequest: WechatMiniprogram.RequestOption | undefined;
    const scenario = await createCapacityPage((request) => {
      pendingRequest = request;
    });

    const first = scenario.page.confirmAdjustment();
    const duplicate = scenario.page.confirmAdjustment();
    await flushMicrotasks();

    expect(capacityWrites(scenario.requests)).toHaveLength(1);
    if (!pendingRequest) throw new Error("expected one pending capacity request");
    respondSuccess(pendingRequest);
    await Promise.all([first, duplicate]);
    expect(scenario.switchTab).toHaveBeenCalledTimes(1);
  });
});

interface DayAdjustmentPageData {
  status: string;
  step: string;
  dashboard: TodayDashboard | null;
  preview: object | null;
  previewing: boolean;
  submitting: boolean;
  minutes: number;
  energy: number;
  stress: number;
  confidenceLevel: number;
  errorMessage: string;
  requestId: string;
}

interface DayAdjustmentPage {
  data: DayAdjustmentPageData;
  setData(patch: Partial<DayAdjustmentPageData>): void;
  onShow(): void;
  onHide(): void;
  onUnload(): void;
  exit(): void;
  loadToday(): Promise<void>;
  retry(): void;
  confirmAdjustment(): Promise<void>;
  backToEdit(): void;
  updateMinutes(minutes: number): void;
}

interface CapturedDayAdjustmentDefinition extends DayAdjustmentPage {
  data: DayAdjustmentPageData;
}

interface MutableTestSession {
  accessToken: string;
  userId: string;
  generation: string;
}

interface CapacityPageScenario {
  page: DayAdjustmentPage;
  requests: WechatMiniprogram.RequestOption[];
  storage: Map<string, unknown>;
  switchTab: ReturnType<typeof vi.fn>;
  navigateBack: ReturnType<typeof vi.fn>;
  getRandomValues: ReturnType<typeof vi.fn>;
  activeSession: MutableTestSession;
  instantiatePage(overrides?: Partial<DayAdjustmentPageData>): DayAdjustmentPage;
}

function clonePageData(data: DayAdjustmentPageData): DayAdjustmentPageData {
  return JSON.parse(JSON.stringify(data)) as DayAdjustmentPageData;
}

async function createCapacityPage(
  requestHandler: (request: WechatMiniprogram.RequestOption, index: number) => void,
): Promise<CapacityPageScenario> {
  let captured: CapturedDayAdjustmentDefinition | undefined;
  const requests: WechatMiniprogram.RequestOption[] = [];
  const storage = new Map<string, unknown>();
  const switchTab = vi.fn();
  const navigateBack = vi.fn();
  const activeSession: MutableTestSession = {
    accessToken: "token-a",
    userId: "user-a",
    generation: "session-a",
  };
  let entropySequence = 1;
  const getRandomValues = vi.fn(({ length }: { length: number }) => {
    const entropy = new Uint8Array(length);
    entropy[entropy.length - 1] = entropySequence;
    entropySequence += 1;
    return Promise.resolve({ randomValues: entropy.buffer });
  });
  vi.spyOn(Date, "now").mockReturnValue(1_000);
  vi.stubGlobal("Page", (definition: CapturedDayAdjustmentDefinition) => {
    captured = definition;
  });
  vi.stubGlobal("getApp", () => ({ globalData: activeSession }));
  vi.stubGlobal("getCurrentPages", () => [{}, {}]);
  vi.stubGlobal("wx", {
    getStorageSync: (key: string) => storage.get(key) ?? "",
    setStorageSync: (key: string, value: unknown) => storage.set(key, value),
    removeStorageSync: (key: string) => storage.delete(key),
    getRandomValues,
    request: (request: WechatMiniprogram.RequestOption) => {
      requests.push(request);
      requestHandler(request, requests.length - 1);
    },
    reLaunch: vi.fn(),
    redirectTo: vi.fn(),
    switchTab,
    navigateBack,
  });

  await import("../miniprogram/plans/day-adjustment/index");
  if (!captured) throw new Error("day adjustment page was not registered");
  const definition = captured;
  const instantiatePage = (overrides: Partial<DayAdjustmentPageData> = {}): DayAdjustmentPage => {
    const page = {
      ...definition,
      data: {
        ...definition.data,
        status: "ready",
        step: "preview",
        dashboard: dashboardWithVersion(prototypeToday.capacity.capacityVersionId),
        preview: {},
        minutes: 65,
        energy: 2,
        stress: 4,
        confidenceLevel: 3,
        ...overrides,
      },
      setData(patch: Partial<DayAdjustmentPageData>) {
        Object.assign(this.data, patch);
      },
    };
    return page;
  };

  return {
    page: instantiatePage(),
    requests,
    storage,
    switchTab,
    navigateBack,
    getRandomValues,
    activeSession,
    instantiatePage,
  };
}

function expectedCapacityCommand(
  basedOnCapacityVersionId: string = prototypeToday.capacity.capacityVersionId,
): DailyCapacityCommand {
  return {
    basedOnCapacityVersionId,
    availableMinutes: 65,
    energy: 2,
    stress: 4,
    confidenceLevel: 3,
    preferredXingceModule: null,
    xingceOnly: false,
  };
}

async function seedAttempt(
  scenario: CapacityPageScenario,
  state: "pending" | "unknown",
  localDate: string = prototypeToday.localDate,
  command: DailyCapacityCommand = expectedCapacityCommand(),
): Promise<OperationAttemptRecord> {
  const store = new OperationAttemptStore("user-a");
  const attempt = await store.begin({
    scope: CAPACITY_ATTEMPT_SCOPE,
    canonicalInput: { localDate, command },
    idempotencyKeyPrefix: "capacity",
    expiresAtMs: Date.now() + ATTEMPT_WINDOW_MS,
  });
  if (state === "pending") return attempt;
  return store.markTransportUnknown(CAPACITY_ATTEMPT_SCOPE, attempt.idempotencyKey, Date.now());
}

function dashboardWithVersion(capacityVersionId: string): TodayDashboard {
  return {
    ...prototypeToday,
    capacity: {
      ...prototypeToday.capacity,
      capacityVersionId,
    },
  };
}

function dashboardWithLocalDate(localDate: string, capacityVersionId: string): TodayDashboard {
  return {
    ...prototypeToday,
    localDate,
    capacity: {
      ...prototypeToday.capacity,
      localDate,
      capacityVersionId,
    },
  };
}

function isTodayRead(request: WechatMiniprogram.RequestOption): boolean {
  return request.method === "GET" && request.url.endsWith("/api/v2/plans/today");
}

function isCapacityWrite(request: WechatMiniprogram.RequestOption): boolean {
  return request.method === "PUT" && request.url.endsWith("/api/v2/plans/today/capacity");
}

function capacityWrites(
  requests: WechatMiniprogram.RequestOption[],
): WechatMiniprogram.RequestOption[] {
  return requests.filter(isCapacityWrite);
}

function capacityWriteCommand(
  request: WechatMiniprogram.RequestOption | undefined,
): DailyCapacityCommand {
  const value: unknown = request?.data;
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("capacity request has no command body");
  }
  return value as DailyCapacityCommand;
}

function requestIdempotencyKey(request: WechatMiniprogram.RequestOption | undefined): string {
  const headers: unknown = request?.header;
  if (!headers || typeof headers !== "object") {
    throw new Error("capacity request has no headers");
  }
  const value = (headers as Record<string, unknown>)["Idempotency-Key"];
  if (typeof value !== "string") throw new Error("capacity request has no idempotency key");
  return value;
}

function storedAttempts(storage: Map<string, unknown>): Array<Record<string, unknown>> {
  return [...storage.entries()]
    .filter(([key]) => key.startsWith(`${operationAttemptStoragePrefix()}.`))
    .map(([, value]) => {
      if (!value || typeof value !== "object") throw new Error("stored attempt is malformed");
      return value as Record<string, unknown>;
    })
    .sort((left, right) =>
      `${String(left.ownerUserId)}.${String(left.scope)}`.localeCompare(
        `${String(right.ownerUserId)}.${String(right.scope)}`,
      ),
    );
}

function recordValue(record: Record<string, unknown>, key: string): Record<string, unknown> {
  const value = record[key];
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`stored attempt field ${key} is not an object`);
  }
  return value as Record<string, unknown>;
}

function respondToday(request: WechatMiniprogram.RequestOption, dashboard: TodayDashboard): void {
  request.success?.({
    data: {
      data: dashboard,
      requestId: "today-success",
      generatedAt: "2026-07-28T00:00:00Z",
    },
    statusCode: 200,
    header: {},
    cookies: [],
    exception: { reasons: [], retryCount: 0 },
    profile: {} as WechatMiniprogram.RequestProfile,
    useHttpDNS: false,
    errMsg: "request:ok",
  });
}

function respondSuccess(request: WechatMiniprogram.RequestOption): void {
  request.success?.({
    data: {
      data: {
        capacity: prototypeToday.capacity,
        plan: prototypeToday,
      },
      requestId: "capacity-success",
      generatedAt: "2026-07-28T00:00:00Z",
    },
    statusCode: 200,
    header: {},
    cookies: [],
    exception: { reasons: [], retryCount: 0 },
    profile: {} as WechatMiniprogram.RequestProfile,
    useHttpDNS: false,
    errMsg: "request:ok",
  });
}

function respondHttpError(request: WechatMiniprogram.RequestOption, statusCode: number): void {
  request.success?.({
    data: {
      code: "capacity_conflict",
      message: "容量调整没有完成，请重新载入后再试。",
      requestId: `capacity-${String(statusCode)}`,
    },
    statusCode,
    header: {},
    cookies: [],
    exception: { reasons: [], retryCount: 0 },
    profile: {} as WechatMiniprogram.RequestProfile,
    useHttpDNS: false,
    errMsg: "request:ok",
  });
}

function respondTransportFailure(request: WechatMiniprogram.RequestOption): void {
  request.fail?.({
    errMsg: "request:fail timeout",
    errno: 5,
    exception: { reasons: [], retryCount: 0 },
    useHttpDNS: false,
  });
}

async function flushMicrotasks(): Promise<void> {
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
}
