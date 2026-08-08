import type { TaskHelpIssueCode, TaskHelpPageObjectReference } from "../../api/task-help";
import { createTaskHelpCommand, TASK_HELP_OPTIONS, type TaskHelpContext } from "./contracts";

Component({
  properties: {
    pageObjectReference: { type: Object, value: null },
    syncState: { type: String, value: "unknown" },
    requestId: { type: String, value: "" },
    errorCode: { type: String, value: "" },
  },
  data: {
    options: TASK_HELP_OPTIONS,
    selectedIssueCode: "",
  },
  methods: {
    chooseIssue(event: WechatMiniprogram.BaseEvent) {
      const issueCode = String(event.currentTarget.dataset.issue ?? "") as TaskHelpIssueCode;
      if (!TASK_HELP_OPTIONS.some((option) => option.code === issueCode)) return;
      this.setData({ selectedIssueCode: issueCode });
    },
    submit() {
      if (!this.data.selectedIssueCode) return;
      const selectedIssueCode = this.data.selectedIssueCode as TaskHelpIssueCode;
      const reference = this.properties.pageObjectReference as TaskHelpPageObjectReference | null;
      if (!reference) return;
      const context: TaskHelpContext = {
        pageObjectReference: reference,
        syncState: this.properties.syncState as TaskHelpContext["syncState"],
        ...(this.properties.requestId ? { requestId: this.properties.requestId } : {}),
        ...(this.properties.errorCode ? { errorCode: this.properties.errorCode } : {}),
      };
      this.triggerEvent(
        "submit",
        createTaskHelpCommand(context, selectedIssueCode, new Date().toISOString()),
      );
    },
    close() {
      this.triggerEvent("close");
    },
  },
});
