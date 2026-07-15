import type { QualificationFactInput, QualificationProfile } from "../../api/generated/types.gen";

export interface QualificationDraft {
  education: string;
  major: string;
  freshGraduate: "" | "true" | "false";
  politicalStatus: string;
  grassrootsYears: number;
  hukou: string;
}

export const emptyQualificationDraft: Readonly<QualificationDraft> = {
  education: "",
  major: "",
  freshGraduate: "",
  politicalStatus: "",
  grassrootsYears: -1,
  hukou: "",
};

export function validateQualificationStep(draft: QualificationDraft, step: number): string {
  if (step === 0 && !draft.education) return "请选择最高学历。";
  if (step === 0 && draft.major.trim().length < 2) return "请输入毕业证上的完整专业名称。";
  if (step === 1 && !draft.freshGraduate) return "请选择是否属于应届高校毕业生。";
  if (step === 1 && !draft.politicalStatus) return "请选择当前政治面貌。";
  if (step === 2 && draft.grassrootsYears < 0) return "请选择基层工作年限。";
  return "";
}

export function validateQualificationDraft(draft: QualificationDraft): string {
  for (const step of [0, 1, 2]) {
    const error = validateQualificationStep(draft, step);
    if (error) return error;
  }
  return "";
}

export function qualificationFactsFromDraft(draft: QualificationDraft): QualificationFactInput[] {
  const facts: QualificationFactInput[] = [
    { key: "education", value: draft.education },
    { key: "major", value: draft.major.trim() },
    { key: "fresh_graduate", value: draft.freshGraduate === "true" },
    { key: "political_status", value: draft.politicalStatus },
    { key: "grassroots_years", value: draft.grassrootsYears },
  ];
  if (draft.hukou.trim()) facts.push({ key: "hukou", value: draft.hukou.trim() });
  return facts;
}

export function qualificationDraftFromProfile(profile: QualificationProfile): QualificationDraft {
  const facts = new Map(profile.facts.map((fact) => [fact.key, fact.value]));
  return {
    education: stringFact(facts.get("education")),
    major: stringFact(facts.get("major")),
    freshGraduate: booleanFact(facts.get("fresh_graduate")),
    politicalStatus: stringFact(facts.get("political_status")),
    grassrootsYears: numberFact(facts.get("grassroots_years")),
    hukou: stringFact(facts.get("hukou")),
  };
}

export function qualificationFactsFingerprint(facts: QualificationFactInput[]): string {
  return JSON.stringify(
    facts
      .map((fact) => ({ key: fact.key, value: fact.value }))
      .sort((left, right) => left.key.localeCompare(right.key)),
  );
}

function stringFact(value: QualificationFactInput["value"] | undefined): string {
  return typeof value === "string" ? value : "";
}

function booleanFact(value: QualificationFactInput["value"] | undefined): "" | "true" | "false" {
  return typeof value === "boolean" ? (value ? "true" : "false") : "";
}

function numberFact(value: QualificationFactInput["value"] | undefined): number {
  return typeof value === "number" && Number.isSafeInteger(value) ? value : -1;
}
