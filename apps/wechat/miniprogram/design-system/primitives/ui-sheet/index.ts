Component({
  options: {
    multipleSlots: true,
  },
  externalClasses: ["sheet-class"],
  properties: {
    visible: { type: Boolean, value: false },
    title: { type: String, value: "" },
    copy: { type: String, value: "" },
    // sheet：答题卡/草稿等任务内弹层；overlay：工具/帮助/争议等上层弹层
    level: { type: String, value: "sheet" },
    maxHeight: { type: String, value: "" },
    surface: { type: String, value: "surface" },
    maskClosable: { type: Boolean, value: true },
    showHandle: { type: Boolean, value: true },
    showClose: { type: Boolean, value: false },
    closeText: { type: String, value: "关闭" },
  },
  methods: {
    onMaskTap() {
      if (this.data.maskClosable) {
        this.triggerEvent("close");
      }
    },
    onCloseTap() {
      this.triggerEvent("close");
    },
    noop() {},
  },
});
