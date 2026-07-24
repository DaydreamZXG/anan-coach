import type { AbilityBaselineWrite } from "../../api/generated/types.gen";

export type AbilityBaselineMode = AbilityBaselineWrite["mode"];

export interface AbilityBaselineDraft {
  mode: AbilityBaselineMode | null;
  xingceScore: string;
  shenlunScore: string;
}

export const emptyAbilityBaselineDraft: Readonly<AbilityBaselineDraft> = {
  mode: null,
  xingceScore: "",
  shenlunScore: "",
};

export function validateAbilityBaselineDraft(draft: AbilityBaselineDraft): string {
  if (draft.mode === null) return "请选择一种能力起点方式。";
  if (draft.mode === "unknown") return "";
  const xingce = parseScore(draft.xingceScore);
  const shenlun = parseScore(draft.shenlunScore);
  if (xingce.kind === "invalid" || shenlun.kind === "invalid") {
    return "成绩需要填写 0–100 之间的数字，最多保留两位小数。";
  }
  if (xingce.value === null && shenlun.value === null) {
    return "请至少填写行测或申论的一项成绩。";
  }
  return "";
}

export function buildAbilityBaselineCommand(
  draft: AbilityBaselineDraft,
  expectedBaselineVersion: number,
): AbilityBaselineWrite {
  const validationError = validateAbilityBaselineDraft(draft);
  if (validationError) throw new Error(validationError);
  if (!Number.isSafeInteger(expectedBaselineVersion) || expectedBaselineVersion < 0) {
    throw new Error("Ability baseline requires a valid expected version");
  }
  if (draft.mode === "unknown") {
    return {
      expectedBaselineVersion,
      mode: "unknown",
      xingceScore: null,
      shenlunScore: null,
    };
  }
  return {
    expectedBaselineVersion,
    mode: "reported_score",
    xingceScore: parseScore(draft.xingceScore).value,
    shenlunScore: parseScore(draft.shenlunScore).value,
  };
}

export function abilityBaselineDraftFromCommand(
  command: AbilityBaselineWrite,
): AbilityBaselineDraft {
  return {
    mode: command.mode,
    xingceScore: scoreText(command.xingceScore),
    shenlunScore: scoreText(command.shenlunScore),
  };
}

function parseScore(raw: string): { kind: "valid" | "invalid"; value: number | null } {
  const normalized = raw.trim();
  if (!normalized) return { kind: "valid", value: null };
  if (!/^(?:100(?:\.0{1,2})?|(?:\d{1,2})(?:\.\d{1,2})?)$/u.test(normalized)) {
    return { kind: "invalid", value: null };
  }
  const value = Number(normalized);
  return Number.isFinite(value) && value >= 0 && value <= 100
    ? { kind: "valid", value }
    : { kind: "invalid", value: null };
}

function scoreText(value: number | null | undefined): string {
  return value === null || value === undefined ? "" : String(value);
}
