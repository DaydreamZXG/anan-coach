import { safeTopHolderHeight } from "./metrics";

/**
 * 自定义导航页的顶部安全区。
 * menu：正文从系统胶囊下方开始。
 * status：标题可以与胶囊同排，仅避开系统状态栏。
 */
Component({
  properties: {
    gap: { type: Number, value: 8 },
    mode: { type: String, value: "menu" },
  },

  data: {
    holderHeight: 0,
  },

  lifetimes: {
    attached() {
      const mode = this.data.mode === "status" ? "status" : "menu";
      this.setData({ holderHeight: safeTopHolderHeight(mode, this.data.gap) });
    },
  },
});
