type VisualStage = "stage-1" | "stage-2" | "stage-3" | "stage-4";

const stages = new Set<VisualStage>(["stage-1", "stage-2", "stage-3", "stage-4"]);
const assetRoot = "/packages/anan-growth/assets/v1.0.0";

Component({
  options: {
    virtualHost: true,
  },

  properties: {
    visualStage: {
      type: String,
      value: "",
      observer(value: string) {
        this.setData({ src: growthSource(value) });
      },
    },
  },

  data: {
    src: `${assetRoot}/companion-hero-240@2x.webp`,
  },
});

function growthSource(value: string): string {
  if (!stages.has(value as VisualStage)) return `${assetRoot}/companion-hero-240@2x.webp`;
  return `${assetRoot}/${value}-hero-240@2x.webp`;
}
