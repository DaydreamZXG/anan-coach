import { afterEach, describe, expect, it, vi } from "vitest";

import type { HubBotAuthorizationView } from "../miniprogram/api/ilink-channel";

const mocks = vi.hoisted(() => ({
  getHubBot: vi.fn(),
  startHubBot: vi.fn(),
  pollHubBot: vi.fn(),
  revokeHubBot: vi.fn(),
  createIdempotencyKey: vi.fn(() => Promise.resolve("hub-command-1")),
}));

vi.mock("../miniprogram/api/ilink-channel", () => ({
  ILinkChannelApi: class {
    getHubBot = mocks.getHubBot;
    startHubBot = mocks.startHubBot;
    pollHubBot = mocks.pollHubBot;
    revokeHubBot = mocks.revokeHubBot;
  },
}));

vi.mock("../miniprogram/auth/session", () => ({
  currentAuthSession: () => ({
    accessToken: "access-token-1",
    userId: "user-1",
    generation: "generation-1",
  }),
}));

vi.mock("../miniprogram/platform/command-id", () => ({
  createIdempotencyKey: mocks.createIdempotencyKey,
}));

interface BotSheetData {
  visible: boolean;
  view: HubBotAuthorizationView | null;
  status: string;
  errorMessage: string;
  requestId: string;
  writing: boolean;
  polling: boolean;
}

interface BotSheetHost {
  data: BotSheetData;
  setData(patch: Partial<BotSheetData>): void;
  triggerEvent(name: string): void;
  load(): Promise<void>;
  issue(): Promise<void>;
  replace(): Promise<void>;
  writeAuthorization(replacePending: boolean): Promise<void>;
  poll(): Promise<void>;
  schedulePoll(): void;
  revoke(): Promise<void>;
}

interface BotSheetDefinition {
  data: Omit<BotSheetData, "visible">;
  methods: {
    [Key in keyof Omit<BotSheetHost, "data" | "setData" | "triggerEvent">]: (
      this: BotSheetHost,
      ...args: never[]
    ) => unknown;
  };
  lifetimes: { detached(this: BotSheetHost): void };
}

afterEach(() => {
  vi.useRealTimers();
  vi.clearAllMocks();
  vi.resetModules();
  vi.unstubAllGlobals();
});

describe("WeChat Anan QR authorization sheet", () => {
  it("polls waiting and scanned authorizations until the bot is connected", async () => {
    vi.useFakeTimers();
    const waiting = authorization("waiting_scan", "data:image/png;base64,waiting");
    const scanned = authorization("scanned", "data:image/png;base64,scanned");
    const connected = authorization("connected", null);
    mocks.getHubBot.mockResolvedValue({ data: waiting });
    mocks.pollHubBot
      .mockResolvedValueOnce({ data: scanned })
      .mockResolvedValueOnce({ data: connected });
    const { definition, host, showToast } = await captureBotSheet();

    await host.load();
    expect(host.data.view).toEqual(waiting);
    await vi.advanceTimersByTimeAsync(1_500);
    expect(mocks.pollHubBot).toHaveBeenLastCalledWith("authorization-1");
    expect(host.data.view).toEqual(scanned);
    await vi.advanceTimersByTimeAsync(1_500);
    expect(host.data.view).toEqual(connected);
    expect(showToast).toHaveBeenCalledWith({ title: "微信岸岸已连接", icon: "success" });

    definition.lifetimes.detached.call(host);
    await vi.runAllTimersAsync();
    expect(mocks.pollHubBot).toHaveBeenCalledTimes(2);
  });

  it("starts, replaces, and revokes the Hub authorization without issuing a link code", async () => {
    const waiting = authorization("waiting_scan", "data:image/png;base64,waiting");
    const revoked = authorization("revoked", null);
    mocks.startHubBot.mockResolvedValue({ data: waiting });
    mocks.revokeHubBot.mockResolvedValue({ data: revoked });
    const { host, showModal } = await captureBotSheet();

    await host.issue();
    expect(mocks.startHubBot).toHaveBeenLastCalledWith(false, "hub-command-1");
    await host.replace();
    expect(mocks.startHubBot).toHaveBeenLastCalledWith(true, "hub-command-1");

    host.data.view = authorization("connected", null);
    await host.revoke();
    expect(showModal).toHaveBeenCalledWith(expect.objectContaining({ title: "解除微信岸岸？" }));
    expect(mocks.revokeHubBot).toHaveBeenCalledWith("hub-command-1");
    expect(host.data.view).toEqual(revoked);
  });
});

async function captureBotSheet(): Promise<{
  definition: BotSheetDefinition;
  host: BotSheetHost;
  showModal: ReturnType<typeof vi.fn>;
  showToast: ReturnType<typeof vi.fn>;
}> {
  let definition: BotSheetDefinition | undefined;
  const showModal = vi.fn(() => Promise.resolve({ confirm: true, cancel: false }));
  const showToast = vi.fn(() => Promise.resolve());
  vi.stubGlobal("Component", (candidate: unknown) => {
    definition = candidate as BotSheetDefinition;
  });
  vi.stubGlobal("wx", { showModal, showToast });
  await import("../miniprogram/components/ilink-bot-sheet/index");
  if (!definition) throw new Error("iLink bot sheet was not registered");

  const data: BotSheetData = { ...definition.data, visible: true };
  const host = {
    data,
    setData(patch: Partial<BotSheetData>) {
      Object.assign(data, patch);
    },
    triggerEvent: vi.fn(),
  } as unknown as BotSheetHost;
  for (const [name, method] of Object.entries(definition.methods)) {
    (host as unknown as Record<string, unknown>)[name] = method.bind(host);
  }
  return { definition, host, showModal, showToast };
}

function authorization(
  state: HubBotAuthorizationView["state"],
  qrImageDataUrl: string | null,
): HubBotAuthorizationView {
  return {
    state,
    authorizationId: "authorization-1",
    qrImageDataUrl,
    expiresAt: "2026-08-03T16:00:00Z",
    statusCopy: state,
    usageCopy: "usage",
  };
}
