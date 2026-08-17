import { describe, expect, it, vi } from "vitest";

import { ExternalAIConsentCoordinator } from "../../../subpackage-shared-src/api/external-ai-consent";
import type {
  ExternalAIConsentApi,
  ExternalAIConsentView,
} from "../../../subpackage-shared-src/api/external-ai-consent";

function view(state: ExternalAIConsentView["state"]): ExternalAIConsentView {
  return {
    contractVersion: "external-ai-consent-view@v1",
    policyVersion: "xingce-cause-external-ai@v1",
    state,
    consentEventId: state === "accepted" ? "consent-037" : null,
    acceptedAt: state === "accepted" ? "2026-08-17T00:00:00Z" : null,
    withdrawnAt: state === "withdrawn" ? "2026-08-17T00:00:00Z" : null,
    title: "岸岸外部 AI 错因分析说明",
    summary: "生成错因候选",
    dataSent: ["作答结果"],
    providerRisk: "处理方规则可能变化",
    withdrawalEffect: "撤回后不再发送新内容",
    processors: [],
    generatedAt: "2026-08-17T00:00:00Z",
  };
}

describe("external AI first-use consent race", () => {
  it("deduplicates concurrent taps and stays silent after one acceptance", async () => {
    const current = vi.fn().mockResolvedValue({ data: view("not_accepted") });
    const accept = vi.fn().mockResolvedValue({ data: view("accepted") });
    const api = { current, accept } as unknown as ExternalAIConsentApi;
    const coordinator = new ExternalAIConsentCoordinator(api, "reflection_first_use");
    const confirmOnce = vi.fn().mockResolvedValue(true);
    const commandKey = vi.fn().mockResolvedValue("consent-command-key-037");

    const results = await Promise.all([
      coordinator.ensureAccepted(confirmOnce, commandKey),
      coordinator.ensureAccepted(confirmOnce, commandKey),
      coordinator.ensureAccepted(confirmOnce, commandKey),
    ]);

    expect(results).toEqual([true, true, true]);
    expect(current).toHaveBeenCalledTimes(1);
    expect(confirmOnce).toHaveBeenCalledTimes(1);
    expect(commandKey).toHaveBeenCalledTimes(1);
    expect(accept).toHaveBeenCalledTimes(1);

    await expect(coordinator.ensureAccepted(confirmOnce, commandKey)).resolves.toBe(true);
    expect(confirmOnce).toHaveBeenCalledTimes(1);
    expect(accept).toHaveBeenCalledTimes(1);
  });
});
