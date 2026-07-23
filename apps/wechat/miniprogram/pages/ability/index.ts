import { fetchAbilityEstimate } from "../../api/endpoints";
import { ApiError } from "../../api/client";
import { config } from "../../config/env";
import type { ModuleAbilityEstimate } from "../../api/generated/types.gen";

type ViewState = "loading" | "ready" | "error";

interface ModuleView {
  name: string;
  percent: number;
  sampleSize: number;
}

/** 六模块口径固定，顺序不可变 */
const MODULE_ORDER = [
  "政治理论",
  "常识判断",
  "言语理解",
  "数量关系",
  "判断推理",
  "资料分析",
] as const;

const CONFIDENCE_TEXT: Record<string, string> = {
  unavailable: "证据不足",
  low: "证据较少",
  medium: "证据中等",
  high: "证据充分",
};

const DEMO = {
  xingceScore: 61,
  confidence: "medium",
  statusLabel: "估分趋于稳定",
  dataLine: "证据 23 组 · 更新于 2 小时前 · 算法 v3.2",
  moduleEstimates: [
    { moduleName: "政治理论", accuracy: 0.62, sampleSize: 40, source: "practice" },
    { moduleName: "常识判断", accuracy: 0.55, sampleSize: 36, source: "practice" },
    { moduleName: "言语理解与表达", accuracy: 0.74, sampleSize: 58, source: "practice" },
    { moduleName: "数量关系", accuracy: 0.48, sampleSize: 22, source: "practice" },
    { moduleName: "判断推理", accuracy: 0.69, sampleSize: 47, source: "practice" },
    { moduleName: "资料分析", accuracy: 0.71, sampleSize: 31, source: "mock_exam" },
  ] as ModuleAbilityEstimate[],
};

function shortModuleName(name: string): string {
  if (name.includes("言语")) return "言语理解";
  if (name.includes("常识")) return "常识判断";
  if (name.includes("数量")) return "数量关系";
  if (name.includes("判断")) return "判断推理";
  if (name.includes("资料")) return "资料分析";
  if (name.includes("政治")) return "政治理论";
  return name;
}

function mapModules(list: ModuleAbilityEstimate[]): ModuleView[] {
  const byShort = new Map<string, ModuleView>();
  for (const item of list) {
    const short = shortModuleName(item.moduleName);
    byShort.set(short, {
      name: short,
      percent: Math.round(item.accuracy * 100),
      sampleSize: item.sampleSize,
    });
  }
  return MODULE_ORDER.filter((name) => byShort.has(name)).map(
    (name) => byShort.get(name) as ModuleView,
  );
}

Page({
  data: {
    statusBarHeight: 20,
    viewState: "loading" as ViewState,
    demo: false,
    hasScore: false,
    scoreText: "",
    confidence: "unavailable",
    confidenceText: "",
    statusLabel: "",
    dataLine: "",
    modules: [] as ModuleView[],
    requestId: "",
  },

  onLoad() {
    const app = getApp<{ globalData: { statusBarHeight: number } }>();
    this.setData({ statusBarHeight: app.globalData.statusBarHeight });
    this.load();
  },

  onShow() {
    this.getTabBar()?.setData({ selected: 2 });
  },

  async load() {
    this.setData({ viewState: "loading", demo: false });
    try {
      const res = await fetchAbilityEstimate();
      this.apply(res as unknown as typeof DEMO, false);
    } catch (err) {
      if (config.enableDemoFallback) {
        this.apply(DEMO, true);
        return;
      }
      this.setData({
        viewState: "error",
        requestId: (err as ApiError).requestId ?? "",
      });
    }
  },

  apply(
    data: {
      xingceScore: number | null;
      confidence: string;
      statusLabel: string;
      dataLine: string;
      moduleEstimates: ModuleAbilityEstimate[];
    },
    demo: boolean,
  ) {
    this.setData({
      viewState: "ready",
      demo,
      hasScore: data.xingceScore !== null,
      scoreText: data.xingceScore !== null ? String(data.xingceScore) : "",
      confidence: data.confidence,
      confidenceText: CONFIDENCE_TEXT[data.confidence] ?? "证据不足",
      statusLabel: data.statusLabel ?? "",
      dataLine: data.dataLine ?? "",
      modules: mapModules(data.moduleEstimates ?? []),
    });
  },
});
