import type { QualificationFactInput, QualificationProfile } from "../../api/generated/types.gen";

export const qualificationCertificationOptions = [
  { value: "法律职业资格证书A证", label: "法律职业资格 A 证" },
  { value: "大学英语四级425分及以上", label: "英语四级 425+" },
  { value: "大学英语六级425分及以上", label: "英语六级 425+" },
  { value: "英语专业八级", label: "英语专业八级" },
  { value: "雅思6.5分及以上", label: "雅思 6.5+" },
  { value: "托福90分及以上", label: "托福 90+" },
  { value: "无", label: "无以上证书" },
] as const;

export const qualificationGenderOptions = ["男", "女"] as const;

export const qualificationPoliticalStatusOptions = [
  "中共党员",
  "中共预备党员",
  "共青团员",
  "群众",
  "民主党派",
] as const;

export function toggleQualificationCertification(
  current: readonly string[],
  value: string,
): string[] {
  const allowed = qualificationCertificationOptions.some((option) => option.value === value);
  if (!allowed) return [...current];
  if (value === "无") return ["无"];
  const withoutNone = current.filter((item) => item !== "无");
  return withoutNone.includes(value)
    ? withoutNone.filter((item) => item !== value)
    : [...withoutNone, value];
}

export interface QualificationDraft {
  education: string;
  degree: string;
  major: string;
  graduationYear: number;
  freshGraduate: "" | "true" | "false";
  gender: string;
  politicalStatus: string;
  certifications: string[];
  workYears: number;
  grassrootsYears: number;
  serviceProject: string;
  hukou: string;
}

export const emptyQualificationDraft: Readonly<QualificationDraft> = {
  education: "",
  degree: "",
  major: "",
  graduationYear: -1,
  freshGraduate: "",
  gender: "",
  politicalStatus: "",
  certifications: [],
  workYears: -1,
  grassrootsYears: -1,
  serviceProject: "",
  hukou: "",
};

export function validateQualificationStep(draft: QualificationDraft, step: number): string {
  if (step === 0 && !draft.education) return "请选择最高学历。";
  if (step === 0 && !draft.degree) return "请选择已取得的学位。";
  if (step === 0 && draft.major.trim().length < 2) return "请输入毕业证上的完整专业名称。";
  if (step === 0 && draft.graduationYear < 2000) return "请选择毕业年份。";
  if (step === 1 && !draft.freshGraduate) return "请选择是否属于应届高校毕业生。";
  if (step === 1 && !draft.gender) return "请选择性别，用于核验限定性别的岗位。";
  if (step === 1 && !draft.politicalStatus) return "请选择当前政治面貌。";
  if (step === 1 && draft.certifications.length === 0) return "请选择已取得的证书，或明确选择无。";
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
  if (!draft.degree) return "请选择已取得的学位。";
  if (draft.major.trim().length < 2) return "请输入毕业证上的完整专业名称。";
  if (draft.graduationYear < 2000) return "请选择毕业年份。";
  if (!draft.freshGraduate) return "请确认毕业年份对应的应届身份。";
  if (!draft.gender) return "请选择性别，用于核验限定性别的岗位。";
  if (!draft.politicalStatus) return "请选择当前政治面貌。";
  if (draft.certifications.length === 0) return "请选择已取得的证书，或明确选择无。";
  if (!draft.serviceProject) return "请选择是否有服务基层项目经历。";
  if (draft.serviceProject !== "无" && draft.grassrootsYears < 0) {
    return "请选择基层服务年限。";
  }
  if (draft.workYears < 0) return "请选择普通工作经历。";
  return "";
}

export function qualificationFactsFromDraft(draft: QualificationDraft): QualificationFactInput[] {
  const facts: QualificationFactInput[] = [];
  if (draft.education) {
    facts.push({ key: "education", value: canonicalEducation(draft.education) });
  }
  if (draft.degree) facts.push({ key: "degree", value: draft.degree });
  if (draft.major.trim()) facts.push({ key: "major", value: draft.major.trim() });
  if (draft.graduationYear >= 2000) {
    facts.push({ key: "graduation_year", value: draft.graduationYear });
  }
  if (draft.freshGraduate) {
    facts.push({ key: "fresh_graduate", value: draft.freshGraduate === "true" });
  }
  if (draft.gender) facts.push({ key: "gender", value: draft.gender });
  if (draft.politicalStatus) {
    facts.push({ key: "political_status", value: draft.politicalStatus });
  }
  if (draft.certifications.length > 0) {
    facts.push({ key: "certifications", value: [...draft.certifications] });
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
    education: canonicalEducation(stringFact(facts.get("education"))),
    degree: stringFact(facts.get("degree")),
    major: stringFact(facts.get("major")),
    graduationYear: numberFact(facts.get("graduation_year")),
    freshGraduate: booleanFact(facts.get("fresh_graduate")),
    gender: stringFact(facts.get("gender")),
    politicalStatus: stringFact(facts.get("political_status")),
    certifications: stringListFact(facts.get("certifications")),
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

function stringListFact(value: QualificationFactInput["value"] | undefined): string[] {
  return Array.isArray(value) && value.every((item) => typeof item === "string") ? [...value] : [];
}

function canonicalEducation(value: string): string {
  if (value === "硕士研究生") return "硕士";
  if (value === "博士研究生") return "博士";
  return value;
}
