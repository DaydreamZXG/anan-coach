import { paintStructuredVisualScene } from "./canvas";
import { presentStructuredVisual } from "./presentation";
import { validateStructuredVisual, type StructuredVisualValidation } from "./schema";
import { buildStructuredVisualScene, preferredCanvasHeight } from "./scene";

interface StructuredVisualRuntime {
  ready: boolean;
  revision: number;
  validation: StructuredVisualValidation;
}

interface StructuredVisualComponent {
  properties: {
    visual: unknown;
  };
  data: {
    status: "valid" | "empty" | "invalid";
    title: string;
    stateTitle: string;
    stateDescription: string;
    warningText: string;
    ariaLabel: string;
    canvasHeight: number;
  };
  setData(patch: Partial<StructuredVisualComponent["data"]>, callback?: () => void): void;
  triggerEvent(name: string, detail: unknown): void;
  createSelectorQuery(): WechatMiniprogram.SelectorQuery;
  refreshVisual(): void;
  bindAndPaint(revision: number): void;
}

const runtimes = new WeakMap<object, StructuredVisualRuntime>();

Component({
  options: {
    styleIsolation: "apply-shared",
  },
  properties: {
    visual: {
      type: Object,
      value: null,
      observer(this: StructuredVisualComponent) {
        this.refreshVisual();
      },
    },
  },
  data: {
    status: "invalid" as const,
    title: "",
    stateTitle: "这份结构化数据无法绘制",
    stateDescription: "尚未提供结构化视觉数据。",
    warningText: "",
    ariaLabel: "结构化视觉数据无效",
    canvasHeight: 300,
  },
  lifetimes: {
    attached(this: StructuredVisualComponent) {
      runtimes.set(this, {
        ready: false,
        revision: 0,
        validation: validateStructuredVisual(this.properties.visual),
      });
    },
    ready(this: StructuredVisualComponent) {
      const runtime = runtimes.get(this);
      if (!runtime) return;
      runtime.ready = true;
      this.refreshVisual();
    },
    detached(this: StructuredVisualComponent) {
      runtimes.delete(this);
    },
  },
  pageLifetimes: {
    resize(this: StructuredVisualComponent) {
      const runtime = runtimes.get(this);
      if (runtime?.ready && runtime.validation.status === "valid") {
        runtime.revision += 1;
        this.bindAndPaint(runtime.revision);
      }
    },
  },
  methods: {
    refreshVisual(this: StructuredVisualComponent) {
      const validation = validateStructuredVisual(this.properties.visual);
      const presentation = presentStructuredVisual(validation);
      const runtime = runtimes.get(this);
      if (runtime) {
        runtime.validation = validation;
        runtime.revision += 1;
      }
      this.setData(
        {
          status: presentation.status,
          title: presentation.title,
          stateTitle: presentation.stateTitle,
          stateDescription: presentation.stateDescription,
          warningText: presentation.warningText,
          ariaLabel: presentation.ariaLabel,
        },
        () => {
          const current = runtimes.get(this);
          if (current?.ready && current.validation.status === "valid") {
            this.bindAndPaint(current.revision);
          }
        },
      );
      this.triggerEvent("validationchange", {
        status: validation.status,
        schemaVersion: validation.registration?.schemaVersion ?? null,
        kind: validation.registration?.kind ?? null,
        issues: validation.issues,
        evidence: validation.evidence,
      });
    },
    bindAndPaint(this: StructuredVisualComponent, revision: number) {
      const runtime = runtimes.get(this);
      if (!runtime || runtime.revision !== revision || runtime.validation.status !== "valid")
        return;
      this.createSelectorQuery()
        .select("#structuredVisualCanvas")
        .fields({ node: true, size: true }, (raw) => {
          const current = runtimes.get(this);
          if (!current || current.revision !== revision || current.validation.status !== "valid")
            return;
          const result = raw as {
            node?: WechatMiniprogram.Canvas;
            width?: number;
            height?: number;
          };
          if (!result.node || !result.width) return;
          const canvasHeight = preferredCanvasHeight(current.validation.visual.kind, result.width);
          if (Math.abs(this.data.canvasHeight - canvasHeight) >= 1) {
            this.setData({ canvasHeight }, () => {
              this.bindAndPaint(revision);
            });
            return;
          }
          const pixelRatio = Math.max(1, wx.getWindowInfo().pixelRatio);
          result.node.width = Math.round(result.width * pixelRatio);
          result.node.height = Math.round(canvasHeight * pixelRatio);
          const context = result.node.getContext("2d");
          context.scale(pixelRatio, pixelRatio);
          const scene = buildStructuredVisualScene(
            current.validation.visual,
            result.width,
            canvasHeight,
          );
          paintStructuredVisualScene(context, scene);
        })
        .exec();
    },
  },
});
