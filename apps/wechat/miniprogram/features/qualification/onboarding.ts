import type { QualificationFactInput, QualificationProfile } from "../../api/generated/types.gen";

export interface QualificationDraft {
  education: string;
  major: string;
  graduationYear: number;
  freshGraduate: "" | "true" | "false";
  politicalStatus: string;
  workYears: number;
  grassrootsYears: number;
  serviceProject: string;
  hukou: string;
}

export const emptyQualificationDraft: Readonly<QualificationDraft> = {
  education: "",
  major: "",
  graduationYear: -1,
  freshGraduate: "",
  politicalStatus: "",
  workYears: -1,
  grassrootsYears: -1,
  serviceProject: "",
  hukou: "",
};

export function validateQualificationStep(draft: QualificationDraft, step: number): string {
  if (step === 0 && !draft.education) return "请选择最高学历。";
  if (step === 0 && draft.major.trim().length < 2) return "请输入毕业证上的完整专业名称。";
  if (step === 0 && draft.graduationYear < 2000) return "请选择毕业年份。";
  if (step === 1 && !draft.freshGraduate) return "请选择是否属于应届高校毕业生。";
  if (step === 1 && !draft.politicalStatus) return "请选择当前政治面貌。";
  if (step === 2 && draft.workYears < 0) return "请选择普通工作年限。";
  if (step === 2 && draft.grassrootsYears < 0) return "请选择基层工作年限。";
  if (step === 2 && !draft.serviceProject) return "请选择是否有服务基层项目。";
  return "";
}

export function validateQualificationDraft(draft: QualificationDraft): string {
  for (const step of [0, 1, 2]) {
    const error = validateQualificationStep(draft, step);
    if (error) return error;
  }
  return "";
}

export function validateOnboardingQualificationDraft(draft: QualificationDraft): string {
  if (!draft.education) return "请选择最高学历。";
  if (draft.major.trim().length < 2) return "请输入毕业证上的完整专业名称。";
  if (draft.graduationYear < 2000) return "请选择毕业年份。";
  if (!draft.freshGraduate) return "请确认毕业年份对应的应届身份。";
  if (!draft.serviceProject) return "请选择是否有服务基层项目经历。";
  if (draft.workYears < 0) return "请选择普通工作经历。";
  return "";
}

export function qualificationFactsFromDraft(draft: QualificationDraft): QualificationFactInput[] {
  const facts: QualificationFactInput[] = [];
  if (draft.education) facts.push({ key: "education", value: draft.education });
  if (draft.major.trim()) facts.push({ key: "major", value: draft.major.trim() });
  if (draft.graduationYear >= 2000) {
    facts.push({ key: "graduation_year", value: draft.graduationYear });
  }
  if (draft.freshGraduate) {
    facts.push({ key: "fresh_graduate", value: draft.freshGraduate === "true" });
  }
  if (draft.politicalStatus) {
    facts.push({ key: "political_status", value: draft.politicalStatus });
  }
  if (draft.workYears >= 0) facts.push({ key: "work_years", value: draft.workYears });
  if (draft.grassrootsYears >= 0) {
    facts.push({ key: "grassroots_years", value: draft.grassrootsYears });
  }
  if (draft.serviceProject) {
    facts.push({ key: "service_project", value: draft.serviceProject });
  }
  if (draft.hukou.trim()) facts.push({ key: "hukou", value: draft.hukou.trim() });
  return facts;
}

export function qualificationDraftFromProfile(profile: QualificationProfile): QualificationDraft {
  const facts = new Map(profile.facts.map((fact) => [fact.key, fact.value]));
  return {
    education: stringFact(facts.get("education")),
    major: stringFact(facts.get("major")),
    graduationYear: numberFact(facts.get("graduation_year")),
    freshGraduate: booleanFact(facts.get("fresh_graduate")),
    politicalStatus: stringFact(facts.get("political_status")),
    workYears: numberFact(facts.get("work_years")),
    grassrootsYears: numberFact(facts.get("grassroots_years")),
    serviceProject: stringFact(facts.get("service_project")),
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
