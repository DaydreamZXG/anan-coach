import { ApiClient, ApiClientError } from "../../api/client";
import { CompanionApi } from "../../api/companion";
import type {
  CompanionMemoryItem,
  CompanionMemoryState,
  CompanionMemoryWriteRequest,
} from "../../api/generated/types.gen";
import { currentAuthSession, sameSessionOwner } from "../../auth/session";
import { createIdempotencyKey } from "../../platform/command-id";

const categories = [
  { value: "explanation_preference", label: "讲解偏好" },
  { value: "study_constraint", label: "学习约束" },
  { value: "personal_note", label: "个人备忘" },
] as const;
const defaultCategoryLabel: string = categories[0].label;

interface MemoryItemView extends CompanionMemoryItem {
  categoryLabel: string;
  updatedLabel: string;
}

interface ValueEvent {
  detail: { value: string | number | boolean };
}

interface ItemEvent {
  currentTarget: { dataset: { id?: string } };
}

let loadGeneration = 0;
let pendingCreate: { fingerprint: string; key: string } | undefined;

Page({
  data: {
    status: "loading",
    errorMessage: "",
    requestId: "",
    busy: false,
    longTermEnabled: true,
    items: [] as MemoryItemView[],
    shortTermTurnCount: 0,
    shortTermExpiresLabel: "暂无会话记忆",
    maximumLongTermItems: 20,
    categories,
    categoryIndex: 0,
    currentCategoryLabel: defaultCategoryLabel,
    draftContent: "",
    editingId: "",
    editingRevision: 0,
  },

  onShow() {
    if (!currentAuthSession()) {
      void wx.reLaunch({ url: "/pages/login/index" });
      return;
    }
    void this.loadMemories();
  },

  onUnload() {
    loadGeneration += 1;
  },

  retry() {
    void this.loadMemories();
  },

  async loadMemories(): Promise<void> {
    const session = currentAuthSession();
    if (!session) return;
    const generation = loadGeneration + 1;
    loadGeneration = generation;
    this.setData({ status: "loading", errorMessage: "", requestId: "" });
    try {
      const response = await new CompanionApi(
        new ApiClient(undefined, session.accessToken),
      ).getMemories();
      if (generation !== loadGeneration || !sameSessionOwner(currentAuthSession(), session)) return;
      this.applyState(response.data);
    } catch (error) {
      if (generation !== loadGeneration || !sameSessionOwner(currentAuthSession(), session)) return;
      this.showLoadError(error);
    }
  },

  applyState(state: CompanionMemoryState) {
    this.setData({
      status: "ready",
      errorMessage: "",
      requestId: "",
      longTermEnabled: state.longTermEnabled,
      items: state.items.map(memoryView),
      shortTermTurnCount: state.shortTerm.turnCount,
      shortTermExpiresLabel: shortTermLabel(state.shortTerm.turnCount, state.shortTerm.expiresAt),
      maximumLongTermItems: state.maximumLongTermItems ?? 20,
    });
  },

  showLoadError(error: unknown) {
    const apiError = error instanceof ApiClientError ? error : undefined;
    this.setData({
      status: "error",
      errorMessage: apiError?.message ?? "记忆清单暂时没有加载出来。",
      requestId: apiError?.requestId ?? "",
    });
  },

  onCategoryChange(event: ValueEvent) {
    const index = Number(event.detail.value);
    const selected = categories[index];
    if (!Number.isInteger(index) || !selected) return;
    pendingCreate = undefined;
    this.setData({ categoryIndex: index, currentCategoryLabel: selected.label });
  },

  onContentInput(event: ValueEvent) {
    pendingCreate = undefined;
    this.setData({ draftContent: String(event.detail.value) });
  },

  async saveMemory(): Promise<void> {
    if (this.data.busy) return;
    const content = this.data.draftContent.trim();
    const category = categories[this.data.categoryIndex]?.value;
    if (!content || !category) {
      void wx.showToast({ title: "先写下想让岸岸记住的内容", icon: "none" });
      return;
    }
    const session = currentAuthSession();
    if (!session) return;
    this.setData({ busy: true, errorMessage: "", requestId: "" });
    const command: CompanionMemoryWriteRequest = { category, content };
    try {
      const api = new CompanionApi(new ApiClient(undefined, session.accessToken));
      if (this.data.editingId) {
        await api.updateMemory(this.data.editingId, {
          ...command,
          revision: this.data.editingRevision,
        });
      } else {
        const fingerprint = `${category}:${content}`;
        const key =
          pendingCreate?.fingerprint === fingerprint
            ? pendingCreate.key
            : await createIdempotencyKey("companion-memory");
        pendingCreate = { fingerprint, key };
        await api.createMemory(command, key);
        pendingCreate = undefined;
      }
      if (!sameSessionOwner(currentAuthSession(), session)) return;
      this.setData({
        busy: false,
        draftContent: "",
        categoryIndex: 0,
        currentCategoryLabel: categories[0].label,
        editingId: "",
        editingRevision: 0,
      });
      await this.loadMemories();
    } catch (error) {
      if (!sameSessionOwner(currentAuthSession(), session)) return;
      this.setData({ busy: false });
      await this.handleMutationError(error);
    }
  },

  editMemory(event: ItemEvent) {
    const item = this.data.items.find(
      (candidate) => candidate.memoryId === event.currentTarget.dataset.id,
    );
    if (!item || this.data.busy) return;
    const categoryIndex = categories.findIndex((candidate) => candidate.value === item.category);
    const normalizedCategoryIndex = Math.max(categoryIndex, 0);
    const selectedCategory = categories[normalizedCategoryIndex] ?? categories[0];
    pendingCreate = undefined;
    this.setData({
      editingId: item.memoryId,
      editingRevision: item.revision,
      categoryIndex: normalizedCategoryIndex,
      currentCategoryLabel: selectedCategory.label,
      draftContent: item.content,
    });
  },

  cancelEdit() {
    if (this.data.busy) return;
    this.setData({
      editingId: "",
      editingRevision: 0,
      categoryIndex: 0,
      currentCategoryLabel: categories[0].label,
      draftContent: "",
    });
  },

  async deleteMemory(event: ItemEvent): Promise<void> {
    const item = this.data.items.find(
      (candidate) => candidate.memoryId === event.currentTarget.dataset.id,
    );
    if (!item || this.data.busy) return;
    const confirmed = await confirmAction("删除这条记忆？", "删除后，岸岸不会再使用它。", "删除");
    if (!confirmed) return;
    await this.runMutation((api) => api.deleteMemory(item.memoryId));
  },

  async toggleLongTerm(event: ValueEvent): Promise<void> {
    if (this.data.busy) return;
    const enabled = Boolean(event.detail.value);
    await this.runMutation((api) => api.setMemoryEnabled({ longTermEnabled: enabled }));
  },

  async clearShortTerm(): Promise<void> {
    if (this.data.busy || this.data.shortTermTurnCount === 0) return;
    const confirmed = await confirmAction(
      "清除近期会话？",
      "会同时清除小程序和微信发题渠道的 2 小时会话记忆。",
      "清除",
    );
    if (!confirmed) return;
    await this.runMutation((api) => api.clearConversationMemory());
  },

  async clearLongTerm(): Promise<void> {
    if (this.data.busy || this.data.items.length === 0) return;
    const confirmed = await confirmAction(
      "删除全部长期记忆？",
      "这些由你主动添加的偏好和备忘会被全部删除，无法恢复。",
      "全部删除",
    );
    if (!confirmed) return;
    await this.runMutation((api) => api.clearMemories());
  },

  async runMutation(operation: (api: CompanionApi) => Promise<unknown>): Promise<void> {
    const session = currentAuthSession();
    if (!session) return;
    this.setData({ busy: true, errorMessage: "", requestId: "" });
    try {
      await operation(new CompanionApi(new ApiClient(undefined, session.accessToken)));
      if (!sameSessionOwner(currentAuthSession(), session)) return;
      this.setData({ busy: false });
      await this.loadMemories();
    } catch (error) {
      if (!sameSessionOwner(currentAuthSession(), session)) return;
      this.setData({ busy: false });
      await this.handleMutationError(error);
    }
  },

  async handleMutationError(error: unknown): Promise<void> {
    const apiError = error instanceof ApiClientError ? error : undefined;
    this.setData({
      errorMessage: apiError?.message ?? "这次操作没有完成，请稍后再试。",
      requestId: apiError?.requestId ?? "",
    });
    if (apiError?.statusCode === 409 || apiError?.classification === "transport_unknown") {
      await this.loadMemories();
    }
  },
});

function memoryView(item: CompanionMemoryItem): MemoryItemView {
  return {
    ...item,
    categoryLabel:
      categories.find((candidate) => candidate.value === item.category)?.label ?? "个人备忘",
    updatedLabel: formatTime(item.updatedAt),
  };
}

function formatTime(value: string): string {
  const timestamp = Date.parse(value);
  if (!Number.isFinite(timestamp)) return "时间未知";
  const date = new Date(timestamp);
  return `${String(date.getMonth() + 1)} 月 ${String(date.getDate())} 日更新`;
}

function shortTermLabel(turnCount: number, expiresAt?: string | null): string {
  if (turnCount <= 0) return "暂无会话记忆";
  if (!expiresAt) return `共 ${String(turnCount)} 轮，会在 2 小时内自动过期`;
  const timestamp = Date.parse(expiresAt);
  if (!Number.isFinite(timestamp)) return `共 ${String(turnCount)} 轮，会在 2 小时内自动过期`;
  const date = new Date(timestamp);
  return `共 ${String(turnCount)} 轮，最晚 ${pad(date.getHours())}:${pad(date.getMinutes())} 自动过期`;
}

function pad(value: number): string {
  return String(value).padStart(2, "0");
}

function confirmAction(title: string, content: string, confirmText: string): Promise<boolean> {
  return new Promise((resolve) => {
    wx.showModal({
      title,
      content,
      confirmText,
      confirmColor: "#D5464F",
      success: (result) => {
        resolve(result.confirm);
      },
      fail: () => {
        resolve(false);
      },
    });
  });
}
