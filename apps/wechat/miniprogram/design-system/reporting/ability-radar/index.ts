export interface AbilityRadarPoint {
  label: string;
  value: number;
}

interface RadarRuntime {
  canvas: WechatMiniprogram.Canvas | null;
  context: WechatMiniprogram.CanvasRenderingContext.CanvasRenderingContext2D | null;
  width: number;
  height: number;
}

const runtimes = new WeakMap<object, RadarRuntime>();

function runtime(instance: object): RadarRuntime {
  const current = runtimes.get(instance);
  if (!current) throw new Error("能力雷达图尚未初始化。");
  return current;
}

function normalizedPoints(value: unknown): AbilityRadarPoint[] {
  if (!Array.isArray(value)) return [];
  return value
    .filter(
      (item): item is AbilityRadarPoint =>
        typeof item === "object" &&
        item !== null &&
        typeof (item as AbilityRadarPoint).label === "string" &&
        typeof (item as AbilityRadarPoint).value === "number",
    )
    .map((item) => ({
      label: item.label.trim().slice(0, 8),
      value: Math.max(0, Math.min(100, item.value)),
    }))
    .slice(0, 8);
}

Component({
  properties: {
    points: {
      type: Array,
      value: [] as AbilityRadarPoint[],
      observer() {
        this.draw();
      },
    },
  },
  lifetimes: {
    attached() {
      runtimes.set(this, {
        canvas: null,
        context: null,
        width: 0,
        height: 0,
      });
    },
    ready() {
      wx.createSelectorQuery()
        .in(this)
        .select("#abilityRadarCanvas")
        .fields({ node: true, size: true }, (raw) => {
          const result = raw as {
            node?: WechatMiniprogram.Canvas;
            width?: number;
            height?: number;
          };
          if (!result.node || !result.width || !result.height) return;
          const current = runtime(this);
          const pixelRatio = wx.getWindowInfo().pixelRatio;
          result.node.width = result.width * pixelRatio;
          result.node.height = result.height * pixelRatio;
          const context = result.node.getContext("2d");
          context.scale(pixelRatio, pixelRatio);
          current.canvas = result.node;
          current.context = context;
          current.width = result.width;
          current.height = result.height;
          this.draw();
        })
        .exec();
    },
    detached() {
      runtimes.delete(this);
    },
  },
  methods: {
    draw() {
      const current = runtimes.get(this);
      const context = current?.context;
      if (!current || !context) return;
      const points = normalizedPoints(this.properties.points);
      context.clearRect(0, 0, current.width, current.height);
      if (points.length < 3) return;

      const centerX = current.width / 2;
      const centerY = current.height / 2 + 4;
      const radius = Math.min(current.width * 0.29, current.height * 0.34);
      const angleStep = (Math.PI * 2) / points.length;
      const startAngle = -Math.PI / 2;

      context.lineJoin = "round";
      for (let level = 1; level <= 4; level += 1) {
        const levelRadius = radius * (level / 4);
        context.beginPath();
        points.forEach((_, index) => {
          const angle = startAngle + angleStep * index;
          const x = centerX + Math.cos(angle) * levelRadius;
          const y = centerY + Math.sin(angle) * levelRadius;
          if (index === 0) context.moveTo(x, y);
          else context.lineTo(x, y);
        });
        context.closePath();
        context.strokeStyle = "#eef0f4";
        context.lineWidth = 1;
        context.stroke();
      }

      points.forEach((_, index) => {
        const angle = startAngle + angleStep * index;
        context.beginPath();
        context.moveTo(centerX, centerY);
        context.lineTo(centerX + Math.cos(angle) * radius, centerY + Math.sin(angle) * radius);
        context.strokeStyle = "#f0f2f6";
        context.lineWidth = 1;
        context.stroke();
      });

      context.beginPath();
      points.forEach((point, index) => {
        const angle = startAngle + angleStep * index;
        const pointRadius = radius * (point.value / 100);
        const x = centerX + Math.cos(angle) * pointRadius;
        const y = centerY + Math.sin(angle) * pointRadius;
        if (index === 0) context.moveTo(x, y);
        else context.lineTo(x, y);
      });
      context.closePath();
      context.fillStyle = "rgba(17, 24, 39, 0.08)";
      context.strokeStyle = "#111827";
      context.lineWidth = 2;
      context.fill();
      context.stroke();

      if (points.every((point) => point.value === 0)) {
        context.beginPath();
        context.arc(centerX, centerY, 4, 0, Math.PI * 2);
        context.fillStyle = "#111827";
        context.fill();
        context.fillStyle = "#9ca3af";
        context.font = "600 10px -apple-system, BlinkMacSystemFont, PingFang SC, sans-serif";
        context.textAlign = "center";
        context.textBaseline = "top";
        context.fillText("0", centerX, centerY + 9);
      }

      context.fillStyle = "#6b7280";
      context.font = "600 11px -apple-system, BlinkMacSystemFont, PingFang SC, sans-serif";
      context.textBaseline = "middle";
      points.forEach((point, index) => {
        const angle = startAngle + angleStep * index;
        const labelRadius = radius + 24;
        const x = centerX + Math.cos(angle) * labelRadius;
        const y = centerY + Math.sin(angle) * labelRadius;
        const horizontal = Math.cos(angle);
        context.textAlign = horizontal > 0.25 ? "left" : horizontal < -0.25 ? "right" : "center";
        context.fillText(point.label, x, y);
      });
    },
  },
});
