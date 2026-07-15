export type ObjectiveTaskExperience = "targeted" | "retest" | "mock";
export type TaskToolPhase = "answering" | "submitted";

export type TaskTool =
  | "answer"
  | "draft"
  | "eliminate"
  | "mark"
  | "answer_sheet"
  | "display_settings"
  | "dispute_report"
  | "operation_help"
  | "fault_help"
  | "analysis"
  | "formula_card"
  | "solution_ai";

export type TaskToolPolicy = Readonly<Record<TaskTool, boolean>>;

const ANSWERING_BASE = {
  answer: true,
  draft: true,
  eliminate: true,
  mark: true,
  answer_sheet: true,
  display_settings: true,
  dispute_report: true,
  operation_help: true,
  fault_help: true,
} as const;

const POLICY_MATRIX = {
  targeted: {
    answering: {
      ...ANSWERING_BASE,
      analysis: false,
      formula_card: false,
      solution_ai: false,
    },
    submitted: {
      ...ANSWERING_BASE,
      answer: false,
      draft: false,
      eliminate: false,
      mark: false,
      analysis: true,
      formula_card: true,
      solution_ai: true,
    },
  },
  retest: {
    answering: {
      ...ANSWERING_BASE,
      analysis: false,
      formula_card: false,
      solution_ai: false,
    },
    submitted: {
      ...ANSWERING_BASE,
      answer: false,
      draft: false,
      eliminate: false,
      mark: false,
      analysis: true,
      formula_card: true,
      solution_ai: true,
    },
  },
  mock: {
    answering: {
      ...ANSWERING_BASE,
      analysis: false,
      formula_card: false,
      solution_ai: false,
    },
    submitted: {
      ...ANSWERING_BASE,
      answer: false,
      draft: false,
      eliminate: false,
      mark: false,
      analysis: true,
      formula_card: true,
      solution_ai: true,
    },
  },
} as const satisfies Record<ObjectiveTaskExperience, Record<TaskToolPhase, TaskToolPolicy>>;

export function taskToolPolicy(
  experience: ObjectiveTaskExperience,
  phase: TaskToolPhase,
): TaskToolPolicy {
  return { ...POLICY_MATRIX[experience][phase] };
}

export function isTaskToolAllowed(
  experience: ObjectiveTaskExperience,
  phase: TaskToolPhase,
  tool: TaskTool,
): boolean {
  return POLICY_MATRIX[experience][phase][tool];
}

export const MOCK_PRE_SUBMIT_ALLOWED_TOOLS = Object.freeze(
  (Object.entries(POLICY_MATRIX.mock.answering) as Array<[TaskTool, boolean]>)
    .filter(([, allowed]) => allowed)
    .map(([tool]) => tool),
);
