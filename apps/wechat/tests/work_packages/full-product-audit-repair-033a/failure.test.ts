import { describe, expect, it } from "vitest";

import { ApiClientError } from "../../../miniprogram/api/client";
import { errorView } from "../../../miniprogram/features/learning-brain/presentation";
import { resolveQuickDiagnostic } from "../../../miniprogram/pages/onboarding/features/coach-recognition-routing";
import { presentErrorPatternEvidence } from "../../../miniprogram/reports/error-pattern/features/evidence-presentation";
import {
  buildExternalScoreDraftCommand,
  initialExternalScoreForm,
} from "../../../miniprogram/reports/external-report/features/form";
import { prototypeToday } from "../../fixtures/prototype-data";
import { recognition } from "../../external-learning/fixtures";

describe("full product audit repair 033a failure path", () => {
  it("identifies the exact missing or invalid score field without blaming OCR", () => {
    const emptyName = initialExternalScoreForm(new Date("2026-08-16T08:00:00+08:00"));
    expect(() => buildExternalScoreDraftCommand(emptyName, recognition())).toThrow(
      expect.objectContaining({
        field: "examName",
        message: "请填写模考名称。",
      }),
    );

    const invalidScore = { ...emptyName, examName: "第 18 季模考", totalScore: "-1" };
    expect(() => buildExternalScoreDraftCommand(invalidScore, recognition())).toThrow(
      expect.objectContaining({
        field: "totalScore",
        message: "总分必须是非负数字。",
      }),
    );
  });

  it("gives transport failures an actionable network explanation", () => {
    expect(errorView(new ApiClientError("网络请求失败。", 0, "request-offline"), "失败")).toEqual({
      message: "网络没有连上或响应超时，请检查网络后再试一次。",
      requestId: "request-offline",
    });
    expect(
      errorView(
        {
          name: "ApiClientError",
          message: "网络请求失败。",
          statusCode: 0,
          requestId: "cross-package",
        },
        "失败",
      ),
    ).toEqual({
      message: "网络没有连上或响应超时，请检查网络后再试一次。",
      requestId: "cross-package",
    });
    expect(
      errorView(new ApiClientError("gateway timeout", 504, "request-timeout"), "失败"),
    ).toEqual({
      message: "请求超时了，请稍后再试一次。",
      requestId: "request-timeout",
    });
  });

  it("does not expose an unknown evidence role and does not invent a diagnostic task", () => {
    expect(
      presentErrorPatternEvidence({
        owner: "future_agent",
        entityType: "future_fact",
        entityId: "future-a",
        role: "internal_future_role",
      }).label,
    ).toBe("相关学习记录");

    const dashboard = JSON.parse(JSON.stringify(prototypeToday)) as typeof prototypeToday;
    dashboard.tasks = [];
    expect(resolveQuickDiagnostic(dashboard)).toBeNull();
  });
});
