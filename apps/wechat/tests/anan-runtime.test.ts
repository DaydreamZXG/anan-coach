import { describe, expect, it } from "vitest";

import { ApiClientError } from "../miniprogram/api/client";
import {
  ANAN_INTENT_LABELS,
  resolveAnanVisual,
  type AnanIntent,
} from "../miniprogram/design-system/companion/anan-companion/runtime";
import { companionFailureView } from "../miniprogram/features/companion/presentation";

describe("岸岸小程序运行时", () => {
  it("按语义请求选择正式资产、动效和无障碍名称", () => {
    for (const intent of Object.keys(ANAN_INTENT_LABELS) as AnanIntent[]) {
      const resolved = resolveAnanVisual({
        intent,
        size: "animated-120",
        label: "",
        animate: true,
        motion: "allowed",
        context: "post-submit",
        visualStage: "",
      });
      expect(resolved).not.toBeNull();
      expect(resolved?.label).toBe(ANAN_INTENT_LABELS[intent]);
      expect(resolved?.motionAllowed).toBe(true);
      expect(resolved?.renderIntentPackage).toBe(intent !== "companion");
    }
  });

  it("将减少动效、题目阅读、大尺寸和专用头像落实为确定性静态规则", () => {
    const reduced = resolveAnanVisual({
      intent: "thinking",
      size: "animated-120",
      label: "",
      animate: true,
      motion: "reduced",
      context: "plan",
      visualStage: "",
    });
    const reading = resolveAnanVisual({
      intent: "reminder",
      size: "medium",
      label: "",
      animate: true,
      motion: "allowed",
      context: "question-reading",
      visualStage: "",
    });
    const hero = resolveAnanVisual({
      intent: "companion",
      size: "hero-240",
      label: "阶段形象",
      animate: true,
      motion: "allowed",
      context: "report",
      visualStage: "stage-3",
    });
    const avatar = resolveAnanVisual({
      intent: "thinking",
      size: "avatar-32",
      label: "",
      animate: true,
      motion: "allowed",
      context: "question-reading",
      visualStage: "",
    });

    expect(reduced?.motionAllowed).toBe(false);
    expect(reading?.motionAllowed).toBe(false);
    expect(hero).toMatchObject({ renderGrowth: true, motionAllowed: false });
    expect(avatar?.staticSrc).toContain("avatar-32@3x.webp");
    expect(avatar?.renderIntentPackage).toBe(false);
  });

  it("拒绝未登记语义，不拿其他形象兜底", () => {
    expect(
      resolveAnanVisual({
        intent: "happy",
        size: "medium",
        label: "",
        animate: true,
        motion: "allowed",
        context: "home",
        visualStage: "",
      }),
    ).toBeNull();
    expect(
      resolveAnanVisual({
        intent: "companion",
        size: "medium",
        label: "",
        animate: true,
        motion: "allowed",
        context: "unknown",
        visualStage: "",
      }),
    ).toBeNull();
  });

  it("外部回答服务未获安全接入时说明未发送资料并保留问题编号", () => {
    const view = companionFailureView(
      new ApiClientError("刚才这一步没有成功，请稍后再试一次。", 503, "mp-anan-503"),
    );

    expect(view).toEqual({
      semanticState: "provider_pending",
      message:
        "岸岸的回答服务尚未完成安全接入，本次没有把你的学习资料发给外部模型。请保留问题编号，服务恢复后再试。",
      requestId: "mp-anan-503",
    });
  });
});
