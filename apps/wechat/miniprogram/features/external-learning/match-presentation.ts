import type { MatchCandidate } from "../../api/external-learning-contracts";

export interface MatchCandidateView extends MatchCandidate {
  scoreLabel: string;
  evidenceSummaries: string[];
}

const evidenceKindLabels: Readonly<Record<string, string>> = {
  artifact: "上传原件",
  "content-version": "题库版本",
  fingerprint: "题面指纹",
  recognition: "识别结果",
  segment: "识别片段",
};

export function presentMatchCandidates(
  candidates: readonly MatchCandidate[],
): MatchCandidateView[] {
  return candidates.map((candidate) => ({
    ...candidate,
    scoreLabel: `${String(Math.round(candidate.score * 100))}%`,
    evidenceSummaries: candidate.evidenceRefs.slice(0, 2).map(evidenceSummary),
  }));
}

function evidenceSummary(reference: string): string {
  const normalized = reference.trim();
  const separator = normalized.indexOf(":");
  if (separator < 0) return `核对依据 · ${abbreviate(normalized)}`;
  const kind = normalized.slice(0, separator);
  const locator = normalized.slice(separator + 1);
  return `${evidenceKindLabels[kind] ?? "核对依据"} · ${abbreviate(locator)}`;
}

function abbreviate(value: string): string {
  if (value.length <= 18) return value;
  return `${value.slice(0, 8)}…${value.slice(-6)}`;
}
