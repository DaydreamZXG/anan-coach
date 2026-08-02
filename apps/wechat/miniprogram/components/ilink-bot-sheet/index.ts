import { ApiClient, ApiClientError } from "../../api/client";
import { ILinkChannelApi, type ILinkChannelView } from "../../api/ilink-channel";
import { currentAuthSession } from "../../auth/session";
import { createIdempotencyKey } from "../../platform/command-id";

interface ILinkBotSheetData {
  view: ILinkChannelView | null;
  status: "idle" | "loading" | "ready" | "error";
  errorMessage: string;
  requestId: string;
  writing: boolean;
}

type ILinkBotSheetProperties = { visible: BooleanConstructor };

type ILinkBotSheetMethods = {
  close: () => void;
  load: () => Promise<void>;
  issue: () => Promise<void>;
  replace: () => Promise<void>;
  writeLink: (replacePending: boolean) => Promise<void>;
  copyCode: () => void;
  revoke: () => Promise<void>;
};

Component<ILinkBotSheetData, ILinkBotSheetProperties, ILinkBotSheetMethods, []>({
  properties: { visible: Boolean },

  data: {
    view: null,
    status: "idle",
    errorMessage: "",
    requestId: "",
    writing: false,
  },

  observers: {
    visible(visible: boolean) {
      if (visible) void this.load();
    },
  },

  pageLifetimes: {
    show() {
      if (this.data.visible) void this.load();
    },
  },

  methods: {
    close() {
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
      this.setData({ status: "loading", errorMessage: "", requestId: "" });
      try {
        const response = await new ILinkChannelApi(
          new ApiClient(undefined, session.accessToken),
        ).get();
        if (!this.data.visible) return;
        this.setData({ view: response.data, status: "ready" });
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
      await this.writeLink(false);
    },

    async replace() {
      await this.writeLink(true);
    },

    async writeLink(replacePending: boolean) {
      const session = currentAuthSession();
      if (!session || this.data.writing) return;
      this.setData({ writing: true, errorMessage: "", requestId: "" });
      try {
        const key = await createIdempotencyKey("ilink-link");
        const response = await new ILinkChannelApi(
          new ApiClient(undefined, session.accessToken),
        ).issue(replacePending, key);
        if (!this.data.visible) return;
        this.setData({ view: response.data, status: "ready", writing: false });
      } catch (error) {
        if (!this.data.visible) return;
        const failure = presentChannelFailure(error, "微信连接码没有生成成功，请重新试一次。");
        this.setData({
          writing: false,
          status: "error",
          errorMessage: failure.message,
          requestId: failure.requestId,
        });
      }
    },

    copyCode() {
      const code = this.data.view?.bindingCode?.trim();
      if (!code) return;
      void wx.setClipboardData({ data: code });
    },

    async revoke() {
      const session = currentAuthSession();
      const linkId = this.data.view?.linkId;
      if (!session || !linkId || this.data.writing) return;
      const decision = await wx.showModal({
        title: "解除微信岸岸？",
        content: "解除后，这个微信入口不能再读取当前账号的学习记录；需要时可重新连接。",
        confirmText: "确认解除",
      });
      if (!decision.confirm) return;
      this.setData({ writing: true, errorMessage: "", requestId: "" });
      try {
        const key = await createIdempotencyKey("ilink-revoke");
        const response = await new ILinkChannelApi(
          new ApiClient(undefined, session.accessToken),
        ).revoke(linkId, key);
        if (!this.data.visible) return;
        this.setData({ view: response.data, status: "ready", writing: false });
      } catch (error) {
        if (!this.data.visible) return;
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
