import { afterEach, describe, expect, it, vi } from "vitest";
import type { CompanionMemoryItem } from "../miniprogram/api/generated/types.gen";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.resetModules();
});

describe("companion memory page", () => {
  it("renders loading, empty and populated server truth", async () => {
    let serverItems: CompanionMemoryItem[] = [];
    const page = await memoryPage((options) => {
      respond(options, memoryState(serverItems));
    });

    const loading = page.loadMemories();
    expect(page.data.status).toBe("loading");
    await loading;
    expect(page.data).toMatchObject({ status: "ready", items: [], shortTermTurnCount: 0 });

    serverItems = [memoryItem({ content: "讲解时先给列式。" })];
    await page.loadMemories();
    expect(page.data.items).toEqual([
      expect.objectContaining({
        content: "讲解时先给列式。",
        categoryLabel: "讲解偏好",
        updatedLabel: "8 月 10 日更新",
      }),
    ]);
  });

  it("gates duplicate creates while a mutation is in flight", async () => {
    const requests: WechatMiniprogram.RequestOption[] = [];
    let serverItems: CompanionMemoryItem[] = [];
    let pendingCreate: WechatMiniprogram.RequestOption | undefined;
    const page = await memoryPage((options) => {
      requests.push(options);
      if (options.method === "POST") {
        pendingCreate = options;
        return;
      }
      respond(options, memoryState(serverItems));
    });
    await page.loadMemories();
    page.setData({ draftContent: "资料分析先给列式。" });

    const first = page.saveMemory();
    const duplicate = page.saveMemory();
    await vi.waitFor(() => {
      expect(pendingCreate).toBeDefined();
    });
    expect(page.data.busy).toBe(true);
    expect(requests.filter((item) => item.method === "POST")).toHaveLength(1);
    const createRequest = pendingCreate;
    if (!createRequest) throw new Error("expected a create request");
    const idempotencyKey = (createRequest.header as Record<string, unknown>)["Idempotency-Key"];
    expect(typeof idempotencyKey).toBe("string");
    expect(idempotencyKey).toMatch(/^companion-memory-/u);
    serverItems = [memoryItem({ content: "资料分析先给列式。" })];
    respond(createRequest, serverItems[0]);
    await Promise.all([first, duplicate]);

    expect(page.data.busy).toBe(false);
    expect(page.data.items).toHaveLength(1);
    expect(page.data.draftContent).toBe("");
  });

  it.each(["stale", "uncertain"] as const)(
    "reloads server state after a %s update",
    async (failure) => {
      const original = memoryItem({ content: "原来的记忆" });
      const current = memoryItem({ content: "服务器上的新内容", revision: 2 });
      const methods: Array<string | undefined> = [];
      let getCount = 0;
      const page = await memoryPage((options) => {
        methods.push(options.method);
        if (options.method === "GET") {
          getCount += 1;
          respond(options, memoryState(getCount === 1 ? [original] : [current]));
          return;
        }
        if (failure === "stale") {
          failHttp(
            options,
            409,
            "memory_revision_conflict",
            "这条记忆已发生变化，请刷新后再修改。",
          );
        } else {
          options.fail?.({
            errMsg: "request:fail timeout",
            errno: 0,
            exception: { reasons: [], retryCount: 0 },
            useHttpDNS: false,
          });
        }
      });
      await page.loadMemories();
      page.editMemory({ currentTarget: { dataset: { id: original.memoryId } } });
      page.setData({ draftContent: "我本地写的内容" });
      await page.saveMemory();

      expect(methods).toEqual(["GET", "PUT", "GET"]);
      expect(page.data.status).toBe("ready");
      expect(page.data.items).toEqual([
        expect.objectContaining({ content: "服务器上的新内容", revision: 2 }),
      ]);
      expect(page.data.busy).toBe(false);
    },
  );
});

interface MemoryPage {
  data: MemoryPageData;
  setData(update: Record<string, unknown>): void;
  loadMemories(): Promise<void>;
  saveMemory(): Promise<void>;
  editMemory(event: { currentTarget: { dataset: { id: string } } }): void;
}

interface MemoryPageData extends Record<string, unknown> {
  status: string;
  items: Array<CompanionMemoryItem & { categoryLabel: string; updatedLabel: string }>;
  shortTermTurnCount: number;
  busy: boolean;
  draftContent: string;
}

async function memoryPage(
  request: (options: WechatMiniprogram.RequestOption) => void,
): Promise<MemoryPage> {
  let captured: unknown;
  let entropy = 0;
  vi.stubGlobal("getApp", () => ({
    globalData: { accessToken: "token-1", userId: "user-a", generation: "generation-a" },
  }));
  vi.stubGlobal("Page", (definition: unknown) => {
    captured = definition;
  });
  vi.stubGlobal("wx", {
    request: (options: WechatMiniprogram.RequestOption) => {
      if (options.url.includes("/api/v2/companion/coach-understanding")) {
        options.fail?.({
          errMsg: "request:fail coach understanding unavailable",
          errno: 0,
          exception: { reasons: [], retryCount: 0 },
          useHttpDNS: false,
        });
        return;
      }
      request(options);
    },
    reLaunch: () => Promise.resolve(),
    showToast: () => Promise.resolve(),
    showModal: () => undefined,
    getStorageSync: () => "",
    setStorageSync: () => undefined,
    removeStorageSync: () => undefined,
    getRandomValues: () => {
      entropy += 1;
      const bytes = new Uint8Array(16);
      bytes[15] = entropy;
      return Promise.resolve({ randomValues: bytes.buffer });
    },
  });
  await import("../miniprogram/account/memory/index");
  const definition = captured as MemoryPage;
  const page = Object.assign({}, definition, {
    data: { ...definition.data },
    setData(update: Record<string, unknown>) {
      Object.assign(this.data, update);
    },
  });
  return page;
}

function memoryState(items: CompanionMemoryItem[]) {
  return {
    longTermEnabled: true,
    items,
    shortTerm: { turnCount: 0, expiresAt: null, retentionHours: 2 },
    maximumLongTermItems: 20,
  };
}

function memoryItem(overrides: Partial<CompanionMemoryItem> = {}): CompanionMemoryItem {
  return {
    memoryId: "00000000-0000-4000-8000-00000000f101",
    category: "explanation_preference",
    content: "讲解时先给列式。",
    source: "explicit_user",
    revision: 1,
    createdAt: "2026-08-10T01:00:00Z",
    updatedAt: "2026-08-10T02:00:00Z",
    ...overrides,
  };
}

function respond(options: WechatMiniprogram.RequestOption, data: unknown): void {
  options.success?.({
    statusCode: 200,
    data: { data, requestId: "request-ok", generatedAt: "2026-08-10T02:00:00Z" },
    header: {},
    cookies: [],
    exception: { reasons: [], retryCount: 0 },
    profile: {} as WechatMiniprogram.RequestProfile,
    useHttpDNS: false,
    errMsg: "request:ok",
  });
}

function failHttp(
  options: WechatMiniprogram.RequestOption,
  statusCode: number,
  code: string,
  message: string,
): void {
  options.success?.({
    statusCode,
    data: { code, message, requestId: "request-conflict" },
    header: {},
    cookies: [],
    exception: { reasons: [], retryCount: 0 },
    profile: {} as WechatMiniprogram.RequestProfile,
    useHttpDNS: false,
    errMsg: "request:ok",
  });
}
