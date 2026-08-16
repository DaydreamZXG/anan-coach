import { arrayPropertyItems } from "../component-property";

Component({
  properties: {
    visible: { type: Boolean, value: false },
    current: { type: Number, value: 1 },
    items: { type: null, value: [] },
    answered: { type: Number, value: 0 },
    unanswered: { type: Number, value: 0 },
    marked: { type: Number, value: 0 },
    subtitle: { type: String, value: "" },
    moduleName: { type: String, value: "" },
    moduleCounts: { type: String, value: "" },
    insightAvailable: { type: Boolean, value: false },
    hint: { type: String, value: "点题号即可直接切换" },
    submitLabel: { type: String, value: "确认交卷" },
  },
  data: {
    renderItems: [] as unknown[],
  },
  observers: {
    items(value: unknown) {
      this.setData({ renderItems: arrayPropertyItems(value) });
    },
  },
  lifetimes: {
    attached() {
      this.setData({ renderItems: arrayPropertyItems(this.data.items) });
    },
  },
  methods: {
    close() {
      this.triggerEvent("close");
    },
    selectQuestion(event: WechatMiniprogram.BaseEvent) {
      this.triggerEvent("select", { index: Number(event.currentTarget.dataset.index) });
    },
    submit() {
      this.triggerEvent("submit");
    },
    noop() {},
  },
});
