import { ApiClient, ApiClientError } from "../../api/client";
import { ILinkChannelApi, type HubBotAuthorizationView } from "../../api/ilink-channel";
import { currentAuthSession } from "../../auth/session";
import { createIdempotencyKey } from "../../platform/command-id";

interface ILinkBotSheetData {
  view: HubBotAuthorizationView | null;
  status: "idle" | "loading" | "ready" | "error";
  errorMessage: string;
  requestId: string;
  writing: boolean;
  polling: boolean;
}

type ILinkBotSheetProperties = { visible: BooleanConstructor };

type ILinkBotSheetMethods = {
  close: () => void;
  load: () => Promise<void>;
  issue: () => Promise<void>;
  replace: () => Promise<void>;
  writeAuthorization: (replacePending: boolean) => Promise<void>;
  poll: () => Promise<void>;
  schedulePoll: () => void;
  revoke: () => Promise<void>;
};

const POLL_DELAY_MS = 1_500;
const pollTimers = new WeakMap<object, ReturnType<typeof setTimeout>>();

function stopPolling(host: object): void {
  const timer = pollTimers.get(host);
  if (timer !== undefined) clearTimeout(timer);
  pollTimers.delete(host);
}

function isPending(view: HubBotAuthorizationView | null): boolean {
  return view?.state === "waiting_scan" || view?.state === "scanned";
}

function sheetIsVisible(host: object): boolean {
  return Boolean((host as { data?: { visible?: unknown } }).data?.visible);
}

Component<ILinkBotSheetData, ILinkBotSheetProperties, ILinkBotSheetMethods, []>({
  properties: { visible: Boolean },

  data: {
    view: null,
    status: "idle",
    errorMessage: "",
    requestId: "",
    writing: false,
    polling: false,
  },

  observers: {
    visible(visible: boolean) {
      if (visible) {
        void this.load();
      } else {
        stopPolling(this);
      }
    },
  },

  lifetimes: {
    detached() {
      stopPolling(this);
    },
  },

  pageLifetimes: {
    show() {
      if (this.data.visible) void this.load();
    },
    hide() {
      stopPolling(this);
    },
  },

  methods: {
    close() {
      stopPolling(this);
      this.triggerEvent("close");
    },

    async load() {
      stopPolling(this);
      const session = currentAuthSession();
      if (!session) {
        this.setData({
          status: "error",
          errorMessage: "登录状态已经失效，请重新登录后再连接微信岸岸。",
          requestId: "",
        });
        return;
      }
      this.setData({ status: "loading", errorMessage: "", requestId: "" });
      try {
        const response = await new ILinkChannelApi(
          new ApiClient(undefined, session.accessToken),
        ).getHubBot();
        if (!this.data.visible) return;
        this.setData({ view: response.data, status: "ready" });
        if (isPending(response.data)) this.schedulePoll();
      } catch (error) {
        if (!this.data.visible) return;
        const failure = presentChannelFailure(
          error,
          "微信岸岸的连接状态没有读取成功，请重新读取。",
        );
        this.setData({
          status: "error",
          errorMessage: failure.message,
          requestId: failure.requestId,
        });
      }
    },

    async issue() {
      await this.writeAuthorization(false);
    },

    async replace() {
      await this.writeAuthorization(true);
    },

    async writeAuthorization(replacePending: boolean) {
      const session = currentAuthSession();
      if (!session || this.data.writing) return;
      stopPolling(this);
      this.setData({ writing: true, errorMessage: "", requestId: "" });
      try {
        const key = await createIdempotencyKey("ilink-hub-authorization");
        const response = await new ILinkChannelApi(
          new ApiClient(undefined, session.accessToken),
        ).startHubBot(replacePending, key);
        if (!sheetIsVisible(this)) {
          this.setData({ writing: false });
          return;
        }
        this.setData({ view: response.data, status: "ready", writing: false });
        if (isPending(response.data)) this.schedulePoll();
      } catch (error) {
        if (!sheetIsVisible(this)) {
          this.setData({ writing: false });
          return;
        }
        const failure = presentChannelFailure(error, "微信扫码二维码没有生成成功，请重新试一次。");
        this.setData({
          writing: false,
          status: "error",
          errorMessage: failure.message,
          requestId: failure.requestId,
        });
      }
    },

    schedulePoll() {
      stopPolling(this);
      if (!this.data.visible || this.data.polling || !isPending(this.data.view)) return;
      const timer = setTimeout(() => {
        pollTimers.delete(this);
        void this.poll();
      }, POLL_DELAY_MS);
      pollTimers.set(this, timer);
    },

    async poll() {
      const session = currentAuthSession();
      const authorizationId = this.data.view?.authorizationId?.trim();
      if (
        !session ||
        !this.data.visible ||
        this.data.polling ||
        !authorizationId ||
        !isPending(this.data.view)
      ) {
        return;
      }
      stopPolling(this);
      this.setData({ polling: true });
      try {
        const response = await new ILinkChannelApi(
          new ApiClient(undefined, session.accessToken),
        ).pollHubBot(authorizationId);
        if (!sheetIsVisible(this)) {
          this.setData({ polling: false });
          return;
        }
        this.setData({
          view: response.data,
          status: "ready",
          errorMessage: "",
          requestId: "",
          polling: false,
        });
        if (isPending(response.data)) {
          this.schedulePoll();
        } else if (response.data.state === "connected") {
          void wx.showToast({ title: "微信岸岸已连接", icon: "success" });
        }
      } catch (error) {
        if (!sheetIsVisible(this)) {
          this.setData({ polling: false });
          return;
        }
        const failure = presentChannelFailure(error, "扫码状态没有刷新成功，请重新读取。");
        this.setData({
          status: "error",
          errorMessage: failure.message,
          requestId: failure.requestId,
          polling: false,
        });
      }
    },

    async revoke() {
      const session = currentAuthSession();
      if (!session || !this.data.view || this.data.writing) return;
      const decision = await wx.showModal({
        title: "解除微信岸岸？",
        content: "解除后，这个微信入口不能再读取当前账号的学习记录；需要时可重新连接。",
        confirmText: "确认解除",
      });
      if (!decision.confirm) return;
      this.setData({ writing: true, errorMessage: "", requestId: "" });
      stopPolling(this);
      try {
        const key = await createIdempotencyKey("ilink-hub-revoke");
        const response = await new ILinkChannelApi(
          new ApiClient(undefined, session.accessToken),
        ).revokeHubBot(key);
        if (!this.data.visible) {
          this.setData({ writing: false });
          return;
        }
        this.setData({ view: response.data, status: "ready", writing: false });
      } catch (error) {
        if (!this.data.visible) {
          this.setData({ writing: false });
          return;
        }
        const failure = presentChannelFailure(error, "解除连接没有完成，请重新试一次。");
        this.setData({
          writing: false,
          status: "error",
          errorMessage: failure.message,
          requestId: failure.requestId,
        });
      }
    },
  },
});

function presentChannelFailure(
  error: unknown,
  fallback: string,
): { message: string; requestId: string } {
  if (!(error instanceof ApiClientError)) return { message: fallback, requestId: "" };
  if (error.statusCode === 401) {
    return {
      message: "登录状态已经失效，请重新登录后再连接微信岸岸。",
      requestId: error.requestId,
    };
  }
  if (error.statusCode === 503) {
    return { message: "微信连接服务暂时没有响应，请稍后重新读取。", requestId: error.requestId };
  }
  return { message: fallback, requestId: error.requestId };
}
