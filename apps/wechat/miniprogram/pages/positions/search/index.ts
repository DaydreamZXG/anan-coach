import { ApiClient } from "../../../api/client";
import { QualificationApi } from "../../../api/qualification";
import { loginUrlForReturn } from "../../../app/navigation/auth-return";
import {
  positionCompareUrl,
  positionDetailUrl,
  positionSearchUrl,
  routes,
} from "../../../app/navigation/routes";
import { currentAuthSession } from "../../../auth/session";
import {
  PositionDiscoveryController,
  type PositionDiscoveryState,
  type PositionSearchFilters,
} from "../features/discovery-controller";
import { PositionPageRuntimeRegistry, type PositionController } from "../features/runtime";
import { PositionStateStore } from "../../../features/positions/state-store";

interface PositionSearchPageData {
  state: PositionDiscoveryState | null;
}

interface PositionSearchPageCustom {
  goBack(): void;
  inputQuery(event: WechatMiniprogram.Input): void;
  inputExamYear(event: WechatMiniprogram.Input): void;
  inputRegion(event: WechatMiniprogram.Input): void;
  inputCity(event: WechatMiniprogram.Input): void;
  search(): void;
  clearFilters(): void;
  retry(): void;
  loadMore(): void;
  openQualificationProfile(): void;
  openPreparation(): void;
  openPosition(event: WechatMiniprogram.BaseEvent): void;
  toggleComparison(event: WechatMiniprogram.BaseEvent): void;
  clearComparison(): void;
  openComparison(): void;
}

type PositionSearchPage = WechatMiniprogram.Page.Instance<
  PositionSearchPageData,
  PositionSearchPageCustom
>;

interface SearchPageController extends PositionController {
  discovery: PositionDiscoveryController;
  hasShown: boolean;
}

const runtimes = new PositionPageRuntimeRegistry<PositionSearchPage, SearchPageController>();

Page<PositionSearchPageData, PositionSearchPageCustom>({
  data: { state: null },

  onLoad(options: Record<string, string | undefined>) {
    const session = currentAuthSession();
    if (!session) {
      void wx.reLaunch({
        url: loginUrlForReturn(
          positionSearchUrl({
            ...(options.q !== undefined ? { query: options.q } : {}),
            ...(options.examYear !== undefined ? { examYear: options.examYear } : {}),
            ...(options.region !== undefined ? { region: options.region } : {}),
            ...(options.city !== undefined ? { city: options.city } : {}),
          }),
        ),
      });
      return;
    }
    const runtime = runtimes.attach(this);
    const initialFilters: Partial<PositionSearchFilters> = {};
    if (options.q !== undefined) initialFilters.query = options.q;
    if (options.examYear !== undefined) initialFilters.examYear = options.examYear;
    if (options.region !== undefined) initialFilters.region = options.region;
    if (options.city !== undefined) initialFilters.city = options.city;
    const controller = new PositionDiscoveryController({
      api: new QualificationApi(new ApiClient(undefined, session.accessToken)),
      store: new PositionStateStore(session.userId),
      initialFilters,
      preserveItemsOnRefreshError: true,
      onChange: (state) => {
        if (runtimes.owns(this, runtime)) this.setData({ state });
      },
    });
    runtime.controller = {
      discovery: controller,
      hasShown: false,
      dispose: () => {
        controller.dispose();
      },
    };
    this.setData({ state: controller.snapshot() });
    void controller.search();
  },

  onShow() {
    const controller = runtimes.get(this)?.controller;
    if (!controller) return;
    if (!controller.hasShown) {
      controller.hasShown = true;
      return;
    }
    void controller.discovery.refresh();
  },

  onUnload() {
    runtimes.detach(this);
  },

  onReachBottom() {
    void runtimes.get(this)?.controller?.discovery.loadMore();
  },

  goBack() {
    if (getCurrentPages().length > 1) {
      void wx.navigateBack();
      return;
    }
    void wx.redirectTo({ url: routes.positions });
  },

  inputQuery(event) {
    runtimes.get(this)?.controller?.discovery.setFilters({ query: event.detail.value });
  },

  inputExamYear(event) {
    runtimes.get(this)?.controller?.discovery.setFilters({ examYear: event.detail.value });
  },

  inputRegion(event) {
    runtimes.get(this)?.controller?.discovery.setFilters({ region: event.detail.value });
  },

  inputCity(event) {
    runtimes.get(this)?.controller?.discovery.setFilters({ city: event.detail.value });
  },

  search() {
    void runtimes.get(this)?.controller?.discovery.search();
  },

  clearFilters() {
    const controller = runtimes.get(this)?.controller?.discovery;
    if (!controller) return;
    controller.setFilters({ query: "", examYear: "", region: "", city: "" });
    void controller.search();
  },

  retry() {
    void runtimes.get(this)?.controller?.discovery.refresh();
  },

  loadMore() {
    void runtimes.get(this)?.controller?.discovery.loadMore();
  },

  openQualificationProfile() {
    void wx.navigateTo({ url: routes.qualificationProfile });
  },

  openPreparation() {
    void wx.switchTab({ url: routes.ability });
  },

  openPosition(event) {
    const positionId = String(event.currentTarget.dataset.id ?? "");
    if (positionId) void wx.navigateTo({ url: positionDetailUrl(positionId) });
  },

  toggleComparison(event) {
    const positionId = String(event.currentTarget.dataset.id ?? "");
    const result = runtimes.get(this)?.controller?.discovery.toggleComparison(positionId);
    if (result?.reason === "limit") {
      void wx.showToast({ title: "一次最多对比 3 个岗位", icon: "none" });
    }
  },

  clearComparison() {
    runtimes.get(this)?.controller?.discovery.clearComparison();
  },

  openComparison() {
    const ids = this.data.state?.selectedIds ?? [];
    if (ids.length >= 2) void wx.navigateTo({ url: positionCompareUrl(ids) });
  },
});
