import type { ApiClient } from "./client";

export interface InkStroke {
  x: number[];
  y: number[];
  t: number[];
}

export interface FormulaVersion {
  formulaVersionId: string;
  formulaId: string;
  requestId: string;
  sessionId: string;
  questionId: string;
  versionNo: number;
  state: "candidate" | "confirmed" | "rejected";
  latex: string;
  source: "myscript_candidate" | "user_confirmed";
  supersedesFormulaVersionId: string | null;
  createdAt: string;
  confirmedAt: string | null;
}

export interface MathInkRecognitionResult {
  state: "candidate" | "failed";
  requestId: string;
  formula: FormulaVersion | null;
  failureCode: string | null;
}

export class MathInkApi {
  constructor(private readonly client: ApiClient) {}

  async recognize(
    command: {
      sessionId: string;
      questionId: string;
      sessionAtMs?: number;
      questionElapsedMs?: number;
      scaleX: number;
      scaleY: number;
      strokes: InkStroke[];
    },
    idempotencyKey: string,
  ) {
    return (
      await this.client.post<MathInkRecognitionResult>("/api/v2/math-ink/recognitions", command, {
        idempotencyKey,
        timeoutMs: 45_000,
      })
    ).data;
  }

  async confirm(formula: FormulaVersion, latex: string) {
    return (
      await this.client.post<FormulaVersion>(
        `/api/v2/math-ink/formulas/${encodeURIComponent(formula.formulaVersionId)}/confirm`,
        { expectedVersionNo: formula.versionNo, latex },
        { timeoutMs: 30_000 },
      )
    ).data;
  }
}
