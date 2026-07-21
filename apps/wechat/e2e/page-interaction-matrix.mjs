const BINDING_CONTROLLER = "binding_controller";
const RUNTIME_READ_ONLY = "runtime_read_only";
const PLATFORM_DEVICE = "platform_device";

function page(
  route,
  scenarioId,
  purpose,
  {
    handler = null,
    selector = null,
    index = 0,
    within = null,
    evidence = null,
    dataset = {},
    observable = true,
  } = {},
) {
  return {
    id: `page-${route
      .replace(/^pages\//u, "")
      .replace(/\/index$/u, "")
      .replaceAll("/", "-")}`,
    route,
    scenarioId,
    purpose,
    primaryInteraction: handler
      ? {
          handler,
          selector,
          index,
          within,
          dataset,
          observable,
          evidence: evidence ?? BINDING_CONTROLLER,
        }
      : {
          handler: null,
          selector: null,
          index: 0,
          within: null,
          dataset: {},
          observable: false,
          evidence: evidence ?? RUNTIME_READ_ONLY,
        },
  };
}

/**
 * One purpose-level contract per registered page. This is deliberately separate from
 * visual fixtures: a screenshot proves rendering, while this matrix owns the primary
 * user action (or the explicit reason why a page is read-only).
 */
export const pageInteractionMatrix = [
  page("pages/login/index", "a01-login-default", "建立微信身份", {
    handler: "login",
    selector: ".wechat-button",
  }),
  page("pages/today/index", "b01-today-ready", "从今日计划进入当前任务", {
    handler: "openTask",
    selector: ".task",
    dataset: { id: "today-task-1" },
  }),
  page("pages/positions/index", "d01-position-workspace-needs-profile", "补齐资格后再做岗位判断", {
    handler: "openOnboarding",
    selector: "ui-button",
    within: "state-view",
  }),
  page("pages/positions/search/index", "d02-position-search-ready", "打开筛选后的岗位依据", {
    handler: "openPosition",
    selector: ".position-card",
    dataset: { id: "position-1" },
  }),
  page("pages/positions/detail/index", "d03-position-detail-ready", "确认单条资格事实", {
    handler: "confirmRequirement",
    selector: ".confirmation-action",
    dataset: { requirementId: "degree-1", response: "satisfied" },
    observable: false,
  }),
  page("pages/positions/compare/index", "d04-position-compare-ready", "回看对比岗位的逐条依据", {
    handler: "openPosition",
    selector: ".inline-link",
    dataset: { id: "position-1" },
  }),
  page("pages/positions/goal/index", "d05-position-goal-preview", "阅读目标变更影响预览"),
  page("pages/positions/briefing/index", "d06-position-briefing-ready", "阅读岗位考情与目标分依据"),
  page("pages/profile/index", "f09-profile-ready", "进入个人学习档案", {
    handler: "openLearningProfile",
    selector: ".profile-menu-row",
  }),
  page(
    "pages/weather-attribution/index",
    "h06-weather-attribution-boundary",
    "阅读 Apple Weather 数据来源边界",
    { evidence: RUNTIME_READ_ONLY },
  ),
  page(
    "pages/notification-settings/index",
    "h05-coach-notification-settings-ready",
    "保存 AI 督学偏好",
    {
      handler: "save",
      selector: ".save-button",
    },
  ),
  page("pages/membership/index", "g01-open-access-ready", "说明免费冷启动边界", {
    evidence: RUNTIME_READ_ONLY,
  }),
  page("pages/privacy/index", "g02-privacy-ready", "创建个人数据导出", {
    handler: "createExport",
    selector: ".privacy-secondary",
  }),
  page("pages/support/index", "f10-support-placeholder", "预览客服群二维码", {
    handler: "previewGroupQr",
    selector: ".qr-action",
    evidence: PLATFORM_DEVICE,
  }),
  page("pages/onboarding/index", "a04-qualification-review", "提交资格档案", {
    handler: "next",
    selector: ".s9-primary-button",
    observable: false,
  }),
  page(
    "pages/onboarding/preparation-experience/index",
    "a03-preparation-experience-ready",
    "确认备考经历分流",
    { handler: "submit", selector: ".s9-primary-button", observable: false },
  ),
  page(
    "pages/onboarding/study-preference/index",
    "a06-study-preference-ready",
    "确认半小时粒度学习时间",
    {
      handler: "submit",
      selector: ".s9-primary-button",
      observable: false,
    },
  ),
  page("pages/onboarding/result/index", "a07-onboarding-result-ready", "确认建档结果与下一步", {
    handler: "acknowledge",
    selector: ".s9-primary-button",
    observable: false,
  }),
  page("pages/retake/index", "a08-retake-workspace-ready", "建立二战失分回收计划", {
    handler: "startProgram",
    selector: ".s9-primary-button",
    observable: false,
  }),
  page("pages/xingce-task-intro/index", "c01-xingce-briefing-ready", "开始真实行测任务", {
    handler: "startTask",
    selector: ".s9-primary-button",
    observable: false,
  }),
  page("pages/practice/index", "c02-xingce-answering-toolbar", "调整长题阅读显示", {
    handler: "toggleDisplayEyeCare",
    selector: ".task-tool-item",
  }),
  page("pages/mock-exam/index", "c06-mock-exam-answering", "调整模考长题阅读显示", {
    handler: "toggleEyeCare",
    selector: ".tool-chip",
  }),
  page("pages/report-center/index", "f01-report-center-ready", "进入外部模考成绩确认", {
    handler: "openExternalScore",
    selector: ".external-card",
  }),
  page("pages/mock-report/index", "f03-mock-report-ready", "把模考结论带回今日计划", {
    handler: "openNextAction",
    selector: ".report-next-action",
  }),
  page("pages/external-report/index", "f04-external-score-final-review", "确认或重新录入外部成绩", {
    handler: "restart",
    selector: ".link-action",
  }),
  page("pages/report-overview/index", "f02-report-overview-ready", "把周期报告结论带回计划", {
    handler: "openNextAction",
    selector: ".report-next-action",
  }),
  page("pages/global-report/index", "f13-global-report-ready", "把全局报告结论带回计划", {
    handler: "openNextAction",
    selector: ".report-next-action",
  }),
  page("pages/reflection/index", "c04-xingce-reflection-voice", "记录用户原话与方法判断", {
    handler: "toggleTag",
    selector: ".tag-chip",
    dataset: { code: "misread" },
  }),
  page("pages/xingce-mastery/index", "c08-xingce-mastery-queue", "打开到期复测项", {
    handler: "openItem",
    selector: ".card-action",
    dataset: { itemid: "visual-mastery-item-001" },
  }),
  page("pages/essay-material/index", "f10-essay-material-map", "带着材料地图进入作答", {
    handler: "openWriter",
    selector: ".workbench-action",
  }),
  page("pages/essay-compose/index", "f11-essay-compose-workbench", "进入申论成稿工作区", {
    handler: "openDrafting",
    selector: ".workbench-action",
    index: 1,
  }),
  page("pages/essay-review/index", "f06-essay-review-evidence", "阅读多模型评阅证据"),
  page("pages/essay-rewrite/index", "f07-essay-rewrite-comparison", "阅读原稿与重写对照"),
  page("pages/essay-evidence/index", "f12-essay-grounding-evidence", "阅读申论证据引用"),
  page("pages/essay-task/index", "f05-essay-rewrite-ready", "维护申论结构化输入", {
    handler: "addPoint",
    selector: ".add-button",
    observable: false,
  }),
  page("pages/capture-import/index", "h01-capture-search-idle", "选择拍题或资料导入模式", {
    handler: "chooseMode",
    selector: ".capture-mode",
    index: 1,
    dataset: { mode: "import" },
  }),
  page("pages/learning-packets/index", "h02-learning-packets-ready", "切换学习包学科", {
    handler: "chooseTrack",
    selector: ".choice",
    index: 1,
    dataset: { value: "essay" },
  }),
  page("pages/private-knowledge/index", "h04-private-knowledge-ready", "检索个人私有知识", {
    handler: "search",
    selector: ".search-button",
  }),
  page("pages/answer-ingestion/index", "h03-answer-ingestion-draft", "选择答案录入方式", {
    handler: "chooseMode",
    selector: ".choice",
    index: 1,
    dataset: { value: "answer_image" },
  }),
  page("pages/community-draft/index", "c21-community-draft-private", "选择私人方法来源", {
    handler: "chooseSource",
    selector: ".choice-card",
    dataset: { value: "voice_transcript" },
  }),
  page("pages/community-structure/index", "c22-community-structure-confirm", "确认方法卡权利声明", {
    handler: "chooseRights",
    selector: ".rights-row",
    dataset: { value: "private_third_party" },
  }),
  page("pages/community-trial/index", "c23-community-independent-trial", "主动加入方法试用", {
    handler: "optIn",
    selector: ".action-button",
  }),
  page(
    "pages/community-contributions/index",
    "c24-community-contributions-ledger",
    "阅读贡献与奖励账本",
  ),
  page("pages/plan-basis/index", "b02-plan-basis-ready", "阅读计划生成依据"),
  page("pages/day-adjustment/index", "b03-day-adjustment-ready", "调整今日精力尺度", {
    handler: "chooseScale",
    selector: ".scale-option",
    dataset: { field: "energy", value: 1 },
  }),
  page("pages/daily-settlement/index", "b04-daily-settlement-ready", "阅读当日闭环结算"),
  page("pages/weekly-plan/index", "b05-weekly-plan-ready", "阅读周计划节奏"),
  page("pages/learning-profile/index", "e01-learning-profile-ready", "打开模块能力画像", {
    handler: "openModule",
    selector: ".brain-row",
    dataset: { track: "xingce", key: "资料分析" },
  }),
  page("pages/ability-module/index", "e02-ability-module-ready", "选择有期限的战略取舍", {
    handler: "chooseDecisionType",
    selector: ".brain-pill",
    dataset: { type: "exam_skip" },
  }),
  page("pages/error-pattern/index", "e03-error-pattern-ready", "阅读错误模式原始证据"),
  page("pages/companion/index", "e04-companion-grounded-answer", "沿证据允许动作继续处理", {
    handler: "handleAction",
    selector: ".action-button",
    dataset: { index: 0 },
  }),
  page("pages/plan-history/index", "e05-plan-history-ready", "阅读计划变更记录"),
];

export const interactionEvidenceKinds = {
  BINDING_CONTROLLER,
  PLATFORM_DEVICE,
  RUNTIME_READ_ONLY,
};
