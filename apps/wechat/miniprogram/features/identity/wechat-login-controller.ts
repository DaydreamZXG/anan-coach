import { ApiClientError } from "../../api/client";
import type {
  OnboardingProgress,
  WechatAuthSessionData,
  WechatLoginRequest,
} from "../../api/generated/types.gen";

export interface WechatLoginState {
  restoring: boolean;
  submitting: boolean;
  errorCode: string;
  errorMessage: string;
}

export interface WechatLoginDependencies {
  requestCode(): Promise<string>;
  deviceId(): string;
  legalAccepted(): boolean;
  authenticate(request: WechatLoginRequest): Promise<WechatAuthSessionData>;
  restoreProgress(): Promise<OnboardingProgress | null>;
  commitSession(session: WechatAuthSessionData): void;
  discardSession(): void;
  navigate(nextStep: OnboardingProgress["nextStep"]): Promise<void>;
  publish(state: WechatLoginState): void;
}

const initialState: WechatLoginState = {
  restoring: false,
  submitting: false,
  errorCode: "",
  errorMessage: "",
};

export class WechatLoginController {
  private attempt = 0;
  private disposed = false;
  private inFlight: Promise<void> | null = null;

  constructor(private readonly dependencies: WechatLoginDependencies) {
    dependencies.publish(initialState);
  }

  login(): Promise<void> {
    if (this.disposed) return Promise.resolve();
    if (this.inFlight) return this.inFlight;
    const attempt = ++this.attempt;
    const operation = this.run(attempt).finally(() => {
      if (this.inFlight === operation) this.inFlight = null;
    });
    this.inFlight = operation;
    return operation;
  }

  resume(): Promise<void> {
    if (this.disposed) return Promise.resolve();
    if (this.inFlight) return this.inFlight;
    const attempt = ++this.attempt;
    const operation = this.runResume(attempt).finally(() => {
      if (this.inFlight === operation) this.inFlight = null;
    });
    this.inFlight = operation;
    return operation;
  }

  dispose(): void {
    this.disposed = true;
    this.attempt += 1;
    this.inFlight = null;
  }

  private async run(attempt: number): Promise<void> {
    this.publish(attempt, {
      restoring: false,
      submitting: true,
      errorCode: "",
      errorMessage: "",
    });
    try {
      const request: WechatLoginRequest = {
        code: await this.dependencies.requestCode(),
        deviceId: this.dependencies.deviceId(),
        legalAccepted: this.dependencies.legalAccepted(),
      };
      if (!this.isCurrent(attempt)) return;
      const session = await this.dependencies.authenticate(request);
      if (!this.isCurrent(attempt)) return;
      if (!validWechatSessionOwnership(session)) {
        throw new WechatLoginContractError();
      }
      this.dependencies.commitSession(session);
      await this.dependencies.navigate(session.onboardingProgress.nextStep);
    } catch (error) {
      if (!this.isCurrent(attempt)) return;
      const failure = presentWechatLoginError(error);
      this.publish(attempt, {
        restoring: false,
        submitting: false,
        errorCode: failure.code,
        errorMessage: failure.message,
      });
      return;
    }
    this.publish(attempt, initialState);
  }

  private async runResume(attempt: number): Promise<void> {
    this.publish(attempt, {
      restoring: true,
      submitting: false,
      errorCode: "",
      errorMessage: "",
    });
    try {
      const progress = await this.dependencies.restoreProgress();
      if (!this.isCurrent(attempt)) return;
      if (!progress) {
        this.publish(attempt, initialState);
        return;
      }
      await this.dependencies.navigate(progress.nextStep);
    } catch (error) {
      if (!this.isCurrent(attempt)) return;
      const failure = presentWechatSessionRestoreError(error);
      if (failure.discardSession) this.dependencies.discardSession();
      this.publish(attempt, {
        restoring: false,
        submitting: false,
        errorCode: failure.code,
        errorMessage: failure.message,
      });
      return;
    }
    this.publish(attempt, initialState);
  }

  private isCurrent(attempt: number): boolean {
    return !this.disposed && this.attempt === attempt;
  }

  private publish(attempt: number, state: WechatLoginState): void {
    if (this.isCurrent(attempt)) this.dependencies.publish(state);
  }
}

export interface WechatLoginFailureView {
  code: string;
  message: string;
}

interface WechatSessionRestoreFailureView extends WechatLoginFailureView {
  discardSession: boolean;
}

function presentWechatSessionRestoreError(error: unknown): WechatSessionRestoreFailureView {
  const apiError = error instanceof ApiClientError ? error : null;
  const code = apiError?.payload?.code ?? "";
  if (apiError?.statusCode === 401 || code === "authentication_required") {
    return {
      code: "stored_session_expired",
      message: "上次登录已失效，请重新微信登录。",
      discardSession: true,
    };
  }
  return {
    code: code || "session_restore_failed",
    message: apiError
      ? `暂时无法恢复上次登录（请求 ${apiError.requestId}），请重新微信登录。`
      : "暂时无法恢复上次登录，请重新微信登录。",
    // 只有服务端明确拒绝认证时才丢弃会话。短暂网络或 5xx 不应
    // 把用户从已登录状态踢出，下次冷启动可继续恢复。
    discardSession: false,
  };
}

export function presentWechatLoginError(error: unknown): WechatLoginFailureView {
  if (error instanceof WechatLoginContractError) {
    return {
      code: "wechat_login_contract_invalid",
      message: "登录状态校验失败，本次登录没有写入本机，请重新尝试。",
    };
  }
  if (!(error instanceof ApiClientError)) {
    return {
      code: "wechat_login_interrupted",
      message: "微信没有完成登录授权，请重新尝试。",
    };
  }
  const code = error.payload?.code ?? (error.statusCode === 0 ? "network_unavailable" : "");
  const messages: Record<string, string> = {
    account_deletion_pending: "账户正在注销流程中，暂时不能重新登录。",
    account_disabled: "账户当前不可用，请联系支持人员。",
    network_unavailable: "当前网络不可用，请检查网络后重试。",
    wechat_login_not_configured: "微信登录服务尚未完成配置，请稍后再试。",
    wechat_login_rate_limited: "登录操作有些频繁，请稍后再试。",
    wechat_login_rejected: "微信登录凭证已失效，请重新授权。",
    wechat_login_upstream_unavailable: "微信登录服务暂时不可用，请稍后再试。",
  };
  return {
    code: code || "wechat_login_failed",
    message: messages[code] ?? `${error.message}（请求 ${error.requestId}）`,
  };
}

class WechatLoginContractError extends Error {
  constructor() {
    super("Authentication returned conflicting onboarding owners");
    this.name = "WechatLoginContractError";
  }
}

function validWechatSessionOwnership(session: WechatAuthSessionData): boolean {
  const progress = session.onboardingProgress;
  return (
    session.onboardingComplete === progress.completed &&
    (progress.nextStep === "workspace") === progress.completed
  );
}
