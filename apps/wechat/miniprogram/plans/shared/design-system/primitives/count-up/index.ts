import { COUNT_UP_FRAME_INTERVAL_MS, COUNT_UP_MAX_DURATION_MS, countUpFrames } from "./runtime";

interface CountUpHost {
  data: { value: number; duration: number; display: string };
  setData(patch: { display: string }): void;
  createSelectorQuery(): WechatMiniprogram.SelectorQuery;
}

interface CountUpState {
  timer: ReturnType<typeof setInterval> | null;
  /** 当前展示中的数值，作为下一次滚动的旧值。 */
  shown: number;
  /** reduce-motion 探针返回前先挂起，返回后再统一结算。 */
  probing: boolean;
  reduced: boolean;
}

const states = new WeakMap<object, CountUpState>();

Component({
  options: {
    virtualHost: true,
  },
  properties: {
    value: { type: Number, value: 0 },
    duration: { type: Number, value: COUNT_UP_MAX_DURATION_MS },
  },
  data: {
    display: "0",
  },
  observers: {
    value() {
      syncCountUp(this);
    },
  },
  lifetimes: {
    attached() {
      states.set(this, { timer: null, shown: 0, probing: true, reduced: false });
    },
    ready() {
      const state = states.get(this);
      if (!state) return;
      detectReducedMotion(this, (reduced) => {
        state.probing = false;
        state.reduced = reduced;
        syncCountUp(this);
      });
    },
    detached() {
      const state = states.get(this);
      if (state && state.timer !== null) clearInterval(state.timer);
      states.delete(this);
    },
  },
});

function syncCountUp(host: CountUpHost): void {
  const state = states.get(host);
  if (!state || state.probing) return;
  if (state.timer !== null) {
    clearInterval(state.timer);
    state.timer = null;
  }
  const target = host.data.value;
  const frames = state.reduced ? [] : countUpFrames(state.shown, target, host.data.duration);
  if (frames.length === 0) {
    state.shown = target;
    host.setData({ display: String(target) });
    return;
  }
  let index = 0;
  state.timer = setInterval(() => {
    const next = frames[index];
    index += 1;
    if (next !== undefined) {
      state.shown = next;
      host.setData({ display: String(next) });
    }
    if (index >= frames.length && state.timer !== null) {
      clearInterval(state.timer);
      state.timer = null;
    }
  }, COUNT_UP_FRAME_INTERVAL_MS);
}

/**
 * 逻辑层读不到系统"减少动效"，借 WXSS 探针量出来：
 * 探针仅在 prefers-reduced-motion: reduce 下有 1px 高度。
 */
function detectReducedMotion(host: CountUpHost, done: (reduced: boolean) => void): void {
  const query = host.createSelectorQuery();
  query.select(".count-up__probe").boundingClientRect();
  query.exec((results: Array<{ height?: number } | null>) => {
    const height = results[0]?.height ?? 0;
    done(height >= 0.5);
  });
}
