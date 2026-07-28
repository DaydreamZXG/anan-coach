import { ApiClient } from "../../api/client";
import type { MajorTreeResponse } from "../../api/generated/types.gen";
import { OnboardingApi } from "../../api/onboarding";
import { QualificationApi } from "../../api/qualification";
import { onboardingNavigationTarget, routes } from "../../app/navigation/routes";
import { PageControllerRegistry } from "../../app/runtime/controller-registry";
import { currentAuthSession } from "../../auth/session";
import {
  QualificationController,
  type QualificationControllerState,
} from "../../features/onboarding/qualification-controller";
import type { OnboardingNextStep } from "../../features/onboarding/types";
import {
  emptyQualificationDraft,
  qualificationFactsFromDraft,
  type QualificationDraft,
} from "../../features/qualification/onboarding";
import { createIdempotencyKey } from "../../platform/command-id";

interface GraduationOption {
  label: string;
  year: number;
  freshGraduate: "true" | "false";
}

interface MajorOption {
  code: string;
  name: string;
  category: string;
}

interface QualificationPageData extends QualificationDraft {
  educationOptions: string[];
  degreeOptions: string[];
  graduationOptions: GraduationOption[];
  serviceProjectOptions: string[];
  majorSuggestions: MajorOption[];
  majorCategory: string;
  graduationLabel: string;
  serviceExpanded: boolean;
  error: string;
  requestId: string;
  saving: boolean;
  savingDraft: boolean;
  profileVersion: number;
  loadingProfile: boolean;
  loadFailed: boolean;
  catalogLoading: boolean;
  catalogError: string;
  pendingRecovery: boolean;
  primaryDisabled: boolean;
  primaryActionLabel: string;
}

interface QualificationPageCustom {
  retryLoad(): void;
  retryCatalog(): void;
  inputMajor(event: WechatMiniprogram.CustomEvent<{ value: string }>): void;
  selectMajor(event: WechatMiniprogram.BaseEvent): void;
  chooseEducation(event: WechatMiniprogram.PickerChange): void;
  chooseDegree(event: WechatMiniprogram.PickerChange): void;
  chooseGraduation(event: WechatMiniprogram.PickerChange): void;
  chooseServiceMode(event: WechatMiniprogram.BaseEvent): void;
  chooseServiceProject(event: WechatMiniprogram.BaseEvent): void;
  chooseWorkYears(event: WechatMiniprogram.BaseEvent): void;
  submit(): void;
  saveAndExit(): void;
  markLater(): void;
  back(): void;
}

type QualificationPage = WechatMiniprogram.Page.Instance<
  QualificationPageData,
  QualificationPageCustom
>;

const educationOptions = ["大专", "本科", "硕士", "博士"];
const degreeOptions = ["无学位", "学士", "硕士", "博士"];
const graduationOptions: GraduationOption[] = [
  { label: "2026（应届）", year: 2026, freshGraduate: "true" },
  { label: "2025（择业期应届）", year: 2025, freshGraduate: "true" },
  { label: "2025（社会人员）", year: 2025, freshGraduate: "false" },
  { label: "2024 及以前", year: 2024, freshGraduate: "false" },
];
const serviceProjectOptions = [
  "大学生村官",
  "三支一扶",
  "特岗教师",
  "西部计划",
  "高校毕业生退役士兵",
  "其他服务基层项目",
];

const runtimes = new PageControllerRegistry<QualificationPage, QualificationController>();
const catalogs = new WeakMap<QualificationPage, MajorOption[]>();
const partialSaveKeys = new WeakMap<QualificationPage, string>();

Page<QualificationPageData, QualificationPageCustom>({
  data: {
    ...emptyQualificationDraft,
    educationOptions,
    degreeOptions,
    graduationOptions,
    serviceProjectOptions,
    majorSuggestions: [],
    majorCategory: "",
    graduationLabel: "",
    serviceExpanded: false,
    error: "",
    requestId: "",
    saving: false,
    savingDraft: false,
    profileVersion: 0,
    loadingProfile: true,
    loadFailed: false,
    catalogLoading: true,
    catalogError: "",
    pendingRecovery: false,
    primaryDisabled: true,
    primaryActionLabel: "继续：填写岗位偏好",
  },

  onLoad() {
    mountController(this);
    void loadMajorCatalog(this);
  },

  onShow() {
    if (!runtimes.get(this)?.controller?.sessionIsCurrent()) {
      mountController(this);
      void loadMajorCatalog(this);
    }
  },

  onUnload() {
    runtimes.detach(this);
    catalogs.delete(this);
    partialSaveKeys.delete(this);
  },

  retryLoad() {
    void runtimes.get(this)?.controller?.load();
  },

  retryCatalog() {
    void loadMajorCatalog(this);
  },

  inputMajor(event) {
    const major = event.detail.value.trimStart();
    runtimes.get(this)?.controller?.changeDraft({ major });
    this.setData({
      majorCategory: "",
      majorSuggestions: searchMajors(catalogs.get(this) ?? [], major),
    });
  },

  selectMajor(event) {
    const name = String(event.currentTarget.dataset.name ?? "");
    const category = String(event.currentTarget.dataset.category ?? "");
    if (!name) return;
    runtimes.get(this)?.controller?.changeDraft({ major: name });
    this.setData({ majorCategory: category, majorSuggestions: [] });
  },

  chooseEducation(event) {
    const education = educationOptions[Number(event.detail.value)];
    if (education) runtimes.get(this)?.controller?.changeDraft({ education });
  },

  chooseDegree(event) {
    const degree = degreeOptions[Number(event.detail.value)];
    if (degree) runtimes.get(this)?.controller?.changeDraft({ degree });
  },

  chooseGraduation(event) {
    const option = graduationOptions[Number(event.detail.value)];
    if (!option) return;
    runtimes.get(this)?.controller?.changeDraft({
      graduationYear: option.year,
      freshGraduate: option.freshGraduate,
    });
  },

  chooseServiceMode(event) {
    const mode = String(event.currentTarget.dataset.mode ?? "");
    if (mode === "none") {
      runtimes.get(this)?.controller?.changeDraft({
        serviceProject: "无",
        grassrootsYears: 0,
      });
      this.setData({ serviceExpanded: false });
      return;
    }
    if (mode === "has") {
      const current = this.data.serviceProject;
      runtimes.get(this)?.controller?.changeDraft({
        serviceProject: current === "无" ? "" : current,
        grassrootsYears: current === "无" ? -1 : this.data.grassrootsYears,
      });
      this.setData({ serviceExpanded: true });
    }
  },

  chooseServiceProject(event) {
    const serviceProject = String(event.currentTarget.dataset.value ?? "");
    if (!serviceProjectOptions.includes(serviceProject)) return;
    runtimes.get(this)?.controller?.changeDraft({ serviceProject });
    this.setData({ serviceExpanded: true });
  },

  chooseWorkYears(event) {
    const workYears = Number(event.currentTarget.dataset.value);
    if (Number.isSafeInteger(workYears) && workYears >= 0) {
      runtimes.get(this)?.controller?.changeDraft({ workYears });
    }
  },

  submit() {
    void submitAndContinue(this);
  },

  saveAndExit() {
    void savePartialAndExit(this);
  },

  markLater() {
    void savePartialAndExit(this);
  },

  back() {
    void wx.redirectTo({ url: routes.onboardingPreparationExperience });
  },
});

function mountController(page: QualificationPage): void {
  const session = currentAuthSession();
  if (!session) {
    runtimes.detach(page);
    void wx.reLaunch({ url: routes.login });
    return;
  }
  const client = new ApiClient(undefined, session.accessToken);
  const runtime = runtimes.attach(page);
  const controller = new QualificationController({
    userId: session.userId,
    profileApi: new QualificationApi(client),
    progressApi: new OnboardingApi(client),
    onChange: (state) => {
      if (runtimes.owns(page, runtime)) page.setData(pageState(state));
    },
    onAdvance: (step) => {
      if (runtimes.owns(page, runtime)) navigateOnboardingStep(step);
    },
    isSessionCurrent: () => {
      const current = currentAuthSession();
      return current?.userId === session.userId && current.accessToken === session.accessToken;
    },
  });
  runtime.controller = controller;
  page.setData(pageState(controller.snapshot()));
  void controller.load();
}

function pageState(state: QualificationControllerState): Partial<QualificationPageData> {
  const draft = state.draft;
  return {
    ...draft,
    graduationLabel: graduationLabel(draft),
    serviceExpanded: Boolean(draft.serviceProject && draft.serviceProject !== "无"),
    error: state.validationError || state.error?.message || "",
    requestId: state.error?.requestId ?? "",
    saving: state.saving,
    profileVersion: state.profileVersion,
    loadingProfile: state.status === "idle" || state.status === "loading",
    loadFailed: state.status === "error",
    pendingRecovery: state.pendingRecovery,
    primaryDisabled:
      state.status !== "ready" ||
      state.saving ||
      !draft.education ||
      !draft.degree ||
      draft.major.trim().length < 2 ||
      draft.graduationYear < 2000 ||
      !draft.freshGraduate ||
      !draft.serviceProject ||
      draft.workYears < 0,
    primaryActionLabel: state.pendingRecovery ? "查询原确认结果" : "继续：填写岗位偏好",
  };
}

async function loadMajorCatalog(page: QualificationPage): Promise<void> {
  const session = currentAuthSession();
  if (!session) return;
  page.setData({ catalogLoading: true, catalogError: "", majorSuggestions: [] });
  try {
    const response = await new QualificationApi(
      new ApiClient(undefined, session.accessToken),
    ).getMajorCatalog();
    const current = currentAuthSession();
    if (
      current?.userId !== session.userId ||
      current.accessToken !== session.accessToken ||
      !runtimes.get(page)?.controller?.sessionIsCurrent()
    ) {
      return;
    }
    const catalog = flattenMajorCatalog(response.data);
    catalogs.set(page, catalog);
    page.setData({
      catalogLoading: false,
      catalogError: "",
      majorSuggestions: searchMajors(catalog, page.data.major),
    });
  } catch {
    const current = currentAuthSession();
    if (
      current?.userId !== session.userId ||
      current.accessToken !== session.accessToken ||
      !runtimes.get(page)?.controller?.sessionIsCurrent()
    ) {
      return;
    }
    catalogs.delete(page);
    page.setData({
      catalogLoading: false,
      catalogError: "专业目录没有读取成功，请重新读取后再选择专业。",
      majorSuggestions: [],
    });
  }
}

function flattenMajorCatalog(tree: MajorTreeResponse): MajorOption[] {
  return tree.categories.flatMap((category) =>
    category.subCategories.flatMap((subcategory) =>
      subcategory.majors.map((major) => ({
        code: major.code,
        name: major.name,
        category: `${category.name} · ${subcategory.name}`,
      })),
    ),
  );
}

function searchMajors(catalog: MajorOption[], query: string): MajorOption[] {
  const normalized = query.trim().toLocaleLowerCase();
  if (!normalized) return [];
  return catalog
    .filter(
      (major) =>
        major.name.toLocaleLowerCase().includes(normalized) ||
        major.code.toLocaleLowerCase().includes(normalized),
    )
    .slice(0, 6);
}

function graduationLabel(draft: QualificationDraft): string {
  if (draft.graduationYear < 2000 || !draft.freshGraduate) return "";
  return `${String(draft.graduationYear)}（${
    draft.freshGraduate === "true" ? "应届身份" : "社会人员"
  }）`;
}

async function submitAndContinue(page: QualificationPage): Promise<void> {
  const controller = runtimes.get(page)?.controller;
  if (!controller || page.data.catalogLoading || page.data.catalogError) return;
  await controller.submit();
  if (controller.snapshot().saved) controller.continue();
}

async function savePartialAndExit(page: QualificationPage): Promise<void> {
  const session = currentAuthSession();
  if (!session || page.data.savingDraft || page.data.saving) return;
  const facts = qualificationFactsFromDraft(page.data);
  if (!facts.length) {
    void wx.redirectTo({ url: routes.profileArchive });
    return;
  }
  page.setData({ savingDraft: true, error: "", requestId: "" });
  try {
    let commandKey = partialSaveKeys.get(page);
    if (!commandKey) {
      commandKey = await createIdempotencyKey("qualification-draft");
      partialSaveKeys.set(page, commandKey);
    }
    await new QualificationApi(new ApiClient(undefined, session.accessToken)).updateProfile(
      facts,
      commandKey,
    );
    partialSaveKeys.delete(page);
    page.setData({ savingDraft: false });
    void wx.redirectTo({ url: routes.profileArchive });
  } catch (error) {
    page.setData({
      savingDraft: false,
      error: error instanceof Error ? error.message : "报考资料暂存失败，请重试。",
    });
  }
}

function navigateOnboardingStep(step: OnboardingNextStep): void {
  if (step === "qualification") return;
  const target = onboardingNavigationTarget(step);
  if (target.mode === "switchTab") void wx.switchTab({ url: target.url });
  else void wx.redirectTo({ url: target.url });
}
