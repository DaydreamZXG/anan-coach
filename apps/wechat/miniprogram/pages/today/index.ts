import { authedV2, fetchTodayTaskstream, idemKey } from "../../api/endpoints";
import { ensureSession } from "../../api/auth";
import { ApiError } from "../../api/client";
import { config } from "../../config/env";
import type {
  AppDomainsTaskstreamSchemasTodayTask,
  DailyOpeningView,
  ImpressionCommand,
  TodayTaskstream,
  WeatherLocationCommand,
} from "../../api/generated/types.gen";

type ViewState = "loading" | "ready" | "error";

/** 每日开场状态机：ready 展示，hidden 不渲染（每天只展示一次由服务端 displayState 把关） */
type OpeningStatus = "idle" | "loading" | "ready" | "hidden";

interface TaskView {
  id: string;
  seq: number;
  title: string;
  subtitle: string;
  reason: string;
  estimatedMinutes: number;
  startUrl: string;
  done: boolean;
}

/** 每日开场卡的展示模型（由 DailyOpeningView 推导） */
interface OpeningPresentation {
  localDateLabel: string;
  greeting: string;
  planTitle: string;
  planMinutesLabel: string;
  weatherReady: boolean;
  weatherLocationRequired: boolean;
  weatherUnavailable: boolean;
  weatherHeadline: string;
  weatherDetail: string;
  attributionUrl: string;
  attributionVisible: boolean;
  coffeeVisible: boolean;
  coffeePrompt: string;
}

const CONDITION_LABELS: Record<string, string> = {
  Clear: "晴",
  MostlyClear: "晴间多云",
  PartlyCloudy: "多云",
  MostlyCloudy: "阴",
  Cloudy: "阴",
  Drizzle: "小雨",
  Rain: "有雨",
  HeavyRain: "大雨",
  Thunderstorms: "雷阵雨",
  Snow: "有雪",
  Haze: "有霾",
  Foggy: "有雾",
  Windy: "有风",
};

/** 开场卡展示推导：日期/计划概要/天气/咖啡提示（咖啡窗口 07:00–17:00 由服务端判定） */
function presentDailyOpening(view: DailyOpeningView): OpeningPresentation {
  const summary = view.weather.summary ?? null;
  const condition = summary
    ? (CONDITION_LABELS[summary.conditionCode] ?? summary.conditionCode)
    : "";
  return {
    localDateLabel: `${view.localDate.slice(5).replace("-", "月")}日`,
    greeting: view.greeting,
    planTitle: view.plan.title,
    planMinutesLabel:
      view.plan.estimatedMinutes > 0 ? `${String(view.plan.estimatedMinutes)} 分钟` : "今日收口",
    weatherReady: view.weather.state === "ready" && Boolean(summary),
    weatherLocationRequired: view.weather.state === "location_required",
    weatherUnavailable: view.weather.state === "provider_unavailable",
    weatherHeadline: summary
      ? `${summary.locationLabel} · ${condition} ${String(Math.round(summary.currentCelsius))}°`
      : "",
    weatherDetail: summary
      ? `${String(Math.round(summary.minimumCelsius))}°–${String(Math.round(summary.maximumCelsius))}° · 降水 ${String(Math.round(summary.precipitationChance * 100))}%`
      : "",
    attributionUrl: view.weather.attributionUrl ?? "",
    attributionVisible: Boolean(view.weather.attributionUrl),
    coffeeVisible: view.coffee.showPrompt && view.coffee.state === "available",
    coffeePrompt: view.coffee.prompt ?? "",
  };
}

const ANAN_AVATAR =
  "https://trae-api-cn.mchost.guru/api/ide/v1/text_to_image?prompt=cute%20otter%20mascot%20portrait%2C%20deep%20blue-purple%20fur%2C%20glowing%20cyan%20dot%20on%20chest%2C%20modern%20minimal%203D%20render%2C%20soft%20studio%20lighting%2C%20dark%20navy%20background%2C%20friendly%20calm%20expression&image_size=square";

/** 接口不可达时的演示数据（界面标注「演示数据」） */
const DEMO_STREAM: TodayTaskstream = {
  date: "",
  headline: "晚上好。",
  rationale: "这周资料分析正确率从 55% 回到 71%。节奏刚好，不用加量——把复测做完比多刷 20 题更值。",
  planBasis: "依据：最近 3 组题基期量错 4 道，平均用时超建议 40%；目标岗位近 3 年进面分 128–132。",
  completedTaskIds: [],
  calibration: {} as TodayTaskstream["calibration"],
  primaryAction: {} as TodayTaskstream["primaryAction"],
  target: {
    exam: "2027 国考",
    positionGroup: "税务总局·科员",
    scoreGap: 6.5,
    scoreGapAvailable: true,
  },
  tasks: [
    {
      id: "demo-1",
      kind: "practice",
      title: "资料分析 · 基期量专项",
      subtitle: "12 题",
      reason: "最近 3 组题里这类错了 4 道，最该先追回的分。",
      sourceGrounding: "近 3 组作答记录",
      estimatedMinutes: 25,
      startUrl: "",
    },
    {
      id: "demo-2",
      kind: "practice",
      title: "言语理解 · 错题复测 6 题",
      subtitle: "6 题",
      reason: "3 天前做错的题，今天到期复测。",
      sourceGrounding: "复测排期",
      estimatedMinutes: 15,
      startUrl: "",
    },
    {
      id: "demo-3",
      kind: "essay",
      title: "申论小题 · 找点练习 1 道",
      subtitle: "1 道",
      reason: "本周第二篇，保持找点手感。",
      sourceGrounding: "周节奏 5+1+1",
      estimatedMinutes: 30,
      startUrl: "",
    },
  ] as AppDomainsTaskstreamSchemasTodayTask[],
  trackMix: [],
};

Page({
  data: {
    statusBarHeight: 20,
    capsuleInset: 96,
    viewState: "loading" as ViewState,
    demo: false,
    avatarUrl: ANAN_AVATAR,

    headline: "",
    subline: "",
    liveText: "",
    targetName: "—",
    gapText: "—",
    doneCount: 0,
    totalCount: 0,

    mainline: null as TaskView | null,
    restTasks: [] as TaskView[],
    insight: "",
    planBasis: "",

    openingStatus: "idle" as OpeningStatus,
    openingView: null as OpeningPresentation | null,
    openingRaw: null as DailyOpeningView | null,
    openingWriting: false,

    errorDesc: "",
    requestId: "",
  },

  /** 开场印象每次页面会话只上报一次（服务端按天去重，这里避免刷新重复 POST） */
  openingImpressionDone: false,

  async onLoad() {
    const app = getApp<{ globalData: { statusBarHeight: number; capsuleInset: number } }>();
    this.setData({
      statusBarHeight: app.globalData.statusBarHeight,
      capsuleInset: app.globalData.capsuleInset,
    });
    try {
      const session = await ensureSession();
      if (!session.onboardingComplete) {
        wx.reLaunch({ url: "/pages/onboarding/index/index" });
        return;
      }
    } catch {
      // 登录不可用：走演示/错误态，不阻塞页面
    }
    this.load();
  },

  onShow() {
    this.getTabBar()?.setData({ selected: 0 });
  },

  onPullDownRefresh() {
    this.load().finally(() => wx.stopPullDownRefresh());
  },

  async load() {
    this.setData({ viewState: "loading", demo: false });
    try {
      const stream = await fetchTodayTaskstream();
      this.applyStream(stream, false);
      void this.loadDailyOpening();
    } catch (err) {
      if (config.enableDemoFallback) {
        this.applyStream(DEMO_STREAM, true);
        // 演示态不拉取真实开场卡
        this.setData({ openingStatus: "hidden", openingRaw: null, openingView: null });
        return;
      }
      const apiErr = err as ApiError;
      this.setData({
        viewState: "error",
        errorDesc: "网络不太稳定，稍后再试一次。",
        requestId: apiErr.requestId ?? "",
      });
    }
  },

  applyStream(stream: TodayTaskstream, demo: boolean) {
    const tasks: TaskView[] = (stream.tasks ?? []).map((t, i) => ({
      id: t.id,
      seq: i + 1,
      title: t.title,
      subtitle: t.subtitle ?? "",
      reason: t.reason ?? "",
      estimatedMinutes: t.estimatedMinutes ?? 0,
      startUrl: t.startUrl ?? "",
      done: stream.completedTaskIds?.includes(t.id) ?? false,
    }));

    const remaining = tasks.filter((t) => !t.done);
    const mainline = remaining[0] ?? tasks[0] ?? null;
    const restTasks = mainline ? tasks.filter((t) => t.id !== mainline.id) : [];

    const target = stream.target;
    const gapText = target?.scoreGapAvailable ? `${target.scoreGap} 分` : "待评估";

    this.setData({
      viewState: "ready",
      demo,
      headline: stream.headline || this.localGreeting(),
      subline: `按你的目标和最近表现，排好了 ${tasks.length} 件事。`,
      liveText: "岸岸已按最近表现更新计划",
      targetName: target?.positionGroup || "未设目标",
      gapText,
      doneCount: stream.completedTaskIds?.length ?? 0,
      totalCount: tasks.length,
      mainline,
      restTasks,
      insight: stream.rationale ?? "",
      planBasis: stream.planBasis ?? "",
    });
  },

  localGreeting(): string {
    const hour = new Date().getHours();
    if (hour < 6) return "夜深了。";
    if (hour < 12) return "早上好。";
    if (hour < 18) return "下午好。";
    return "晚上好。";
  },

  onShowBasis() {
    wx.navigateTo({ url: "/pages/plan-basis/index" }).catch(() => {
      wx.showToast({ title: "页面暂未开放", icon: "none" });
    });
  },

  onOpenWeeklyPlan() {
    void wx.navigateTo({ url: "/pages/weekly-plan/index" });
  },

  onOpenPlanHistory() {
    void wx.navigateTo({ url: "/pages/plan-history/index" });
  },

  onOpenDayAdjustment() {
    void wx.navigateTo({ url: "/pages/day-adjustment/index" });
  },

  /** 拉取每日开场：displayState 为 already_seen/disabled 时不展示 */
  async loadDailyOpening() {
    if (this.data.openingStatus === "loading") return;
    this.setData({ openingStatus: "loading" });
    try {
      const view = await authedV2<DailyOpeningView>({
        method: "GET",
        path: "/api/v2/plans/daily-opening",
      });
      if (view.displayState === "already_seen" || view.displayState === "disabled") {
        this.setData({ openingStatus: "hidden", openingRaw: view, openingView: null });
        return;
      }
      this.setData({
        openingStatus: "ready",
        openingRaw: view,
        openingView: presentDailyOpening(view),
      });
      wx.nextTick(() => {
        void this.recordOpeningImpression();
      });
    } catch (err) {
      // 开场是辅助内容，失败不阻塞今日主流程
      console.warn("[anan] 每日开场加载失败：", err);
      this.setData({ openingStatus: "hidden" });
    }
  },

  /** 展示后上报 impressions；选址未完成时不上报（等选址后重载再报） */
  async recordOpeningImpression() {
    const view = this.data.openingRaw;
    if (!view || this.data.openingStatus !== "ready" || this.openingImpressionDone) return;
    if (view.displayState === "location_required") return;
    this.openingImpressionDone = true;
    const command: ImpressionCommand = {
      planVersionId: view.planVersionId,
      preferenceVersionId: view.preference.preferenceVersionId,
      locationVersionId: view.locationVersionId ?? null,
      weatherFingerprint: view.weatherFingerprint ?? null,
      coffeeState: view.coffeeState,
    };
    try {
      await authedV2<unknown>({
        method: "POST",
        path: "/api/v2/plans/daily-opening/impressions",
        body: command,
        idempotencyKey: idemKey(),
      });
    } catch (err) {
      this.openingImpressionDone = false;
      console.warn("[anan] 开场印象上报失败：", err);
    }
  },

  onChooseWeatherLocation() {
    if (this.data.openingWriting) return;
    wx.chooseLocation({
      success: (result) => {
        const command: WeatherLocationCommand = {
          label: (result.name || result.address || "已选地点").slice(0, 120),
          latitude: result.latitude,
          longitude: result.longitude,
          sourceType: "wechat_choose_location",
        };
        void this.updateWeatherLocation(command);
      },
      fail: () => {
        void wx.showToast({ title: "没有选择地点", icon: "none" });
      },
    });
  },

  async updateWeatherLocation(command: WeatherLocationCommand) {
    this.setData({ openingWriting: true });
    try {
      await authedV2<unknown>({
        method: "PUT",
        path: "/api/v2/plans/daily-opening/weather-location",
        body: command,
        idempotencyKey: idemKey(),
      });
      this.setData({ openingWriting: false });
      await this.loadDailyOpening();
    } catch (err) {
      console.warn("[anan] 天气地点写入失败：", err);
      this.setData({ openingWriting: false });
      void wx.showToast({ title: "地点没有保存上，请重试", icon: "none" });
    }
  },

  /** 今天不喝：记录决定并本地即时收口 */
  async onDeclineCoffee() {
    const view = this.data.openingRaw;
    if (!view || this.data.openingWriting) return;
    this.setData({ openingWriting: true });
    try {
      await authedV2<unknown>({
        method: "POST",
        path: "/api/v2/plans/daily-opening/coffee-decisions",
        body: { decision: "declined_today" },
        idempotencyKey: idemKey(),
      });
      const next: DailyOpeningView = {
        ...view,
        coffeeState: "today_declined",
        coffee: { state: "today_declined", showPrompt: false },
      };
      this.setData({
        openingWriting: false,
        openingRaw: next,
        openingView: presentDailyOpening(next),
      });
    } catch (err) {
      console.warn("[anan] 咖啡决定写入失败：", err);
      this.setData({ openingWriting: false });
      void wx.showToast({ title: "操作没有写入，请重试", icon: "none" });
    }
  },

  onDisableCoffee() {
    if (!this.data.openingRaw || this.data.openingWriting) return;
    wx.showModal({
      title: "以后不再询问咖啡？",
      content: "只关闭咖啡询问，天气和今日计划仍会显示。你可以在“我的”里重新开启。",
      confirmText: "确认关闭",
      success: (result) => {
        if (result.confirm) void this.disableCoffeePreference();
      },
    });
  },

  /** 关闭咖啡询问偏好（保留开场与天气开关） */
  async disableCoffeePreference() {
    const view = this.data.openingRaw;
    if (!view) return;
    this.setData({ openingWriting: true });
    try {
      await authedV2<unknown>({
        method: "PUT",
        path: "/api/v2/plans/daily-opening/preferences",
        body: {
          enabled: view.preference.enabled,
          weatherEnabled: view.preference.weatherEnabled,
          coffeePromptEnabled: false,
        },
        idempotencyKey: idemKey(),
      });
      const next: DailyOpeningView = {
        ...view,
        coffeeState: "disabled",
        coffee: { state: "disabled", showPrompt: false },
        preference: { ...view.preference, coffeePromptEnabled: false },
      };
      this.setData({
        openingWriting: false,
        openingRaw: next,
        openingView: presentDailyOpening(next),
      });
    } catch (err) {
      console.warn("[anan] 开场偏好写入失败：", err);
      this.setData({ openingWriting: false });
      void wx.showToast({ title: "操作没有写入，请重试", icon: "none" });
    }
  },

  onWeatherAttribution() {
    const url = this.data.openingView?.attributionUrl;
    if (!url) return;
    void wx.navigateTo({
      url: `/pages/weather-attribution/index?url=${encodeURIComponent(url)}`,
    });
  },

  /** 开场卡计划行：指向今日页则滚动到主线卡，否则按 startUrl 跳转 */
  onStartOpening() {
    const startUrl = this.data.openingRaw?.plan.startUrl;
    if (!startUrl) return;
    if (startUrl.split("?")[0] === "/pages/today/index") {
      void wx.pageScrollTo({ selector: ".mainline-wrap", duration: 260 });
      return;
    }
    wx.navigateTo({ url: startUrl }).catch(() => {
      wx.showToast({ title: "任务入口即将上线", icon: "none" });
    });
  },

  onStartMainline() {
    this.openTask(this.data.mainline);
  },

  onOpenTask(e: WechatMiniprogram.TouchEvent) {
    const id = e.currentTarget.dataset.id as string;
    const task = [this.data.mainline, ...this.data.restTasks].find((t) => t?.id === id);
    this.openTask(task ?? null);
  },

  openTask(task: TaskView | null) {
    if (!task) return;
    if (task.startUrl) {
      wx.navigateTo({ url: task.startUrl }).catch(() => {
        wx.showToast({ title: "任务入口即将上线", icon: "none" });
      });
      return;
    }
    if (this.data.demo) {
      wx.showToast({ title: "演示数据，暂不可进入", icon: "none" });
      return;
    }
    wx.navigateTo({
      url: `/pages/practice/index?planTaskId=${encodeURIComponent(task.id)}`,
    }).catch(() => {
      wx.showToast({ title: "任务入口即将上线", icon: "none" });
    });
  },
});
