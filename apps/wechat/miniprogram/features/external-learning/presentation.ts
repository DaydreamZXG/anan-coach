import type { OfflinePacketVersion } from "../../api/external-learning-contracts";
import type {
  AnswerCandidateItem,
  ImportRecognitionSegment,
  PacketAnswerTarget,
} from "./contracts";

export interface PacketCardView extends OfflinePacketVersion {
  displayTitle: string;
  stateLabel: string;
  stateTone: "neutral" | "processing" | "success" | "danger" | "warning";
  itemCountLabel: string;
  versionLabel: string;
  createdAtLabel: string;
  expiresAtLabel: string;
  canOpen: boolean;
  canRetry: boolean;
  canRenew: boolean;
  canIngestAnswers: boolean;
}

export interface RecognitionSegmentView extends ImportRecognitionSegment {
  confidenceLabel: string;
  tone: "success" | "warning";
}

export interface AnswerCandidateView extends AnswerCandidateItem {
  confidenceLabel: string;
  tone: "success" | "warning";
}

export interface AnswerTargetView extends PacketAnswerTarget {
  trackLabel: string;
  expiresAtLabel: string;
}

export function packetCardView(packet: OfflinePacketVersion): PacketCardView {
  const presentation = packetStatePresentation(packet.state);
  const trackLabel = packet.items[0]?.track === "essay" ? "申论" : "行测";
  const created = shortDateLabel(packet.createdAt);
  return {
    ...packet,
    displayTitle: `${trackLabel}学习包 · ${created}`,
    stateLabel: presentation.label,
    stateTone: presentation.tone,
    itemCountLabel: `${String(packet.items.length)} 题`,
    versionLabel: packet.versionNo > 1 ? `第 ${String(packet.versionNo)} 版` : "首次生成",
    createdAtLabel: dateTimeLabel(packet.createdAt),
    expiresAtLabel: packet.expiresAt
      ? `有效至 ${dateTimeLabel(packet.expiresAt)}`
      : "未设置到期时间",
    canOpen: packet.state === "ready" && Boolean(packet.artifactRef),
    canRetry: packet.state === "failed",
    canRenew: packet.state === "expired" || packet.state === "revoked",
    canIngestAnswers:
      packet.state === "ready" &&
      packet.items.length > 0 &&
      packet.items.every((item) => item.track === "xingce"),
  };
}

function shortDateLabel(value: string): string {
  const timestamp = Date.parse(value);
  if (!Number.isFinite(timestamp)) return "已生成";
  const date = new Date(timestamp);
  return `${String(date.getMonth() + 1)}月${String(date.getDate())}日`;
}

export function recognitionSegmentView(segment: ImportRecognitionSegment): RecognitionSegmentView {
  return {
    ...segment,
    confidenceLabel:
      segment.confidence === null
        ? "等待人工确认"
        : `置信度 ${String(Math.round(segment.confidence * 100))}%`,
    tone: segment.requiresCorrection || segment.confidence === null ? "warning" : "success",
  };
}

export function answerCandidateView(item: AnswerCandidateItem): AnswerCandidateView {
  return {
    ...item,
    confidenceLabel:
      item.confidence === null
        ? "需要确认"
        : `置信度 ${String(Math.round(item.confidence * 100))}%`,
    tone: item.requiresCorrection || item.confidence === null ? "warning" : "success",
  };
}

export function answerTargetView(target: PacketAnswerTarget): AnswerTargetView {
  return {
    ...target,
    trackLabel: target.track === "xingce" ? "行测" : "申论",
    expiresAtLabel: target.expiresAt ? `有效至 ${dateTimeLabel(target.expiresAt)}` : "长期有效",
  };
}

export function dateTimeLabel(value: string): string {
  const timestamp = Date.parse(value);
  if (!Number.isFinite(timestamp)) return value;
  const date = new Date(timestamp);
  const pad = (part: number) => String(part).padStart(2, "0");
  return `${String(date.getFullYear())}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function packetStatePresentation(state: OfflinePacketVersion["state"]): {
  label: string;
  tone: PacketCardView["stateTone"];
} {
  const states: Record<
    OfflinePacketVersion["state"],
    {
      label: string;
      tone: PacketCardView["stateTone"];
    }
  > = {
    requested: { label: "已提交", tone: "processing" },
    generating: { label: "生成中", tone: "processing" },
    ready: { label: "可使用", tone: "success" },
    failed: { label: "生成失败", tone: "danger" },
    revoked: { label: "授权已撤回", tone: "warning" },
    expired: { label: "已过期", tone: "warning" },
  };
  return states[state];
}
