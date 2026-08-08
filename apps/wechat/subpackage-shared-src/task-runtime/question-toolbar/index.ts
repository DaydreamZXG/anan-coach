Component({
  properties: {
    moduleName: { type: String, value: "" },
    current: { type: Number, value: 1 },
    interactionLocked: { type: Boolean, value: false },
    favoriteAvailable: { type: Boolean, value: false },
    favoriteStatus: { type: String, value: "idle" },
    favorited: { type: Boolean, value: false },
    favoriteButtonLabel: { type: String, value: "收藏" },
    markAvailable: { type: Boolean, value: true },
    marked: { type: Boolean, value: false },
    draftAvailable: { type: Boolean, value: true },
    settingsAvailable: { type: Boolean, value: false },
  },
  methods: {
    favorite() {
      this.triggerEvent("favorite");
    },
    mark() {
      this.triggerEvent("mark");
    },
    draft() {
      this.triggerEvent("draft");
    },
    settings() {
      this.triggerEvent("settings");
    },
  },
});
