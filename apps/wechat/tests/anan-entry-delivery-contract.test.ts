import { describe, expect, it } from "vitest";

// @ts-expect-error JavaScript visual fixtures intentionally run in Node and do not ship declarations.
import { pageInteractionMatrix } from "../e2e/page-interaction-matrix.mjs";
// @ts-expect-error JavaScript visual fixtures intentionally run in Node and do not ship declarations.
import { visualDeliveryContracts } from "../e2e/visual-delivery-contracts.mjs";
// @ts-expect-error JavaScript visual fixtures intentionally run in Node and do not ship declarations.
import { visualScenarios } from "../e2e/visual-scenarios.mjs";

interface DeliveryContract {
  scenarioId: string;
  requiredText: string[];
  primarySelector?: string;
}

interface PageInteractionContract {
  route: string;
  purpose: string;
  primaryInteraction: {
    handler: string | null;
    observable: boolean;
    evidence: string;
  };
}

interface VisualScenario {
  id: string;
  data: { errorMessage?: string };
}

const contracts = visualDeliveryContracts as DeliveryContract[];
const interactions = pageInteractionMatrix as PageInteractionContract[];
const scenarios = visualScenarios as VisualScenario[];

describe("岸岸静默入口交付契约", () => {
  it("把默认入口定义为自动只读状态，并只在失败态提供重新进入", () => {
    const contract = contracts.find((item) => item.scenarioId === "a01-login-default");
    expect(contract).toMatchObject({
      requiredText: ["正在进入岸岸", "正在准备你的备考空间"],
    });
    expect(contract).not.toHaveProperty("primarySelector");

    const interaction = interactions.find((item) => item.route === "pages/login/index");
    expect(interaction).toMatchObject({
      purpose: "自动恢复或建立微信身份",
      primaryInteraction: {
        handler: null,
        observable: false,
        evidence: "runtime_read_only",
      },
    });

    const failure = scenarios.find((item) => item.id === "a03-login-typed-error");
    expect(failure?.data.errorMessage).toContain("重新进入岸岸");
    expect(failure?.data.errorMessage).not.toContain("重新登录");
  });

  it("今日视觉契约跟随卡片内的新解释入口", () => {
    const today = contracts.find((item) => item.scenarioId === "b01-today-ready");
    expect(today?.requiredText).toContain("为什么今天先做这个");
  });
});
