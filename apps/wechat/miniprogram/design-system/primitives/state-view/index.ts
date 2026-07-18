type ViewState = "loading" | "empty" | "error" | "processing" | "stale";
const defaultState: ViewState = "empty";

Component({
  options: {
    virtualHost: true,
  },
  externalClasses: ["custom-class"],
  properties: {
    state: { type: String, value: defaultState },
    title: { type: String, value: "" },
    description: { type: String, value: "" },
    actionText: { type: String, value: "" },
    requestId: { type: String, value: "" },
  },
  methods: {
    handleAction() {
      this.triggerEvent("action");
    },
  },
});
