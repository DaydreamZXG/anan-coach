import { safeTopHolderHeight } from "./metrics";

/**
 * 自定义导航页的顶部安全区。
 * menu：正文从系统胶囊下方开始。
 * status：仅供没有顶部控件的沉浸页使用；任何标题、返回或关闭控件都必须使用 menu。
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
