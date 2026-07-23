import { fetchDailySettlement } from "../../api/endpoints";
import type { DailySettlementView } from "../../api/generated/types.gen";

const TRACK_LABEL: Record<string, string> = {
  xingce: "行测",
  essay: "申论",
  recovery: "恢复",
  mock: "模考",
};

const STATUS_LABEL: Record<string, string> = {
  complete: "已完成",
  in_progress: "进行中",
  ready: "没动",
  abandoned: "已顺延",
};

interface TaskVM {
  taskId: string;
  title: string;
  trackLabel: string;
  statusLabel: string;
  done: boolean;
  minutes: number;
}

function pickAnanWord(completionPct: number, carryOverCount: number): string {
  if (completionPct >= 100) {
    return "今天这件核心的事你啃下来了，节奏很稳。我把明天的第一件事也排好了，早点休息。";
  }
  if (completionPct >= 60) {
    const carry =
      carryOverCount > 0 ? `剩下的 ${carryOverCount} 件我帮你挪到明天，不用有负担。` : "";
    return `做完了大部分，${carry}能坚持坐下来练，就已经在往前走了。`;
  }
  return "今天状态可能一般，没关系。我把没做完的降载处理了，明天我们从轻一点的开始。";
}

Page({
  data: {
    loading: true,
    fromServer: false,
    answered: 0,
    total: 0,
    minutes: 0,
    completionPct: 0,
    carryOverCount: 0,
    summary: "",
    risks: [] as string[],
    tasks: [] as TaskVM[],
    ananWord: "",
  },

  onLoad(query: Record<string, string>) {
    // demo 兜底：练习页带过来的参数先展示，接口成功后覆盖
    const answered = Number(query.answered || 0);
    const total = Number(query.total || 0);
    const seconds = Number(query.seconds || 0);
    const minutes = Math.max(1, Math.round(seconds / 60));
    const completionPct = total > 0 ? Math.round((answered / total) * 100) : 0;

    this.setData({
      answered,
      total,
      minutes,
      completionPct,
      ananWord: pickAnanWord(completionPct, 0),
    });

    void this.loadSettlement();
  },

  async loadSettlement() {
    try {
      const view = await fetchDailySettlement();
      this.applyView(view);
    } catch (err) {
      // 后端不可达时保留 demo 数据，符合"页面必须能预览"铁律
      console.warn("settlement fallback to demo:", err);
      this.setData({ loading: false });
    }
  },

  applyView(view: DailySettlementView) {
    const totalTasks = view.tasks.length;
    const done = view.completedTaskCount;
    const completionPct = totalTasks > 0 ? Math.round((done / totalTasks) * 100) : 0;
    const carryOverCount = view.carryOverTaskCount ?? 0;

    const tasks: TaskVM[] = view.tasks.map((t) => ({
      taskId: t.taskId,
      title: t.title,
      trackLabel: TRACK_LABEL[t.track] ?? t.track,
      statusLabel: STATUS_LABEL[t.status] ?? t.status,
      done: t.status === "complete",
      minutes: t.elapsedMinutes,
    }));

    this.setData({
      loading: false,
      fromServer: true,
      answered: done,
      total: totalTasks,
      minutes: view.elapsedMinutes,
      completionPct,
      carryOverCount,
      summary: view.summary,
      risks: view.newRisks,
      tasks,
      ananWord: view.summary || pickAnanWord(completionPct, carryOverCount),
    });
  },

  onBackToday() {
    wx.switchTab({ url: "/pages/today/index" });
  },

  onAskAnan() {
    wx.switchTab({ url: "/pages/anan/index" });
  },
});
