import type { ModuleAbility } from "../api/generated/types.gen";

/** 行测能力轴（moduleKey 与服务端 xingce track 模块一致）。 */
export const XINGCE_MODULE_BASELINE: ReadonlyArray<
  Pick<ModuleAbility, "moduleKey" | "displayName">
> = [
  { moduleKey: "政治理论", displayName: "政治理论" },
  { moduleKey: "常识判断", displayName: "常识判断" },
  { moduleKey: "言语理解与表达", displayName: "言语理解与表达" },
  { moduleKey: "数量关系", displayName: "数量关系" },
  { moduleKey: "判断推理", displayName: "判断推理" },
  { moduleKey: "资料分析", displayName: "资料分析" },
];

/** 申论能力轴（moduleKey 与服务端 essay track 模块一致）。 */
export const ESSAY_MODULE_BASELINE: ReadonlyArray<
  Pick<ModuleAbility, "moduleKey" | "displayName">
> = [
  { moduleKey: "point_finding", displayName: "找点与覆盖" },
  { moduleKey: "abstraction", displayName: "概括与归纳" },
  { moduleKey: "structure", displayName: "结构组织" },
  { moduleKey: "expression", displayName: "表达准确性" },
  { moduleKey: "format", displayName: "格式规范" },
  { moduleKey: "unsupported_claim", displayName: "材料依据" },
];
