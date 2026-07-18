import type { PreparationExperienceWrite } from "../../api/generated/types.gen";

export interface PreparationExperienceDraft {
  firstSystematic: boolean;
  externalLearning: boolean;
  priorGuokaoExam: boolean;
  retake: boolean;
}

export type PreparationExperienceChoice = keyof PreparationExperienceDraft;

export const emptyPreparationExperienceDraft: PreparationExperienceDraft = {
  firstSystematic: false,
  externalLearning: false,
  priorGuokaoExam: false,
  retake: false,
};

export function togglePreparationExperience(
  draft: PreparationExperienceDraft,
  choice: PreparationExperienceChoice,
): PreparationExperienceDraft {
  if (choice === "firstSystematic") {
    return draft.firstSystematic
      ? { ...emptyPreparationExperienceDraft }
      : { ...emptyPreparationExperienceDraft, firstSystematic: true };
  }
  const next = {
    ...draft,
    firstSystematic: false,
    [choice]: !draft[choice],
  };
  if (choice === "retake" && next.retake) next.priorGuokaoExam = true;
  if (choice === "priorGuokaoExam" && !next.priorGuokaoExam) next.retake = false;
  return next;
}

export function validatePreparationExperience(draft: PreparationExperienceDraft): string {
  if (
    !draft.firstSystematic &&
    !draft.externalLearning &&
    !draft.priorGuokaoExam &&
    !draft.retake
  ) {
    return "请选择最符合当前情况的一项；已有经历可以多选。";
  }
  if (draft.firstSystematic && (draft.externalLearning || draft.priorGuokaoExam || draft.retake)) {
    return "首次系统备考不能与已有学习或实考经历同时选择。";
  }
  if (draft.retake && !draft.priorGuokaoExam) return "二战或多次备考需要包含往年国考经历。";
  return "";
}

export function buildPreparationExperienceCommand(
  draft: PreparationExperienceDraft,
  versions: { expectedExperienceVersion: number; expectedProgressVersion: number },
): PreparationExperienceWrite {
  const validation = validatePreparationExperience(draft);
  if (validation) throw new Error(validation);
  return {
    expectedExperienceVersion: versions.expectedExperienceVersion,
    expectedProgressVersion: versions.expectedProgressVersion,
    ...draft,
    priorExamYears: [],
  };
}
