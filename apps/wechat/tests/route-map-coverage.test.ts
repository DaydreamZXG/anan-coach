import { describe, expect, it } from "vitest";

import routeMapJson from "../design/figma-delivery/route-map.json";
import appConfigJson from "../miniprogram/app.json";
// @ts-expect-error Vite exposes mini-program templates through its raw asset loader.
import captureImportTemplate from "../miniprogram/pages/capture-import/index.wxml?raw";
// @ts-expect-error Vite exposes mini-program logic through its raw asset loader.
import captureImportLogic from "../miniprogram/pages/capture-import/index.ts?raw";
// @ts-expect-error Vite exposes mini-program templates through its raw asset loader.
import essayTaskTemplate from "../miniprogram/pages/essay-task/index.wxml?raw";
// @ts-expect-error Vite exposes mini-program logic through its raw asset loader.
import essayTaskLogic from "../miniprogram/pages/essay-task/index.ts?raw";
// @ts-expect-error Vite exposes mini-program templates through its raw asset loader.
import positionGoalTemplate from "../miniprogram/pages/positions/goal/index.wxml?raw";
// @ts-expect-error Vite exposes mini-program logic through its raw asset loader.
import positionGoalLogic from "../miniprogram/pages/positions/goal/index.ts?raw";
// @ts-expect-error Vite exposes mini-program templates through its raw asset loader.
import practiceTemplate from "../miniprogram/pages/practice/index.wxml?raw";
// @ts-expect-error Vite exposes mini-program logic through its raw asset loader.
import practiceLogic from "../miniprogram/pages/practice/index.ts?raw";
// @ts-expect-error Vite exposes mini-program templates through its raw asset loader.
import privateKnowledgeTemplate from "../miniprogram/pages/private-knowledge/index.wxml?raw";
// @ts-expect-error Vite exposes mini-program logic through its raw asset loader.
import privateKnowledgeLogic from "../miniprogram/pages/private-knowledge/index.ts?raw";
// @ts-expect-error Vite exposes mini-program templates through its raw asset loader.
import reflectionTemplate from "../miniprogram/pages/reflection/index.wxml?raw";
// @ts-expect-error Vite exposes mini-program logic through its raw asset loader.
import reflectionLogic from "../miniprogram/pages/reflection/index.ts?raw";

interface DeliveryRouteMapping {
  route: string;
  routeState?: string;
}

interface DeliveryRouteMap {
  mappings: DeliveryRouteMapping[];
}

interface MiniProgramConfig {
  pages: string[];
  subPackages?: Array<{ root: string; pages: string[] }>;
}

const routeMap: DeliveryRouteMap = routeMapJson;
const appConfig: MiniProgramConfig = appConfigJson;

const pageSources: Readonly<Record<string, string>> = {
  "/pages/practice/index": pageSource(practiceTemplate, practiceLogic),
  "/pages/reflection/index": pageSource(reflectionTemplate, reflectionLogic),
  "/pages/positions/goal/index": pageSource(positionGoalTemplate, positionGoalLogic),
  "/pages/capture-import/index": pageSource(captureImportTemplate, captureImportLogic),
  "/pages/essay-task/index": pageSource(essayTaskTemplate, essayTaskLogic),
  "/pages/private-knowledge/index": pageSource(privateKnowledgeTemplate, privateKnowledgeLogic),
};

const stateMarkers: Readonly<Record<string, readonly string[]>> = {
  "/pages/practice/index#answering": ["sessionState === 'answering'"],
  "/pages/reflection/index#summary": ["phase === 'summary'", 'class="summary-content"'],
  "/pages/reflection/index#cause-review": ["phase === 'reflection'", 'class="review-content"'],
  "/pages/reflection/index#completed": [': "completed"', 'class="completed-screen"'],
  "/pages/positions/goal/index#impact-preview": [
    "state.status === 'ready' || state.status === 'confirming'",
    'class="goal-preview"',
  ],
  "/pages/positions/goal/index#confirmed": [
    "state.status === 'confirmed'",
    'class="goal-confirmed"',
  ],
  "/pages/capture-import/index#camera-permission": [
    'wx:elif="{{permissionDenied}}"',
    'class="permission-screen"',
  ],
  "/pages/capture-import/index#camera": ["state.status === 'idle'", 'class="camera-workspace"'],
  "/pages/capture-import/index#crop": ["captureStage === 'crop'", 'class="crop-stage"'],
  "/pages/capture-import/index#ocr-review": [
    "state.reviewStep === 'recognition'",
    'class="ocr-content"',
  ],
  "/pages/capture-import/index#question-bank-searching": [
    "state.status === 'uploading' || state.status === 'processing'",
    'class="searching-screen"',
  ],
  "/pages/capture-import/index#multiple-matches": [
    "state.activeQuestionUnit.match.candidates.length > 1",
    'class="match-list"',
  ],
  "/pages/capture-import/index#question-bank-miss": [
    "state.activeQuestionUnit.state === 'unmatched'",
    'class="no-hit-screen"',
  ],
  "/pages/capture-import/index#ai-solving": [
    "state.activeQuestionUnit.aiAnswer.state === 'requested'",
    "state.activeQuestionUnit.aiAnswer.state === 'processing'",
  ],
  "/pages/capture-import/index#question-bank-answer": [
    "state.activeQuestionUnit.referenceAnswer",
    "题库参考答案",
  ],
  "/pages/capture-import/index#ai-answer": [
    "state.activeQuestionUnit.aiAnswer.state === 'candidate'",
    "AI 推理答案",
  ],
  "/pages/capture-import/index#recorded": [
    'wx:elif="{{receiptVisible}}"',
    'class="receipt-screen"',
  ],
  "/pages/essay-task/index#drafting": [
    "capability.stage === 'essay_drafting'",
    'class="draft-writing-card"',
  ],
  "/pages/private-knowledge/index#library": ['class="library-content"'],
  "/pages/private-knowledge/index#processing": [
    "busy && !reviewRevision && !backgroundProcessing",
    'class="processing-screen"',
  ],
  "/pages/private-knowledge/index#citation-evidence": [
    "citationOpen && activeCitation",
    'class="citation-sheet motion-sheet"',
  ],
};

describe("Figma delivery route-map coverage", () => {
  it("registers every delivery route in app.json", () => {
    const registeredRoutes = new Set([
      ...appConfig.pages.map((page) => `/${page}`),
      ...(appConfig.subPackages ?? []).flatMap((subpackage) =>
        subpackage.pages.map((page) => `/${subpackage.root}/${page}`),
      ),
    ]);
    const missingRoutes = routeMap.mappings
      .filter((mapping) => !registeredRoutes.has(mapping.route))
      .map((mapping) => mapping.route);

    expect(missingRoutes).toEqual([]);
  });

  it("requires an explicit production marker for every mapped routeState", () => {
    const stateMappings = routeMap.mappings.filter(
      (mapping): mapping is Required<DeliveryRouteMapping> =>
        typeof mapping.routeState === "string",
    );
    const mappedKeys = stateMappings.map(mappingKey).sort();
    const registeredKeys = Object.keys(stateMarkers).sort();

    expect(registeredKeys).toEqual(mappedKeys);

    const missingMarkers = stateMappings.flatMap((mapping) => {
      const key = mappingKey(mapping);
      const source = pageSources[mapping.route];
      const markers = stateMarkers[key];
      if (!source) return [`${key}：未登记对应页面源码`];
      if (!markers) return [`${key}：未登记相态标识`];
      return markers
        .filter((marker) => !source.includes(marker))
        .map((marker) => `${key}：缺少 ${marker}`);
    });

    expect(missingMarkers).toEqual([]);
  });
});

function mappingKey(mapping: Required<DeliveryRouteMapping>): string {
  return `${mapping.route}#${mapping.routeState}`;
}

function pageSource(template: unknown, logic: unknown): string {
  if (typeof template !== "string" || typeof logic !== "string") {
    throw new Error("Mini-program route source must be text.");
  }
  return `${template}\n${logic}`;
}
