import type { FontAssetConfiguration } from "../config/environment";

export const PRODUCT_FONT_FAMILY = "Maple Mono CN";

export type ProductFontLoadStatus =
  "unconfigured" | "invalid_configuration" | "loading" | "loaded" | "failed";
export type ProductFontLoadTrigger =
  "app_launch" | "app_show_retry" | "network_reconnected" | "manual";
export type ProductFontFaceKey = "regular" | "bold";
export type ProductFontWeight = "400" | "700";

export interface ProductFontFaceState {
  sourceUrl: string | null;
  status: ProductFontLoadStatus;
  weight: ProductFontWeight;
  resultStatus: string | null;
}

export interface ProductFontLoadState {
  family: typeof PRODUCT_FONT_FAMILY;
  status: ProductFontLoadStatus;
  configured: boolean;
  faces: Record<ProductFontFaceKey, ProductFontFaceState>;
  attempt: number;
  lastTrigger: ProductFontLoadTrigger | null;
  updatedAt: string;
}

type FontFaceLoader = (options: WechatMiniprogram.LoadFontFaceOption) => void;
type StateListener = (state: Readonly<ProductFontLoadState>) => void;

interface ResolvedFace {
  key: ProductFontFaceKey;
  sourceUrl: string;
  weight: ProductFontWeight;
}

interface ActiveLoad {
  assetKey: string;
  promise: Promise<Readonly<ProductFontLoadState>>;
}

const FACE_WEIGHTS: Record<ProductFontFaceKey, ProductFontWeight> = {
  regular: "400",
  bold: "700",
};

export class ProductFontFaceManager {
  private state: ProductFontLoadState;
  private readonly listeners = new Set<StateListener>();
  private activeLoad: ActiveLoad | null = null;
  private loadSequence = 0;

  constructor(
    private readonly loader: FontFaceLoader = (options) => {
      void wx.loadFontFace(options);
    },
    private readonly now: () => Date = () => new Date(),
  ) {
    this.state = this.createEmptyState();
  }

  snapshot(): Readonly<ProductFontLoadState> {
    return cloneState(this.state);
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
    trigger: ProductFontLoadTrigger = "manual",
  ): Promise<Readonly<ProductFontLoadState>> {
    const faces = resolveFaces(config);
    if (!faces) {
      this.invalidateActiveLoad();
      return Promise.resolve(this.reset("unconfigured", false, trigger));
    }
    if (faces.some((face) => !isOwnedHttpsAssetUrl(face.sourceUrl, config.ownedHttpsOrigins))) {
      this.invalidateActiveLoad();
      return Promise.resolve(
        this.transition(
          "invalid_configuration",
          true,
          createFaceStates(faces, "invalid_configuration", "asset_url_rejected"),
          undefined,
          trigger,
        ),
      );
    }

    const assetKey = faces.map((face) => `${face.weight}:${face.sourceUrl}`).join("|");
    if (this.state.status === "loaded" && stateUsesFaces(this.state, faces)) {
      return Promise.resolve(this.snapshot());
    }
    if (this.activeLoad?.assetKey === assetKey) return this.activeLoad.promise;

    const sequence = ++this.loadSequence;
    const attempt = this.state.attempt + 1;
    const previousFaces = stateUsesFaces(this.state, faces) ? this.state.faces : null;
    const loadingFaces = createFaceStates(faces, "loading", null, previousFaces);
    this.transition("loading", true, loadingFaces, attempt, trigger);

    const facesToLoad = faces.filter((face) => loadingFaces[face.key].status === "loading");
    const promise = Promise.all(facesToLoad.map((face) => this.loadFace(face)))
      .then((results) => {
        if (sequence !== this.loadSequence) return this.snapshot();
        const completedFaces = cloneFaces(loadingFaces);
        results.forEach((result) => {
          completedFaces[result.key] = result.state;
        });
        const status = Object.values(completedFaces).every((face) => face.status === "loaded")
          ? "loaded"
          : "failed";
        return this.transition(status, true, completedFaces, attempt, trigger);
      })
      .finally(() => {
        if (sequence === this.loadSequence) this.activeLoad = null;
      });
    this.activeLoad = { assetKey, promise };
    return promise;
  }

  retry(
    config: FontAssetConfiguration,
    trigger: Exclude<ProductFontLoadTrigger, "app_launch">,
  ): Promise<Readonly<ProductFontLoadState>> {
    if (this.state.status !== "failed") return Promise.resolve(this.snapshot());
    return this.load(config, trigger);
  }

  private loadFace(
    face: ResolvedFace,
  ): Promise<{ key: ProductFontFaceKey; state: ProductFontFaceState }> {
    return new Promise((resolve) => {
      const complete = (status: "loaded" | "failed", resultStatus: string) => {
        resolve({
          key: face.key,
          state: {
            sourceUrl: face.sourceUrl,
            status,
            weight: face.weight,
            resultStatus,
          },
        });
      };
      try {
        this.loader({
          family: PRODUCT_FONT_FAMILY,
          source: `url("${face.sourceUrl}")`,
          global: true,
          scopes: ["webview", "native"],
          desc: { style: "normal", weight: face.weight },
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
    });
  }

  private transition(
    status: ProductFontLoadStatus,
    configured: boolean,
    faces: Record<ProductFontFaceKey, ProductFontFaceState>,
    attempt = this.state.attempt,
    trigger = this.state.lastTrigger,
  ): Readonly<ProductFontLoadState> {
    this.state = {
      family: PRODUCT_FONT_FAMILY,
      status,
      configured,
      faces,
      attempt,
      lastTrigger: trigger,
      updatedAt: this.now().toISOString(),
    };
    const snapshot = this.snapshot();
    this.listeners.forEach((listener) => {
      listener(snapshot);
    });
    return snapshot;
  }

  private reset(
    status: "unconfigured",
    configured: false,
    trigger: ProductFontLoadTrigger,
  ): Readonly<ProductFontLoadState> {
    return this.transition(status, configured, createEmptyFaces(), this.state.attempt, trigger);
  }

  private createEmptyState(): ProductFontLoadState {
    return {
      family: PRODUCT_FONT_FAMILY,
      status: "unconfigured",
      configured: false,
      faces: createEmptyFaces(),
      attempt: 0,
      lastTrigger: null,
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

function resolveFaces(config: FontAssetConfiguration): ResolvedFace[] | null {
  const regularUrl = config.mapleMonoCnRegularUrl?.trim();
  const boldUrl = config.mapleMonoCnBoldUrl?.trim();
  if (!regularUrl || !boldUrl) return null;
  return [
    { key: "regular", sourceUrl: regularUrl, weight: FACE_WEIGHTS.regular },
    { key: "bold", sourceUrl: boldUrl, weight: FACE_WEIGHTS.bold },
  ];
}

function createEmptyFaces(): Record<ProductFontFaceKey, ProductFontFaceState> {
  return {
    regular: { sourceUrl: null, status: "unconfigured", weight: "400", resultStatus: null },
    bold: { sourceUrl: null, status: "unconfigured", weight: "700", resultStatus: null },
  };
}

function createFaceStates(
  faces: ResolvedFace[],
  status: ProductFontLoadStatus,
  resultStatus: string | null,
  previous: Record<ProductFontFaceKey, ProductFontFaceState> | null = null,
): Record<ProductFontFaceKey, ProductFontFaceState> {
  return Object.fromEntries(
    faces.map((face) => {
      const prior = previous?.[face.key];
      return [
        face.key,
        prior?.status === "loaded"
          ? { ...prior }
          : { sourceUrl: face.sourceUrl, status, weight: face.weight, resultStatus },
      ];
    }),
  ) as Record<ProductFontFaceKey, ProductFontFaceState>;
}

function stateUsesFaces(state: ProductFontLoadState, faces: ResolvedFace[]): boolean {
  return faces.every(
    (face) =>
      state.faces[face.key].sourceUrl === face.sourceUrl &&
      state.faces[face.key].weight === face.weight,
  );
}

function cloneFaces(
  faces: Record<ProductFontFaceKey, ProductFontFaceState>,
): Record<ProductFontFaceKey, ProductFontFaceState> {
  return { regular: { ...faces.regular }, bold: { ...faces.bold } };
}

function cloneState(state: ProductFontLoadState): Readonly<ProductFontLoadState> {
  return { ...state, faces: cloneFaces(state.faces) };
}

function normalizedHttpsOrigin(url: string): string | null {
  const match = /^(https:\/\/[a-z0-9.-]+(?::\d+)?)(?:\/|$)/i.exec(url.trim());
  return match?.[1]?.toLowerCase() ?? null;
}

function normalizedOwnedHttpsOrigin(url: string): string | null {
  const match = /^(https:\/\/[a-z0-9.-]+(?::\d+)?)\/?$/i.exec(url.trim());
  return match?.[1]?.toLowerCase() ?? null;
}

export const productFontFaceManager = new ProductFontFaceManager();
