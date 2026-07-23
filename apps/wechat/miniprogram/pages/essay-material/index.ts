import {
  createEssayAnswer,
  createEssayAttempt,
  ensureEssayReview,
  fetchEssayWorkspace,
} from "../../api/endpoints";
import type { EssayFinding, EssayTaskWorkspace } from "../../api/generated/types.gen";

type SegmentVM = { segmentId: string; order: string; text: string };
type FindingVM = {
  findingId: string;
  severityLabel: string;
  severityClass: string;
  attributionLabel: string;
  diagnosis: string;
  action: string;
};

const QUESTION_TYPE_LABEL: Record<string, string> = {
  summary: "归纳概括",
  comprehensive_analysis: "综合分析",
  countermeasure: "提出对策",
  implementation: "贯彻执行",
  composition: "文章写作",
};

const ATTRIBUTION_LABEL: Record<string, string> = {
  not_found: "材料里没找到",
  found_not_written: "找到了但没写",
  written_imprecisely: "写了但不准",
  unknown: "其他",
};

const SEVERITY: Record<string, { label: string; cls: string }> = {
  high: { label: "重伤", cls: "is-high" },
  medium: { label: "扣分", cls: "is-mid" },
  low: { label: "小瑕疵", cls: "is-low" },
};

const DEMO_SEGMENTS: SegmentVM[] = [
  {
    segmentId: "demo-s1",
    order: "材料一",
    text: "近年来，S 市持续推进社区养老服务改革。全市已建成社区养老服务中心 128 个，覆盖 85% 的街道。但调研发现，部分中心「建而不用」：日均到访老人不足 10 人，助餐、康复等核心服务开展率仅四成。",
  },
  {
    segmentId: "demo-s2",
    order: "材料二",
    text: "「不是不想去，是不知道有什么。」家住幸福里社区的王大爷说。多位受访老人表示，服务中心的活动信息主要靠楼道张贴，子女不在身边的老人很难及时知晓。此外，部分中心的开放时间与老人作息错位，「上午九点开门，我们六点就起了」。",
  },
];

const DEMO_PROMPT =
  "根据给定材料，概括 S 市社区养老服务中心「建而不用」的主要原因。要求：全面、准确、有条理，不超过 200 字。";

const DEMO_FINDINGS: FindingVM[] = [
  {
    findingId: "demo-f1",
    severityLabel: "重伤",
    severityClass: "is-high",
    attributionLabel: "找到了但没写",
    diagnosis: "材料二里「开放时间与老人作息错位」是一条独立原因，你的答案漏掉了。",
    action: "概括题先把每段材料的问题词圈出来再合并，不要读完凭印象写。",
  },
  {
    findingId: "demo-f2",
    severityLabel: "扣分",
    severityClass: "is-mid",
    attributionLabel: "写了但不准",
    diagnosis: "「宣传不到位」太笼统，材料说的是「信息渠道单一，依赖楼道张贴」。",
    action: "用材料里的具体表述替换你的概念词，踩点给分踩的是关键词。",
  },
];

Page({
  data: {
    loading: true,
    usingDemo: false,
    phase: "write" as "write" | "grading" | "result",
    // 工作台
    questionTypeLabel: "",
    prompt: "",
    requirements: "",
    wordLimit: 0,
    maximumScore: 0,
    segments: [] as SegmentVM[],
    // 作答
    answerText: "",
    wordCount: 0,
    submitting: false,
    // 结果
    findings: [] as FindingVM[],
    reviewPending: false,
  },
  _taskVersionId: "",
  _materialBundleVersionId: "",
  _rubricVersionId: "",
  _attemptId: "",
  _startedAt: 0,

  onLoad(query: Record<string, string>) {
    this._taskVersionId = query.taskVersionId || "";
    this._startedAt = Date.now();
    void this.load();
  },

  async load() {
    if (!this._taskVersionId) {
      this.applyDemo();
      return;
    }
    try {
      const ws = await fetchEssayWorkspace(this._taskVersionId);
      this.applyWorkspace(ws);
    } catch (err) {
      console.warn("[essay] workspace fallback to demo", err);
      this.applyDemo();
    }
  },

  applyDemo() {
    this.setData({
      loading: false,
      usingDemo: true,
      questionTypeLabel: "归纳概括",
      prompt: DEMO_PROMPT,
      requirements: "全面、准确、有条理",
      wordLimit: 200,
      maximumScore: 20,
      segments: DEMO_SEGMENTS,
    });
  },

  applyWorkspace(ws: EssayTaskWorkspace) {
    this._materialBundleVersionId = ws.material.materialBundleVersionId;
    this._rubricVersionId = ws.rubricAvailability.rubric?.rubricVersionId ?? "";
    this._attemptId = ws.progression.attemptId ?? "";

    const segments: SegmentVM[] = ws.material.segments.map((s) => ({
      segmentId: s.segmentId,
      order: `材料${["一", "二", "三", "四", "五", "六", "七", "八", "九", "十"][s.materialOrder - 1] ?? s.materialOrder}`,
      text: s.text,
    }));

    // 已有批改直接进结果页
    const review = ws.progression.currentReview;
    if (review && review.status === "completed") {
      this.setData({
        loading: false,
        phase: "result",
        questionTypeLabel: QUESTION_TYPE_LABEL[ws.task.questionType] ?? ws.task.questionType,
        prompt: ws.task.prompt,
        segments,
        findings: review.findings.map(toFindingVM),
      });
      return;
    }

    this.setData({
      loading: false,
      questionTypeLabel: QUESTION_TYPE_LABEL[ws.task.questionType] ?? ws.task.questionType,
      prompt: ws.task.prompt,
      requirements: ws.task.requirements ?? "",
      wordLimit: ws.task.wordLimit,
      maximumScore: ws.task.maximumScore,
      segments,
      answerText: ws.progression.currentAnswer?.answerText ?? "",
      wordCount: (ws.progression.currentAnswer?.answerText ?? "").length,
    });
  },

  onInput(e: WechatMiniprogram.TextareaInput) {
    const value = e.detail.value;
    this.setData({ answerText: value, wordCount: value.length });
  },

  async onSubmit() {
    const text = this.data.answerText.trim();
    if (!text || this.data.submitting) return;
    if (text.length < 30) {
      wx.showToast({ title: "再写一点，30 字以上才值得批", icon: "none" });
      return;
    }
    this.setData({ submitting: true });

    if (this.data.usingDemo) {
      // 演示模式：直接出示例批改
      this.setData({ phase: "grading" });
      setTimeout(() => {
        this.setData({ phase: "result", findings: DEMO_FINDINGS, submitting: false });
      }, 1200);
      return;
    }

    try {
      const elapsedSeconds = Math.max(1, Math.round((Date.now() - this._startedAt) / 1000));
      if (!this._attemptId) {
        const created = await createEssayAttempt(
          {
            taskVersionId: this._taskVersionId,
            materialBundleVersionId: this._materialBundleVersionId,
            rubricVersionId: this._rubricVersionId,
            sourceContext: "in_program",
            timedStatus: "untimed",
            elapsedSeconds,
          },
          `attempt-${this._taskVersionId}`,
        );
        this._attemptId = created.attempt.attemptId;
      }

      await createEssayAnswer(
        {
          attemptId: this._attemptId,
          answerText: text,
          sourceType: "typed",
        },
        `answer-${this._attemptId}-${text.length}`,
      );

      this.setData({ phase: "grading" });
      const ensured = await ensureEssayReview(
        {
          taskVersionId: this._taskVersionId,
          materialBundleVersionId: this._materialBundleVersionId,
          rubricVersionId: this._rubricVersionId,
          reviewScope: "grounded_full",
        },
        `review-${this._attemptId}`,
      );

      if (ensured.job) {
        // 批改在排队：告知用户稍后回来看
        this.setData({ phase: "result", reviewPending: true, submitting: false });
        return;
      }
      // 立即可用：重新拉工作台拿 findings
      const ws = await fetchEssayWorkspace(this._taskVersionId);
      const review = ws.progression.currentReview;
      this.setData({
        phase: "result",
        findings: review?.findings.map(toFindingVM) ?? [],
        reviewPending: !review || review.status !== "completed",
        submitting: false,
      });
    } catch (err) {
      console.error("[essay] submit failed", err);
      this.setData({ submitting: false, phase: "write" });
      wx.showToast({ title: "提交没成功，稿子还在，再试一次", icon: "none" });
    }
  },

  onBackToday() {
    wx.switchTab({ url: "/pages/today/index" });
  },

  onBack() {
    wx.navigateBack({ fail: () => wx.switchTab({ url: "/pages/today/index" }) });
  },
});

function toFindingVM(f: EssayFinding): FindingVM {
  const sev = SEVERITY[f.severity] ?? { label: f.severity, cls: "is-low" };
  return {
    findingId: f.findingId,
    severityLabel: sev.label,
    severityClass: sev.cls,
    attributionLabel: ATTRIBUTION_LABEL[f.attribution] ?? "",
    diagnosis: f.diagnosis,
    action: f.action,
  };
}
