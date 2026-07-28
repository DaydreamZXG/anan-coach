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
  pollGeneration: number;
}

type ILinkBotSheetProperties = {
  visible: BooleanConstructor;
};

type ILinkBotSheetMethods = {
  close: () => void;
  load: () => Promise<void>;
  startAuthorization: () => Promise<void>;
  replaceAuthorization: () => Promise<void>;
  pollAuthorization: () => Promise<void>;
  revoke: () => Promise<void>;
  writeAuthorization: (replacePending: boolean) => Promise<void>;
  schedulePoll: (generation: number, delay?: number) => void;
};

Component<ILinkBotSheetData, ILinkBotSheetProperties, ILinkBotSheetMethods, []>({
  properties: {
    visible: Boolean,
  },

  data: {
    view: null,
    status: "idle",
    errorMessage: "",
    requestId: "",
    writing: false,
    polling: false,
    pollGeneration: 0,
  },

  observers: {
    visible(visible: boolean) {
      const pollGeneration = this.data.pollGeneration + 1;
      this.setData({ pollGeneration, polling: false });
      if (visible) void this.load();
    },
  },

  lifetimes: {
    detached() {
      this.setData({
        pollGeneration: this.data.pollGeneration + 1,
        polling: false,
      });
    },
  },

  pageLifetimes: {
    show() {
      if (this.data.visible) void this.load();
    },
    hide() {
      this.setData({
        pollGeneration: this.data.pollGeneration + 1,
        polling: false,
      });
    },
  },

  methods: {
    close() {
      this.setData({
        pollGeneration: this.data.pollGeneration + 1,
        polling: false,
      });
      this.triggerEvent("close");
    },

    async load() {
      const session = currentAuthSession();
      if (!session) {
        this.setData({
          status: "error",
          errorMessage: "登录状态已经失效，请重新登录后再连接微信岸岸。",
          requestId: "",
        });
        return;
      }
      const generation = this.data.pollGeneration;
      this.setData({ status: "loading", errorMessage: "", requestId: "" });
      try {
        const response = await new ILinkChannelApi(
          new ApiClient(undefined, session.accessToken),
        ).getHubBot();
        if (generation !== this.data.pollGeneration) return;
        this.setData({
          view: response.data,
          status: "ready",
          errorMessage: "",
          requestId: "",
        });
        if (
          response.data.authorizationId &&
          ["waiting_scan", "scanned"].includes(response.data.state)
        ) {
          this.schedulePoll(generation);
        }
      } catch (error) {
        if (generation !== this.data.pollGeneration) return;
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

    async startAuthorization() {
      await this.writeAuthorization(false);
    },

    async replaceAuthorization() {
      await this.writeAuthorization(true);
    },

    async writeAuthorization(replacePending: boolean) {
      const session = currentAuthSession();
      if (!session || this.data.writing) return;
      const generation = this.data.pollGeneration;
      this.setData({ writing: true, errorMessage: "", requestId: "" });
      try {
        const key = await createIdempotencyKey("ilink-hub-authorize");
        const response = await new ILinkChannelApi(
          new ApiClient(undefined, session.accessToken),
        ).startHubBot(replacePending, key);
        if (generation !== this.data.pollGeneration) return;
        this.setData({
          view: response.data,
          status: "ready",
          writing: false,
        });
        this.schedulePoll(generation);
      } catch (error) {
        if (generation !== this.data.pollGeneration) return;
        const failure = presentChannelFailure(error, "微信二维码没有生成成功，请重新试一次。");
        this.setData({
          writing: false,
          status: "error",
          errorMessage: failure.message,
          requestId: failure.requestId,
        });
      }
    },

    async pollAuthorization() {
      const authorizationId = this.data.view?.authorizationId;
      const session = currentAuthSession();
      if (!authorizationId || !session || this.data.polling || !this.data.visible) {
        return;
      }
      const generation = this.data.pollGeneration;
      this.setData({ polling: true, errorMessage: "", requestId: "" });
      try {
        const response = await new ILinkChannelApi(
          new ApiClient(undefined, session.accessToken),
        ).pollHubBot(authorizationId);
        if (generation !== this.data.pollGeneration) return;
        this.setData({
          view: response.data,
          status: "ready",
          polling: false,
          errorMessage: "",
          requestId: "",
        });
        if (["waiting_scan", "scanned"].includes(response.data.state)) {
          this.schedulePoll(generation, 400);
        }
      } catch (error) {
        if (generation !== this.data.pollGeneration) return;
        const failure = presentChannelFailure(error, "暂时没有读到微信确认结果，请重新检查。");
        this.setData({
          polling: false,
          status: "error",
          errorMessage: failure.message,
          requestId: failure.requestId,
        });
      }
    },

    async revoke() {
      const session = currentAuthSession();
      if (!session || !this.data.view || this.data.writing) return;
      const decision = await wx.showModal({
        title: "解除微信岸岸？",
        content: "解除后，当前专属 Bot 会停止工作；再次使用需要重新扫码连接。",
        confirmText: "确认解除",
      });
      if (!decision.confirm) return;
      const pollGeneration = this.data.pollGeneration + 1;
      this.setData({
        pollGeneration,
        polling: false,
        writing: true,
        errorMessage: "",
        requestId: "",
      });
      try {
        const key = await createIdempotencyKey("ilink-revoke");
        const response = await new ILinkChannelApi(
          new ApiClient(undefined, session.accessToken),
        ).revokeHubBot(key);
        if (!this.data.visible || pollGeneration !== this.data.pollGeneration) return;
        this.setData({
          view: response.data,
          status: "ready",
          writing: false,
        });
      } catch (error) {
        if (!this.data.visible || pollGeneration !== this.data.pollGeneration) return;
        const failure = presentChannelFailure(error, "解除连接没有完成，请重新试一次。");
        this.setData({
          writing: false,
          status: "error",
          errorMessage: failure.message,
          requestId: failure.requestId,
        });
      }
    },

    schedulePoll(generation: number, delay = 0) {
      setTimeout(() => {
        if (this.data.visible && generation === this.data.pollGeneration && !this.data.polling) {
          void this.pollAuthorization();
        }
      }, delay);
    },
  },
});

function presentChannelFailure(
  error: unknown,
  fallback: string,
): { message: string; requestId: string } {
  if (!(error instanceof ApiClientError)) {
    return { message: fallback, requestId: "" };
  }
  if (error.statusCode === 401) {
    return {
      message: "登录状态已经失效，请重新登录后再连接微信岸岸。",
      requestId: error.requestId,
    };
  }
  if (error.statusCode === 503) {
    return {
      message: "微信连接服务暂时没有响应，请稍后重新读取。",
      requestId: error.requestId,
    };
  }
  return { message: fallback, requestId: error.requestId };
}
