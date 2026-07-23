Page({
  data: {
    answered: 0,
    total: 0,
    minutes: 0,
    completionPct: 0,
    ananWord: "",
  },

  onLoad(query: Record<string, string>) {
    const answered = Number(query.answered || 0);
    const total = Number(query.total || 0);
    const seconds = Number(query.seconds || 0);
    const minutes = Math.max(1, Math.round(seconds / 60));
    const completionPct = total > 0 ? Math.round((answered / total) * 100) : 0;

    let ananWord = "";
    if (completionPct >= 100) {
      ananWord =
        "今天这件核心的事你啃下来了，节奏很稳。我把明天的第一件事也排好了，早点休息。";
    } else if (completionPct >= 60) {
      ananWord =
        "做完了大部分，剩下的我帮你挪到明天，不用有负担。能坚持坐下来练，就已经在往前走了。";
    } else {
      ananWord =
        "今天状态可能一般，没关系。我把没做完的降载处理了，明天我们从轻一点的开始。";
    }

    this.setData({ answered, total, minutes, completionPct, ananWord });
  },

  onBackToday() {
    wx.switchTab({ url: "/pages/today/index" });
  },

  onAskAnan() {
    wx.switchTab({ url: "/pages/anan/index" });
  },
});
