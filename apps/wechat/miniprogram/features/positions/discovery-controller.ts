import type { QualificationApi } from "../../api/qualification";
import type { PositionDiscoveryItem } from "../../api/generated/types.gen";
import {
  deliveryView,
  positionRouteSummary,
  type PositionDeliveryView,
  type PositionOperationError,
  type PositionRemoteStatus,
} from "./contracts";
import { positionOperationError } from "./error-state";
import { positionCardView, type PositionCardView } from "./presentation";
import type { PositionStateStore, PositionSearchDraft } from "./state-store";

export interface PositionSearchFilters {
  query: string;
  examYear: string;
  region: string;
  city: string;
}

export interface SelectablePositionCardView extends PositionCardView {
  comparisonSelected: boolean;
}

export type PositionPaginationStatus = "idle" | "loading" | "error" | "done";

export interface PositionDiscoveryState {
  status: PositionRemoteStatus;
  error: PositionOperationError | null;
  filters: PositionSearchFilters;
  appliedFilters: PositionSearchFilters;
  items: SelectablePositionCardView[];
  nextCursor: string;
  hasMore: boolean;
  paginationStatus: PositionPaginationStatus;
  paginationError: PositionOperationError | null;
  refreshError: PositionOperationError | null;
  validationError: string;
  learnerProfileVersion: number;
  ruleSetVersion: string;
  positionDataVersion: string;
  delivery: PositionDeliveryView | null;
  selectedIds: string[];
}

export interface PositionSelectionResult {
  accepted: boolean;
  reason: "selected" | "removed" | "limit" | "missing";
}

export interface PositionDiscoveryControllerOptions {
  api: Pick<QualificationApi, "discoverPositions">;
  store: PositionStateStore;
  initialFilters?: Partial<PositionSearchFilters>;
  onChange: (state: PositionDiscoveryState) => void;
  pageSize?: number;
  restoreSearchDraft?: boolean;
  preserveItemsOnRefreshError?: boolean;
}

const emptyFilters: PositionSearchFilters = {
  query: "",
  examYear: "",
  region: "",
  city: "",
};

export class PositionDiscoveryController {
  private readonly api: Pick<QualificationApi, "discoverPositions">;
  private readonly store: PositionStateStore;
  private readonly onChange: (state: PositionDiscoveryState) => void;
  private readonly pageSize: number;
  private readonly preserveItemsOnRefreshError: boolean;
  private requestEpoch = 0;
  private disposed = false;
  private state: PositionDiscoveryState;

  constructor(options: PositionDiscoveryControllerOptions) {
    this.api = options.api;
    this.store = options.store;
    this.onChange = options.onChange;
    this.pageSize = options.pageSize ?? 20;
    this.preserveItemsOnRefreshError = options.preserveItemsOnRefreshError ?? false;
    const restored = options.restoreSearchDraft === false ? null : this.store.loadSearchDraft();
    const filters = normalizeFilters({
      ...emptyFilters,
      ...(restored ?? {}),
      ...(options.initialFilters ?? {}),
    });
    this.state = {
      status: "idle",
      error: null,
      filters,
      appliedFilters: filters,
      items: [],
      nextCursor: "",
      hasMore: false,
      paginationStatus: "idle",
      paginationError: null,
      refreshError: null,
      validationError: "",
      learnerProfileVersion: 0,
      ruleSetVersion: "",
      positionDataVersion: "",
      delivery: null,
      selectedIds: this.store.loadComparison(),
    };
    this.publish();
  }

  snapshot(): PositionDiscoveryState {
    return cloneState(this.state);
  }

  setFilters(patch: Partial<PositionSearchFilters>): void {
    this.state = {
      ...this.state,
      filters: normalizeFilters({ ...this.state.filters, ...patch }),
      validationError: "",
    };
    this.publish();
  }

  async search(): Promise<void> {
    const appliedFilters = normalizeFilters(this.state.filters);
    const validationError = validateFilters(appliedFilters);
    if (validationError) {
      this.state = { ...this.state, validationError };
      this.publish();
      return;
    }
    this.store.saveSearchDraft(appliedFilters satisfies PositionSearchDraft);
    this.state = { ...this.state, appliedFilters };
    await this.load(true);
  }

  async refresh(): Promise<void> {
    await this.load(true);
  }

  async loadMore(): Promise<void> {
    if (this.disposed || !this.state.hasMore || this.state.paginationStatus === "loading") {
      return;
    }
    await this.load(false);
  }

  toggleComparison(positionId: string): PositionSelectionResult {
    if (!this.state.items.some((item) => item.positionId === positionId)) {
      return { accepted: false, reason: "missing" };
    }
    const selected = this.state.selectedIds;
    const exists = selected.includes(positionId);
    if (!exists && selected.length >= 3) {
      return { accepted: false, reason: "limit" };
    }
    const selectedIds = exists
      ? selected.filter((item) => item !== positionId)
      : [...selected, positionId];
    this.store.saveComparison(selectedIds);
    this.state = {
      ...this.state,
      selectedIds,
      items: this.state.items.map((item) => selectablePosition(item, selectedIds)),
    };
    this.publish();
    return { accepted: true, reason: exists ? "removed" : "selected" };
  }

  clearComparison(): void {
    this.store.saveComparison([]);
    this.state = {
      ...this.state,
      selectedIds: [],
      items: this.state.items.map((item) => selectablePosition(item, [])),
    };
    this.publish();
  }

  summary(positionId: string) {
    const item = this.state.items.find((candidate) => candidate.positionId === positionId);
    return item ? positionRouteSummary(item) : null;
  }

  dispose(): void {
    this.disposed = true;
    this.requestEpoch += 1;
  }

  private async load(reset: boolean): Promise<void> {
    if (this.disposed) return;
    const epoch = ++this.requestEpoch;
    const retainedItems = reset ? this.state.items : [];
    if (reset) {
      this.state = {
        ...this.state,
        status: "loading",
        error: null,
        items: this.preserveItemsOnRefreshError ? retainedItems : [],
        nextCursor: "",
        hasMore: false,
        paginationStatus: "idle",
        paginationError: null,
        refreshError: null,
        validationError: "",
      };
    } else {
      this.state = {
        ...this.state,
        paginationStatus: "loading",
        paginationError: null,
        refreshError: null,
      };
    }
    this.publish();
    try {
      const filters = requestFilters(
        this.state.appliedFilters,
        this.pageSize,
        reset ? undefined : this.state.nextCursor,
      );
      const response = await this.api.discoverPositions(filters);
      if (!this.isCurrent(epoch)) return;
      const incoming = response.data.items.map((item) => {
        const summary = positionRouteSummary(item);
        this.store.saveSummary(summary);
        return selectablePosition(positionCardView(item), this.state.selectedIds);
      });
      const items = mergeItems(reset ? [] : this.state.items, incoming);
      const delivery = deliveryView(response);
      this.state = {
        ...this.state,
        status: items.length > 0 ? "ready" : "empty",
        error: null,
        items,
        nextCursor: response.data.nextCursor ?? "",
        hasMore: response.data.hasMore,
        paginationStatus: response.data.hasMore ? "idle" : "done",
        paginationError: null,
        learnerProfileVersion: response.data.learnerProfileVersion,
        ruleSetVersion: response.data.ruleSetVersion,
        positionDataVersion: response.data.positionDataVersion,
        delivery,
      };
      this.publish();
    } catch (error) {
      if (!this.isCurrent(epoch)) return;
      const operationError = positionOperationError(error);
      if (reset && this.preserveItemsOnRefreshError && retainedItems.length > 0) {
        this.state = {
          ...this.state,
          status: "ready",
          error: null,
          items: retainedItems,
          refreshError: operationError,
          delivery: this.state.delivery
            ? {
                ...this.state.delivery,
                stale: true,
                staleLabel: "刷新失败，当前保留上次已加载的版本",
              }
            : null,
        };
      } else if (!reset && this.state.items.length > 0) {
        this.state = {
          ...this.state,
          status: "ready",
          paginationStatus: "error",
          paginationError: operationError,
          delivery: this.state.delivery
            ? {
                ...this.state.delivery,
                partial: true,
                partialLabel: "后续分页加载失败，当前仅展示已加载结果",
              }
            : null,
        };
      } else {
        this.state = {
          ...this.state,
          status: operationError.status,
          error: operationError,
          paginationStatus: "idle",
        };
      }
      this.publish();
    }
  }

  private isCurrent(epoch: number): boolean {
    return !this.disposed && epoch === this.requestEpoch;
  }

  private publish(): void {
    if (!this.disposed) this.onChange(this.snapshot());
  }
}

function selectablePosition(
  item: PositionCardView,
  selectedIds: string[],
): SelectablePositionCardView {
  return { ...item, comparisonSelected: selectedIds.includes(item.positionId) };
}

type PositionDiscoveryRequestFilters = {
  examYear?: number;
  region?: string;
  city?: string;
  query?: string;
  limit: number;
  cursor?: string;
};

function requestFilters(
  filters: PositionSearchFilters,
  limit: number,
  cursor?: string,
): PositionDiscoveryRequestFilters {
  const request: PositionDiscoveryRequestFilters = { limit };
  if (filters.query) request.query = filters.query;
  if (filters.region) request.region = filters.region;
  if (filters.city) request.city = filters.city;
  const examYear = Number(filters.examYear);
  if (Number.isInteger(examYear) && examYear >= 2000 && examYear <= 2100) {
    request.examYear = examYear;
  }
  if (cursor) request.cursor = cursor;
  return request;
}

function normalizeFilters(filters: PositionSearchFilters): PositionSearchFilters {
  return {
    query: filters.query.trim(),
    examYear: filters.examYear.trim(),
    region: filters.region.trim(),
    city: filters.city.trim(),
  };
}

function validateFilters(filters: PositionSearchFilters): string {
  if (filters.query.length > 80) return "搜索关键词最多 80 个字符。";
  if (filters.region.length > 40) return "省份筛选最多 40 个字符。";
  if (filters.city.length > 60) return "城市筛选最多 60 个字符。";
  if (filters.examYear) {
    const examYear = Number(filters.examYear);
    if (!Number.isInteger(examYear) || examYear < 2000 || examYear > 2100) {
      return "考试年度必须是 2000–2100 之间的四位年份。";
    }
  }
  return "";
}

function mergeItems(
  current: SelectablePositionCardView[],
  incoming: SelectablePositionCardView[],
): SelectablePositionCardView[] {
  const known = new Set(current.map((item) => item.positionId));
  const merged = [...current];
  incoming.forEach((item) => {
    if (known.has(item.positionId)) return;
    known.add(item.positionId);
    merged.push(item);
  });
  return merged;
}

function cloneState(state: PositionDiscoveryState): PositionDiscoveryState {
  return {
    ...state,
    error: state.error ? { ...state.error } : null,
    filters: { ...state.filters },
    appliedFilters: { ...state.appliedFilters },
    items: state.items.map((item) => ({ ...item })),
    paginationError: state.paginationError ? { ...state.paginationError } : null,
    refreshError: state.refreshError ? { ...state.refreshError } : null,
    delivery: state.delivery ? { ...state.delivery } : null,
    selectedIds: [...state.selectedIds],
  };
}

export function positionItemFixture(
  item: PositionDiscoveryItem,
  selectedIds: string[] = [],
): SelectablePositionCardView {
  return selectablePosition(positionCardView(item), selectedIds);
}
