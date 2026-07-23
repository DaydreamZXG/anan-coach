Component({
  options: {
    styleIsolation: "isolated",
  },

  properties: {
    /** loading / empty / error / partial */
    type: {
      type: String,
      value: "empty",
    },
    title: {
      type: String,
      value: "",
    },
    desc: {
      type: String,
      value: "",
    },
    requestId: {
      type: String,
      value: "",
    },
    actionText: {
      type: String,
      value: "",
    },
  },

  methods: {
    onAction() {
      this.triggerEvent("action");
    },
  },
});
