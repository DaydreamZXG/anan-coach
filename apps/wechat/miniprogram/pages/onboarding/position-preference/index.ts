import { ApiClient } from "../../../api/client";
import type { ProvinceListResponse } from "../../../api/generated/types.gen";
import { OnboardingApi } from "../../../api/onboarding";
import { QualificationApi } from "../../../api/qualification";
import { routes } from "../../../app/navigation/routes";
import { PageControllerRegistry } from "../../../app/runtime/controller-registry";
import { currentAuthSession } from "../../../auth/session";
import {
  PositionPreferenceController,
  type PositionPreferenceControllerState,
} from "../../../features/onboarding/position-preference-controller";
import type { PositionLevel } from "../../../features/onboarding/position-preference";

interface PositionLevelOption {
  value: PositionLevel;
  label: string;
  selected: boolean;
}

interface PositionPreferencePageData {
  state: PositionPreferenceControllerState | null;
  regions: string[];
  levels: PositionLevelOption[];
  hasRequiredSelection: boolean;
  catalogLoading: boolean;
  catalogError: string;
  exitAfterSave: boolean;
}

interface PositionPreferencePageCustom {
  selectRegion(event: WechatMiniprogram.BaseEvent): void;
  toggleRelocation(): void;
  toggleLevel(event: WechatMiniprogram.BaseEvent): void;
  submit(): void;
  saveAndExit(): void;
  retry(): void;
  retryCatalog(): void;
  back(): void;
}

type PositionPreferencePage = WechatMiniprogram.Page.Instance<
  PositionPreferencePageData,
  PositionPreferencePageCustom
>;

const levelOptions: ReadonlyArray<Omit<PositionLevelOption, "selected">> = [
  { value: "central", label: "中央国家行政机关" },
  { value: "provincial", label: "省级以上机关" },
  { value: "municipal", label: "市（地）级机关" },
  { value: "county_or_below", label: "县（区）级及以下" },
];

const figmaRegionOrder = ["北京", "天津", "河北", "上海", "江苏", "浙江", "广东"];

const runtimes = new PageControllerRegistry<PositionPreferencePage, PositionPreferenceController>();

Page<PositionPreferencePageData, PositionPreferencePageCustom>({
  data: {
    state: null,
    regions: [],
    levels: levelRows(null),
    hasRequiredSelection: false,
    catalogLoading: true,
    catalogError: "",
    exitAfterSave: false,
  },

  onLoad() {
    mountController(this);
    void loadRegions(this);
  },

  onShow() {
    if (!runtimes.get(this)?.controller?.sessionIsCurrent()) {
      mountController(this);
      void loadRegions(this);
    }
  },

  onUnload() {
    runtimes.detach(this);
  },

  selectRegion(event) {
    const region = String(event.currentTarget.dataset.region ?? "");
    runtimes.get(this)?.controller?.selectRegion(region);
  },

  toggleRelocation() {
    runtimes.get(this)?.controller?.toggleRelocation();
  },

  toggleLevel(event) {
    const level = String(event.currentTarget.dataset.level ?? "");
    if (levelOptions.some((item) => item.value === level)) {
      runtimes.get(this)?.controller?.toggleLevel(level as PositionLevel);
    }
  },

  submit() {
    this.setData({ exitAfterSave: false });
    void runtimes.get(this)?.controller?.submit();
  },

  saveAndExit() {
    const controller = runtimes.get(this)?.controller;
    if (!controller) return;
    this.setData({ exitAfterSave: true });
    if (
      controller.snapshot().draft.preferredRegions.length &&
      controller.snapshot().draft.acceptedPositionLevels.length
    ) {
      void controller.submit();
      return;
    }
    void wx.redirectTo({ url: routes.profileArchive });
  },

  retry() {
    void runtimes.get(this)?.controller?.load();
  },

  retryCatalog() {
    void loadRegions(this);
  },

  back() {
    void wx.redirectTo({ url: routes.onboarding });
  },
});

function mountController(page: PositionPreferencePage): void {
  const session = currentAuthSession();
  if (!session) {
    runtimes.detach(page);
    void wx.reLaunch({ url: routes.login });
    return;
  }
  const runtime = runtimes.attach(page);
  const controller = new PositionPreferenceController({
    userId: session.userId,
    api: new OnboardingApi(new ApiClient(undefined, session.accessToken)),
    onChange: (state) => {
      if (runtimes.owns(page, runtime)) {
        page.setData({
          state,
          levels: levelRows(state),
          hasRequiredSelection:
            state.draft.preferredRegions.length > 0 &&
            state.draft.acceptedPositionLevels.length > 0,
        });
      }
    },
    onAdvance: () => {
      if (!runtimes.owns(page, runtime)) return;
      if (page.data.exitAfterSave) {
        page.setData({ exitAfterSave: false });
        void wx.redirectTo({ url: routes.profileArchive });
        return;
      }
      void wx.redirectTo({ url: routes.onboardingStudyPreference });
    },
    isSessionCurrent: () => {
      const current = currentAuthSession();
      return current?.userId === session.userId && current.accessToken === session.accessToken;
    },
  });
  runtime.controller = controller;
  const state = controller.snapshot();
  page.setData({
    state,
    levels: levelRows(state),
    hasRequiredSelection: false,
  });
  void controller.load();
}

async function loadRegions(page: PositionPreferencePage): Promise<void> {
  const session = currentAuthSession();
  if (!session) return;
  page.setData({ catalogLoading: true, catalogError: "" });
  try {
    const response = await new QualificationApi(
      new ApiClient(undefined, session.accessToken),
    ).getRegionCatalog();
    if (!sessionStillOwnsPage(page, session.userId, session.accessToken)) return;
    page.setData({
      regions: regionNames(response.data),
      catalogLoading: false,
      catalogError: "",
    });
  } catch {
    if (!sessionStillOwnsPage(page, session.userId, session.accessToken)) return;
    page.setData({
      regions: [],
      catalogLoading: false,
      catalogError: "国考岗位地区目录没有读取成功，请重新读取。",
    });
  }
}

function regionNames(response: ProvinceListResponse): string[] {
  const catalog = response.provinces
    .map((province) => province.name.trim())
    .filter((name, index, values) => Boolean(name) && values.indexOf(name) === index);
  return [
    ...figmaRegionOrder.filter((name) => catalog.includes(name)),
    ...catalog.filter((name) => !figmaRegionOrder.includes(name)),
  ];
}

function levelRows(state: PositionPreferenceControllerState | null): PositionLevelOption[] {
  return levelOptions.map((item) => ({
    ...item,
    selected: state?.draft.acceptedPositionLevels.includes(item.value) ?? false,
  }));
}

function sessionStillOwnsPage(
  page: PositionPreferencePage,
  userId: string,
  accessToken: string,
): boolean {
  const current = currentAuthSession();
  return (
    current?.userId === userId &&
    current.accessToken === accessToken &&
    Boolean(runtimes.get(page)?.controller?.sessionIsCurrent())
  );
}
