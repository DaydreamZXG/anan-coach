Component({
  options: {
    multipleSlots: false,
    styleIsolation: "isolated",
  },

  properties: {
    /** primary=渐变主行动（一页一个） ghost=墨线次行动 */
    variant: {
      type: String,
      value: "primary",
    },
    size: {
      type: String,
      value: "md",
    },
    block: {
      type: Boolean,
      value: false,
    },
    loading: {
      type: Boolean,
      value: false,
    },
    disabled: {
      type: Boolean,
      value: false,
    },
  },

  methods: {
    onTap() {
      if (this.data.disabled || this.data.loading) return;
      this.triggerEvent("tap");
    },
  },
});
