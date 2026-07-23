/**
 * 路由重映射层
 *
 * 背景：后端 taskstream / alerts 下发的 startUrl、actionUrl 是按旧版前端
 * （backup/wechat-frontend-pre-redesign，50+ 页面）的路径配置的。
 * 新版只有 20 个页面，直接 navigateTo 会大面积 fail。
 * 这里把旧路径翻译成新路径，后端无需改动。
 */

/** 旧路径 → 新路径（匹配时忽略参数，转发时保留参数） */
const LEGACY_ROUTE_MAP: Record<string, string> = {
  // 练习链路
  "pages/xingce-task-intro/index": "pages/practice/index",
  // 复盘
  "pages/reflection/index": "pages/review/index",
  "pages/error-pattern/index": "pages/review/index",
  // 申论：旧版拆了 5 个页面，新版收敛到工作台一页
  "pages/essay-task/index": "pages/essay-material/index",
  "pages/essay-compose/index": "pages/essay-material/index",
  "pages/essay-review/index": "pages/essay-material/index",
  "pages/essay-rewrite/index": "pages/essay-material/index",
  "pages/essay-evidence/index": "pages/essay-material/index",
  // 报告
  "pages/report-center/index": "pages/mock-report/index",
  "pages/report-overview/index": "pages/mock-report/index",
  "pages/global-report/index": "pages/mock-report/index",
  // 能力细分页 → 能力 Tab
  "pages/xingce-mastery/index": "pages/ability/index",
  "pages/ability-module/index": "pages/ability/index",
  // 目标与岗位
  "pages/positions/index": "pages/goal/index",
  "pages/positions/search/index": "pages/goal/index",
  "pages/positions/detail/index": "pages/goal/index",
  "pages/positions/goal/index": "pages/goal/index",
  // 计划相关
  "pages/day-adjustment/index": "pages/plan-basis/index",
  "pages/weekly-plan/index": "pages/plan-history/index",
  // 社区/知识卡
  "pages/community-draft/index": "pages/method-cards/publish",
  "pages/community-structure/index": "pages/method-cards/index",
  "pages/community-trial/index": "pages/method-cards/index",
  "pages/community-contributions/index": "pages/method-cards/index",
  "pages/learning-packets/index": "pages/method-cards/index",
  "pages/private-knowledge/index": "pages/method-cards/index",
  // 伴学
  "pages/companion/index": "pages/anan/index",
  // 我的 / 设置
  "pages/profile/index": "pages/me/index",
  // 成绩导入（旧版 OCR 页 → 新版自报页）
  "pages/external-report/index": "pages/score-import/index",
  "pages/exam-records/index": "pages/score-import/index",
  "pages/profile/qualification/index": "pages/me/index",
  "pages/learning-profile/index": "pages/me/index",
  "pages/notification-settings/index": "pages/notification/index",
  "pages/membership/index": "pages/about/index",
  "pages/privacy/index": "pages/about/index",
  "pages/support/index": "pages/about/index",
  // 引导子页 → 新版单页引导
  "pages/login/index": "pages/onboarding/index",
  "pages/onboarding/preparation-experience/index": "pages/onboarding/index",
  "pages/onboarding/study-preference/index": "pages/onboarding/index",
  "pages/onboarding/result/index": "pages/onboarding/index",
};

/** 任务 kind → 新页面兜底（路径完全认不出时用） */
const KIND_FALLBACK: Record<string, string> = {
  practice: "pages/practice/index",
  review: "pages/review/index",
  essay: "pages/essay-material/index",
  mock: "pages/mock-exam/index",
  method_card: "pages/method-cards/index",
};

/** 新版四个 Tab 页，必须用 switchTab 且不能带参数 */
const TAB_PAGES = new Set([
  "pages/today/index",
  "pages/ability/index",
  "pages/anan/index",
  "pages/me/index",
]);

/** 与 app.json pages 保持一致的注册表（新增页面时同步维护） */
const REGISTERED = new Set([
  "pages/today/index",
  "pages/ability/index",
  "pages/anan/index",
  "pages/me/index",
  "pages/practice/index",
  "pages/daily-settlement/index",
  "pages/method-cards/index",
  "pages/method-cards/publish",
  "pages/onboarding/index",
  "pages/review/index",
  "pages/essay-material/index",
  "pages/mock-exam/index",
  "pages/mock-report/index",
  "pages/retake/index",
  "pages/placement/index",
  "pages/plan-history/index",
  "pages/study-preference/index",
  "pages/goal/index",
  "pages/notification/index",
  "pages/about/index",
  "pages/score-import/index",
  "pages/plan-basis/index",
]);

function splitUrl(url: string): { path: string; query: string } {
  const clean = url.startsWith("/") ? url.slice(1) : url;
  const qIndex = clean.indexOf("?");
  if (qIndex === -1) return { path: clean, query: "" };
  return { path: clean.slice(0, qIndex), query: clean.slice(qIndex + 1) };
}

function mapKind(kind: string): string | null {
  const target = KIND_FALLBACK[kind];
  return target ? `/${target}` : null;
}

/**
 * 把后端下发的 URL 翻译成新版可跳转的 URL。
 * 返回 null 表示实在认不出（调用方自行兜底提示）。
 */
export function resolveRoute(url: string, kind?: string): string | null {
  if (!url) return kind ? mapKind(kind) : null;

  const { path, query } = splitUrl(url);
  const mapped = LEGACY_ROUTE_MAP[path] ?? path;

  if (REGISTERED.has(mapped)) {
    if (TAB_PAGES.has(mapped)) return `/${mapped}`; // switchTab 不能带 query
    return query ? `/${mapped}?${query}` : `/${mapped}`;
  }
  return kind ? mapKind(kind) : null;
}

/**
 * 智能跳转：自动重映射旧路径，Tab 页走 switchTab，其余走 navigateTo。
 * 认不出的路径提示"稍后再来"，不再出现"开发中"死胡同。
 */
export function navigateSmart(url: string, kind?: string): void {
  const resolved = resolveRoute(url, kind);
  if (!resolved) {
    wx.showToast({ title: "这个入口稍后开放，先做今天的任务", icon: "none" });
    return;
  }
  const { path } = splitUrl(resolved);
  if (TAB_PAGES.has(path)) {
    wx.switchTab({ url: resolved });
    return;
  }
  wx.navigateTo({
    url: resolved,
    fail: () => wx.showToast({ title: "这个入口稍后开放，先做今天的任务", icon: "none" }),
  });
}
