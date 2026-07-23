import { askCompanion } from "../../api/endpoints";
import type {
  CompanionAskResponse,
  GlobalAskContext,
} from "../../api/generated/types.gen";

type ChatMsg = {
  id: string;
  role: "user" | "anan";
  text: string;
  intent?: CompanionAskResponse["companionIntent"];
  /** 高影响决策标记：岸岸给出与用户意愿相反的建议时为 true */
  highImpact?: boolean;
  citations?: CompanionAskResponse["citations"];
  allowedActions?: CompanionAskResponse["allowedActions"];
};

/** 预设快捷入口：不会打字的用户点按钮，按钮本身即教学 */
const PRESETS = [
  { key: "no-time", text: "我今天没时间了" },
  { key: "too-hard", text: "这些题太难了，跟不上" },
  { key: "replan", text: "帮我调整一下计划" },
  { key: "low-mood", text: "我最近状态不太好" },
];

/** 本地兜底应答：后端不可达时也能演示对话与高影响决策卡 */
function demoReply(question: string): CompanionAskResponse {
  const wantsMore = /加练|多练|再来|三小时|加量/.test(question);
  const base: CompanionAskResponse = {
    allowedActions: [],
    answer: "",
    answerId: `demo-${Date.now()}`,
    answerType: "text",
    citations: [],
    companionIntent: "companion",
    confidence: "medium",
    knowledgeFreshness: "demo",
    limitations: [],
    policyDecision: "allow",
    quota: {} as CompanionAskResponse["quota"],
    respondingAgent: "planner",
    scenarioKey: "demo",
    semanticState: "ready",
    highImpactDecision: null,
  };
  if (/没时间/.test(question)) {
    return {
      ...base,
      companionIntent: "deprioritize",
      answer:
        "没事，今天就别硬扛了。我把申论素材那项挪到明天，只保留言语这一件核心的，20 分钟就能收。做完这一件今天就算达标。",
      allowedActions: [
        { kind: "adjust_plan_preview", label: "按这样调整", requiresConfirmation: false },
        { kind: "refresh_context", label: "还是全做完", requiresConfirmation: false },
      ],
    };
  }
  if (/状态|情绪|难受|焦虑/.test(question)) {
    return {
      ...base,
      companionIntent: "encouragement",
      answer:
        "备考到这个阶段状态起伏很正常，不代表你不行。要不今天先做点轻的，把复盘那 8 道错题过一遍就好，找回一点掌控感。剩下的明天再说。",
    };
  }
  if (/坚持/.test(question)) {
    return {
      ...base,
      answer:
        "好，那就按你说的来，最终决定权在你。我把申论加进明天的计划，三天后我们一起看总分数据——说到做到。",
      allowedActions: [
        { kind: "adjust_plan_preview", label: "看看调整后的计划", requiresConfirmation: false },
      ],
    };
  }
  if (wantsMore) {
    return {
      ...base,
      companionIntent: "thinking",
      answer:
        "我理解你想冲一把，但我得说句实话：按你目前言语理解 62% 的正确率，把时间大量压给申论，短期内总分反而会掉。我建议维持言语为主、申论保温，两周后看数据再决定。如果你坚持，我会照办，但三天后我们一起看总分有没有变化。",
      highImpactDecision: {
        decisionType: "strategic_deprioritization",
        evidenceRefs: [],
        prohibitedMutations: [],
        status: "explain_only",
      },
      allowedActions: [
        { kind: "adjust_plan_preview", label: "听你的，先按现在的来", requiresConfirmation: false },
        { kind: "view_evidence", label: "我坚持要加练", requiresConfirmation: true },
      ],
    };
  }
  return {
    ...base,
    answer:
      "我在的。你可以问我今天为什么这么安排、某道题怎么想，或者直接告诉我你的状态，我帮你调计划。",
  };
}

Page({
  data: {
    messages: [] as ChatMsg[],
    presets: PRESETS,
    input: "",
    sending: false,
    usingDemo: false,
    scrollTo: "",
  },

  onShow() {
    if (typeof this.getTabBar === "function") {
      const tabBar = this.getTabBar();
      if (tabBar) tabBar.setData({ active: "anan" });
    }
    if (this.data.messages.length === 0) {
      this.pushMsg({
        id: "welcome",
        role: "anan",
        text: "我是岸岸，陪你一起上岸的那个。今天有什么想聊的，或者直接告诉我你的状态？",
        intent: "companion",
      });
    }
  },

  buildContext(): GlobalAskContext {
    return {
      learnerStateVersion: "latest",
      routeId: "pages/anan/index",
      workspace: "today",
    };
  },

  pushMsg(msg: ChatMsg) {
    const messages = this.data.messages.concat(msg);
    this.setData({ messages, scrollTo: `msg-${msg.id}` });
  },

  onInput(e: WechatMiniprogram.Input) {
    this.setData({ input: e.detail.value });
  },

  onPreset(e: WechatMiniprogram.TouchEvent) {
    const { text } = e.currentTarget.dataset as { text: string };
    this.send(text);
  },

  onSend() {
    const text = this.data.input.trim();
    if (text) this.send(text);
  },

  async send(text: string) {
    if (this.data.sending) return;
    this.pushMsg({ id: `u-${Date.now()}`, role: "user", text });
    this.setData({ input: "", sending: true });

    let reply: CompanionAskResponse;
    let usingDemo = this.data.usingDemo;
    try {
      reply = await askCompanion({
        context: this.buildContext(),
        questionText: text,
      });
    } catch (err) {
      console.error("[anan] ask failed, fallback to demo", err);
      reply = demoReply(text);
      usingDemo = true;
    }

    this.pushMsg({
      id: reply.answerId || `a-${Date.now()}`,
      role: "anan",
      text: reply.answer,
      intent: reply.companionIntent,
      highImpact: !!reply.highImpactDecision,
      citations: reply.citations,
      allowedActions: reply.allowedActions,
    });
    this.setData({ sending: false, usingDemo });
  },

  onAction(e: WechatMiniprogram.TouchEvent) {
    const { label } = e.currentTarget.dataset as { label: string };
    // 点选项即继续对话：选择本身就是一次表态
    this.send(label);
  },
});
