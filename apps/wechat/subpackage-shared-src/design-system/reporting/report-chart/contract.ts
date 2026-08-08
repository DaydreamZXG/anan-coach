import type { ChartSpec } from "../../../../miniprogram/api/generated/types.gen";

export const REPORT_CHART_TYPES = [
  "confidence_trend",
  "module_bullet",
  "speed_accuracy_quadrant",
  "error_heatmap",
  "score_gap_waterfall",
  "weekly_completion",
] as const satisfies readonly ChartSpec["chartType"][];

export const REPORT_CHART_TEXT_CHANNELS = ["label", "primary", "secondary", "summary"] as const;
