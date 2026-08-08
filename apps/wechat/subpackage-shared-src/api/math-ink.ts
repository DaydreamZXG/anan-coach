import type { ApiClient } from "../../miniprogram/api/client";
import type {
  ConfirmFormulaCommand,
  FormulaVersion as GeneratedFormulaVersion,
  InkStroke as GeneratedInkStroke,
  MathInkRecognitionResult as GeneratedMathInkRecognitionResult,
  RecognizeMathInkCommand,
} from "../../miniprogram/api/generated/types.gen";

export type InkStroke = GeneratedInkStroke;
export type FormulaVersion = GeneratedFormulaVersion;
export type MathInkRecognitionResult = GeneratedMathInkRecognitionResult;

export class MathInkApi {
  constructor(private readonly client: ApiClient) {}

  async recognize(command: RecognizeMathInkCommand, idempotencyKey: string) {
    return (
      await this.client.post<MathInkRecognitionResult>("/api/v2/math-ink/recognitions", command, {
        idempotencyKey,
        timeoutMs: 45_000,
      })
    ).data;
  }

  async confirm(formula: FormulaVersion, latex: string) {
    const command: ConfirmFormulaCommand = {
      expectedVersionNo: formula.versionNo,
      latex,
    };
    return (
      await this.client.post<FormulaVersion>(
        `/api/v2/math-ink/formulas/${encodeURIComponent(formula.formulaVersionId)}/confirm`,
        command,
        { timeoutMs: 30_000 },
      )
    ).data;
  }
}
