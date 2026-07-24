type ButtonVariant = "primary" | "secondary" | "wechat" | "danger" | "text";
const defaultVariant: ButtonVariant = "primary";

Component({
  options: {
    virtualHost: true,
  },
  externalClasses: ["custom-class"],
  properties: {
    label: { type: String, value: "" },
    variant: { type: String, value: defaultVariant },
    block: { type: Boolean, value: true },
    loading: { type: Boolean, value: false },
    disabled: { type: Boolean, value: false },
    formType: { type: String, value: "" },
  },
  methods: {
    handleTap() {
      if (this.data.loading || this.data.disabled) return;
      this.triggerEvent("action");
    },
  },
});
