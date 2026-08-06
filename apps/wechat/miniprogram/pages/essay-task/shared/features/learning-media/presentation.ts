import {
  maximumReflectionVoiceSegments,
  maximumReflectionVoiceTotalBytes,
  type ReflectionVoiceSegment,
} from "./contracts";

export interface ReflectionVoiceSegmentView {
  segmentId: string;
  sequenceLabel: string;
  durationLabel: string;
  sizeLabel: string;
  stateLabel: string;
  stateTone: "neutral" | "working" | "success" | "error";
  failureMessage: string;
  canOperate: boolean;
  canUpload: boolean;
}

export interface ReflectionVoiceSummaryView {
  segmentCountLabel: string;
  totalSizeLabel: string;
  canRecord: boolean;
  allAvailable: boolean;
  hasUnready: boolean;
}

export function presentReflectionVoiceSegments(
  segments: readonly ReflectionVoiceSegment[],
): ReflectionVoiceSegmentView[] {
  return segments.map((segment, index) => ({
    segmentId: segment.segmentId,
    sequenceLabel: `第 ${String(index + 1)} 段`,
    durationLabel: formatDuration(segment.durationMilliseconds),
    sizeLabel: formatBytes(segment.byteSize),
    stateLabel: stateLabel(segment),
    stateTone: stateTone(segment),
    failureMessage: segment.failureMessage ?? "",
    canOperate: !["authorizing", "uploading", "scanning"].includes(segment.state),
    canUpload: ["local", "failed"].includes(segment.state),
  }));
}

export function presentReflectionVoiceSummary(
  segments: readonly ReflectionVoiceSegment[],
): ReflectionVoiceSummaryView {
  const totalBytes = segments.reduce((total, segment) => total + segment.byteSize, 0);
  return {
    segmentCountLabel: `${String(segments.length)} / ${String(maximumReflectionVoiceSegments)} 段`,
    totalSizeLabel: `${formatBytes(totalBytes)} / ${formatBytes(maximumReflectionVoiceTotalBytes)}`,
    canRecord: segments.length < maximumReflectionVoiceSegments,
    allAvailable: segments.every((segment) => segment.state === "available"),
    hasUnready: segments.some((segment) => segment.state !== "available"),
  };
}

function stateLabel(segment: ReflectionVoiceSegment): string {
  switch (segment.state) {
    case "local":
      return "本地草稿";
    case "authorizing":
      return "正在申请私有上传";
    case "uploading":
      return "正在上传";
    case "scanning":
      return "正在安全检查";
    case "available":
      return "已就绪";
    case "failed":
      return "未就绪";
  }
}

function stateTone(segment: ReflectionVoiceSegment): ReflectionVoiceSegmentView["stateTone"] {
  switch (segment.state) {
    case "authorizing":
    case "uploading":
    case "scanning":
      return "working";
    case "available":
      return "success";
    case "failed":
      return "error";
    case "local":
      return "neutral";
  }
}

function formatDuration(milliseconds: number): string {
  const seconds = Math.max(1, Math.ceil(milliseconds / 1_000));
  const minutes = Math.floor(seconds / 60);
  const remainder = seconds % 60;
  return `${String(minutes).padStart(2, "0")}:${String(remainder).padStart(2, "0")}`;
}

function formatBytes(bytes: number): string {
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(bytes < 1024 * 100 ? 1 : 0)}KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)}MB`;
}
