import type {
  PositionPreferenceProfile,
  PositionPreferenceWrite,
} from "../../api/generated/types.gen";

export type PositionLevel = PositionPreferenceWrite["acceptedPositionLevels"][number];

export interface PositionPreferenceDraft {
  preferredRegions: string[];
  acceptsRelocation: boolean;
  acceptedPositionLevels: PositionLevel[];
}

export const emptyPositionPreferenceDraft: Readonly<PositionPreferenceDraft> = {
  preferredRegions: [],
  acceptsRelocation: false,
  acceptedPositionLevels: [],
};

export function positionPreferenceDraftFromProfile(
  profile: PositionPreferenceProfile,
): PositionPreferenceDraft {
  return {
    preferredRegions: [...profile.preferredRegions],
    acceptsRelocation: profile.acceptsRelocation,
    acceptedPositionLevels: [...profile.acceptedPositionLevels],
  };
}

export function validatePositionPreferenceDraft(draft: PositionPreferenceDraft): string {
  if (!draft.preferredRegions.length) return "请选择至少一个首选工作地区。";
  if (!draft.acceptedPositionLevels.length) return "请选择至少一个可接受的岗位层级。";
  return "";
}

export function buildPositionPreferenceCommand(
  draft: PositionPreferenceDraft,
  expectedPreferenceVersion: number,
): PositionPreferenceWrite {
  const validationError = validatePositionPreferenceDraft(draft);
  if (validationError) throw new Error(validationError);
  return {
    expectedPreferenceVersion,
    preferredRegions: [...draft.preferredRegions],
    acceptsRelocation: draft.acceptsRelocation,
    acceptedPositionLevels: [...draft.acceptedPositionLevels],
  };
}
