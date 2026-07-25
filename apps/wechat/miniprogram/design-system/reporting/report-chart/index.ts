import type { ChartSpec } from "../../../api/generated/types.gen";
import { REPORT_CHART_TYPES } from "./contract";

export interface ReportChartRowView {
  key: string;
  label: string;
  primary: string;
  secondary: string;
  width: string;
  marker: string;
  tone: "primary" | "warning" | "neutral";
}

Component({
  options: { virtualHost: true },
  properties: {
    chart: { type: Object, value: null },
  },
  data: {
    rows: [] as ReportChartRowView[],
    confidenceLabel: "",
    evidenceCount: 0,
  },
  observers: {
    chart(value: ChartSpec | null) {
      if (!value) {
        this.setData({ rows: [], confidenceLabel: "", evidenceCount: 0 });
        return;
      }
      this.setData({
        rows: reportChartRows(value),
        confidenceLabel: confidenceLabel(value.confidence),
        evidenceCount: value.evidenceRefs.length,
      });
    },
  },
});

export function reportChartRows(chart: ChartSpec): ReportChartRowView[] {
  if (!REPORT_CHART_TYPES.includes(chart.chartType)) return [];
  const data = chartData(chart);
  const max = maxValue(chart.chartType, data);
  return data.map((raw, index) => {
    const row = asRecord(raw);
    const mapped = mapRow(chart.chartType, row, index);
    const widthValue = widthValueFor(chart.chartType, row);
    return {
      ...mapped,
      key: `${chart.chartId}-${String(index)}`,
      width: `${String(Math.max(8, Math.min(100, (widthValue / max) * 100)))}%`,
    };
  });
}

function mapRow(
  chartType: ChartSpec["chartType"],
  row: Record<string, unknown>,
  index: number,
): Omit<ReportChartRowView, "key" | "width"> {
  if (chartType === "confidence_trend") {
    return {
      label: shortDate(text(row.at, `第 ${String(index + 1)} 次`)),
      primary: `${number(row.recommended).toFixed(1)} 分`,
      secondary: `${number(row.lower).toFixed(1)}–${number(row.upper).toFixed(1)} · ${confidenceLabel(normalizeConfidence(row.confidence))}`,
      marker: "能力区间",
      tone: "primary",
    };
  }
  if (chartType === "module_bullet") {
    const comparison = nullableNumber(row.comparisonMarker);
    return {
      label: text(row.module, "未知模块"),
      primary: `${number(row.actual).toFixed(1)}%`,
      secondary: comparison === null ? "无上次可比数据" : `上次 ${comparison.toFixed(1)}%`,
      marker: comparison === null ? "当次" : "当次 / 上次",
      tone: number(row.actual) < 60 ? "warning" : "primary",
    };
  }
  if (chartType === "speed_accuracy_quadrant") {
    return {
      label: shortDate(text(row.at, `题组 ${String(index + 1)}`)),
      primary: `${number(row.accuracy).toFixed(1)}%`,
      secondary: `${number(row.secondsPerQuestion).toFixed(1)} 秒/题`,
      marker: "正确率 / 速度",
      tone: number(row.accuracy) < 60 ? "warning" : "primary",
    };
  }
  if (chartType === "error_heatmap") {
    return {
      label: text(row.path, "未知错因"),
      primary: `${String(number(row.wrongCount))} 题`,
      secondary: text(row.fixAction, "待生成修正动作"),
      marker: "错题聚类",
      tone: "warning",
    };
  }
  if (chartType === "score_gap_waterfall") {
    const labels: Record<string, string> = {
      current_ability: "当前能力",
      gap: "岗位分差",
      position_target: "岗位目标",
    };
    const step = text(row.step, "unknown");
    return {
      label: labels[step] ?? step,
      primary: `${number(row.value).toFixed(1)} 分`,
      secondary: step === "gap" ? "目标减当前建议值" : "当前有效版本",
      marker: "分差拆解",
      tone: step === "gap" ? "warning" : "primary",
    };
  }
  return {
    label: `${trackLabel(text(row.track, ""))} · ${text(row.kind, "计划任务")}`,
    primary: `${String(number(row.completed))}/${String(number(row.planned))} 项`,
    secondary: `完成 ${String(number(row.minutes))} 分钟`,
    marker: "计划执行",
    tone: number(row.completed) < number(row.planned) ? "warning" : "primary",
  };
}

function chartData(chart: ChartSpec): Array<Record<string, unknown>> {
  if (Array.isArray(chart.data)) return chart.data;
  throw new TypeError(
    `Invalid ChartSpec ${chart.chartId}: data must be an array, received ${Object.prototype.toString.call(chart.data)} ${JSON.stringify(chart.data)}`,
  );
}

function maxValue(chartType: ChartSpec["chartType"], data: Array<Record<string, unknown>>): number {
  return Math.max(1, ...data.map((row) => widthValueFor(chartType, asRecord(row))));
}

function widthValueFor(chartType: ChartSpec["chartType"], row: Record<string, unknown>): number {
  if (chartType === "confidence_trend") return number(row.recommended);
  if (chartType === "module_bullet") return number(row.actual);
  if (chartType === "speed_accuracy_quadrant") return number(row.accuracy);
  if (chartType === "error_heatmap") return number(row.wrongCount);
  if (chartType === "score_gap_waterfall") return Math.abs(number(row.value));
  return number(row.planned);
}

function confidenceLabel(value: ChartSpec["confidence"]): string {
  return { high: "高置信", medium: "中置信", low: "低置信", not_applicable: "不适用置信度" }[value];
}

function normalizeConfidence(value: unknown): ChartSpec["confidence"] {
  return value === "high" || value === "medium" || value === "not_applicable" ? value : "low";
}

function trackLabel(value: string): string {
  return { xingce: "行测", essay: "申论", mock: "模考" }[value] ?? (value || "全部");
}

function shortDate(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : `${String(date.getMonth() + 1).padStart(2, "0")}.${String(date.getDate()).padStart(2, "0")}`;
}

function asRecord(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function text(value: unknown, fallback: string): string {
  return typeof value === "string" && value.trim() ? value : fallback;
}

function number(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

function nullableNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}
