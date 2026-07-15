import type { FontAssetConfiguration } from "../config/environment";

export const DATA_FONT_FAMILY = "Maple Mono Data";

export type DataFontLoadStatus =
  "unconfigured" | "invalid_configuration" | "loading" | "loaded" | "failed";
export type DataFontLoadTrigger =
  "app_launch" | "app_show_retry" | "network_reconnected" | "manual";

export interface DataFontLoadState {
  family: typeof DATA_FONT_FAMILY;
  status: DataFontLoadStatus;
  configured: boolean;
  sourceUrl: string | null;
  attempt: number;
  lastTrigger: DataFontLoadTrigger | null;
  resultStatus: string | null;
  updatedAt: string;
}

type FontFaceLoader = (options: WechatMiniprogram.LoadFontFaceOption) => void;
type StateListener = (state: Readonly<DataFontLoadState>) => void;

interface ActiveLoad {
  sourceUrl: string;
  promise: Promise<Readonly<DataFontLoadState>>;
}

export class DataFontFaceManager {
  private state: DataFontLoadState;
  private readonly listeners = new Set<StateListener>();
  private activeLoad: ActiveLoad | null = null;
  private loadSequence = 0;

  constructor(
    private readonly loader: FontFaceLoader = (options) => {
      void wx.loadFontFace(options);
    },
    private readonly now: () => Date = () => new Date(),
  ) {
    this.state = this.createState("unconfigured", false, null, 0, null, null);
  }

  snapshot(): Readonly<DataFontLoadState> {
    return { ...this.state };
  }

  subscribe(listener: StateListener): () => void {
    this.listeners.add(listener);
    listener(this.snapshot());
    return () => {
      this.listeners.delete(listener);
    };
  }

  load(
    config: FontAssetConfiguration,
    trigger: DataFontLoadTrigger = "manual",
  ): Promise<Readonly<DataFontLoadState>> {
    const sourceUrl = config.mapleMonoUrl?.trim() || null;
    if (!sourceUrl) {
      this.invalidateActiveLoad();
      return Promise.resolve(
        this.transition("unconfigured", false, null, null, undefined, trigger),
      );
    }
    if (!isOwnedHttpsAssetUrl(sourceUrl, config.ownedHttpsOrigins)) {
      this.invalidateActiveLoad();
      return Promise.resolve(
        this.transition(
          "invalid_configuration",
          true,
          sourceUrl,
          "asset_url_rejected",
          undefined,
          trigger,
        ),
      );
    }
    if (this.state.status === "loaded" && this.state.sourceUrl === sourceUrl) {
      return Promise.resolve(this.snapshot());
    }
    if (this.activeLoad?.sourceUrl === sourceUrl) return this.activeLoad.promise;

    const sequence = ++this.loadSequence;
    const attempt = this.state.attempt + 1;
    this.transition("loading", true, sourceUrl, null, attempt, trigger);
    const promise = new Promise<Readonly<DataFontLoadState>>((resolve) => {
      const complete = (status: DataFontLoadStatus, resultStatus: string) => {
        if (sequence !== this.loadSequence) {
          resolve(this.snapshot());
          return;
        }
        resolve(this.transition(status, true, sourceUrl, resultStatus, attempt, trigger));
      };
      try {
        this.loader({
          family: DATA_FONT_FAMILY,
          source: `url("${sourceUrl}")`,
          global: true,
          scopes: ["webview", "native"],
          desc: { style: "normal" },
          success: (result) => {
            complete("loaded", result.status || "loaded");
          },
          fail: (result) => {
            complete("failed", result.status || "load_failed");
          },
        });
      } catch (error) {
        complete("failed", error instanceof Error ? error.message : "load_failed");
      }
    }).finally(() => {
      if (sequence === this.loadSequence) this.activeLoad = null;
    });
    this.activeLoad = { sourceUrl, promise };
    return promise;
  }

  retry(
    config: FontAssetConfiguration,
    trigger: Exclude<DataFontLoadTrigger, "app_launch">,
  ): Promise<Readonly<DataFontLoadState>> {
    if (this.state.status !== "failed") return Promise.resolve(this.snapshot());
    return this.load(config, trigger);
  }

  private transition(
    status: DataFontLoadStatus,
    configured: boolean,
    sourceUrl: string | null,
    resultStatus: string | null,
    attempt = this.state.attempt,
    trigger = this.state.lastTrigger,
  ): Readonly<DataFontLoadState> {
    this.state = this.createState(status, configured, sourceUrl, attempt, resultStatus, trigger);
    const snapshot = this.snapshot();
    this.listeners.forEach((listener) => {
      listener(snapshot);
    });
    return snapshot;
  }

  private createState(
    status: DataFontLoadStatus,
    configured: boolean,
    sourceUrl: string | null,
    attempt: number,
    resultStatus: string | null,
    lastTrigger: DataFontLoadTrigger | null,
  ): DataFontLoadState {
    return {
      family: DATA_FONT_FAMILY,
      status,
      configured,
      sourceUrl,
      attempt,
      lastTrigger,
      resultStatus,
      updatedAt: this.now().toISOString(),
    };
  }

  private invalidateActiveLoad(): void {
    this.loadSequence += 1;
    this.activeLoad = null;
  }
}

export function isOwnedHttpsAssetUrl(sourceUrl: string, ownedOrigins: string[]): boolean {
  if (/['"()\s]/.test(sourceUrl)) return false;
  if (!/^https:\/\/[a-z0-9.-]+(?::\d+)?\/[^/?#][^\s]*$/i.test(sourceUrl)) return false;
  const sourceOrigin = normalizedHttpsOrigin(sourceUrl);
  if (!sourceOrigin) return false;
  return ownedOrigins.some((origin) => normalizedOwnedHttpsOrigin(origin) === sourceOrigin);
}

function normalizedHttpsOrigin(url: string): string | null {
  const match = /^(https:\/\/[a-z0-9.-]+(?::\d+)?)(?:\/|$)/i.exec(url.trim());
  return match?.[1]?.toLowerCase() ?? null;
}

function normalizedOwnedHttpsOrigin(url: string): string | null {
  const match = /^(https:\/\/[a-z0-9.-]+(?::\d+)?)\/?$/i.exec(url.trim());
  return match?.[1]?.toLowerCase() ?? null;
}

export const dataFontFaceManager = new DataFontFaceManager();
