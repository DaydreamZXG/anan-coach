const mascot =
  "../miniprogram/design-system/companion/anan-companion/assets/v1.0.0/core/card-160@2x.webp";
const avatar =
  "../miniprogram/design-system/companion/anan-companion/assets/v1.0.0/core/avatar-32@3x.webp";

const iconPaths = {
  today:
    '<path d="M12 3v2M12 19v2M3 12h2M19 12h2M5.64 5.64l1.42 1.42M16.94 16.94l1.42 1.42M18.36 5.64l-1.42 1.42M7.06 16.94l-1.42 1.42"/><circle cx="12" cy="12" r="4"/>',
  learn:
    '<path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H11v17H6.5A2.5 2.5 0 0 0 4 22.5v-17ZM20 5.5A2.5 2.5 0 0 0 17.5 3H13v17h4.5a2.5 2.5 0 0 1 2.5 2.5v-17Z"/>',
  position:
    '<circle cx="12" cy="12" r="7"/><circle cx="12" cy="12" r="2"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3"/>',
  profile: '<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>',
  chart: '<path d="M4 19V9M10 19V5M16 19v-8M22 19V2"/><path d="m4 8 6-4 6 5 6-7"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  spark: '<path d="M5 12h3l2-6 4 12 2-6h3"/>',
  shield:
    '<path d="M12 3 5 6v5c0 4.5 2.7 7.6 7 10 4.3-2.4 7-5.5 7-10V6l-7-3Z"/><path d="m9 12 2 2 4-5"/>',
  check: '<path d="m5 12 4 4L19 6"/>',
  chevron: '<path d="m9 18 6-6-6-6"/>',
  target:
    '<circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="3"/><path d="M18 6 21 3M21 3v4M21 3h-4"/>',
  report: '<path d="M5 3h11l3 3v15H5V3Z"/><path d="M16 3v4h4M8 11h8M8 15h8"/>',
  lock: '<rect x="5" y="10" width="14" height="10" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3"/>',
  crown: '<path d="m4 8 4 4 4-7 4 7 4-4-2 11H6L4 8Z"/>',
  headset:
    '<path d="M4 13v-2a8 8 0 0 1 16 0v2"/><path d="M4 13h3v6H5a2 2 0 0 1-2-2v-2a2 2 0 0 1 1-2ZM20 13h-3v6h2a2 2 0 0 0 2-2v-2a2 2 0 0 0-1-2Z"/>',
  settings:
    '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .34 1.88l.06.06-2.83 2.83-.06-.06a1.7 1.7 0 0 0-1.88-.34 1.7 1.7 0 0 0-1.03 1.56V21h-4v-.09A1.7 1.7 0 0 0 9 19.36a1.7 1.7 0 0 0-1.88.34l-.06.06-2.83-2.83.06-.06A1.7 1.7 0 0 0 4.64 15 1.7 1.7 0 0 0 3.09 14H3v-4h.09A1.7 1.7 0 0 0 4.64 9a1.7 1.7 0 0 0-.34-1.88l-.06-.06 2.83-2.83.06.06A1.7 1.7 0 0 0 9 4.64 1.7 1.7 0 0 0 10 3.09V3h4v.09A1.7 1.7 0 0 0 15 4.64a1.7 1.7 0 0 0 1.88-.34l.06-.06 2.83 2.83-.06.06A1.7 1.7 0 0 0 19.36 9 1.7 1.7 0 0 0 20.91 10H21v4h-.09A1.7 1.7 0 0 0 19.4 15Z"/>',
  wechat:
    '<path d="M8.5 5C4.9 5 2 7.3 2 10.2c0 1.7 1 3.2 2.5 4.2L4 16.8l2.4-1.3c.7.2 1.4.3 2.1.3 3.6 0 6.5-2.3 6.5-5.3S12.1 5 8.5 5Z"/><path d="M15.5 9c3.6 0 6.5 2.3 6.5 5.2 0 1.7-1 3.2-2.5 4.2l.5 2.4-2.4-1.3c-.7.2-1.4.3-2.1.3-2.1 0-4-.8-5.2-2.1"/><circle cx="6.5" cy="9.5" r=".7" fill="currentColor" stroke="none"/><circle cx="10.5" cy="9.5" r=".7" fill="currentColor" stroke="none"/>',
};

function icon(name, size = 20, className = "") {
  return `<svg class="ui-icon ${className}" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${iconPaths[name] ?? iconPaths.spark}</svg>`;
}

function tabbar(active) {
  const tabs = [
    ["today", "今日"],
    ["learn", "训练"],
    ["position", "岗位"],
    ["profile", "我的"],
  ];
  return `<nav class="tabbar" aria-label="主导航">${tabs
    .map(
      ([key, label]) => `<button data-nav="${key}" class="${active === key ? "is-active" : ""}">
        <span class="tabbar__icon-wrap">${icon(key, 20)}</span><span>${label}</span>
      </button>`,
    )
    .join("")}</nav>`;
}

function loginScreen() {
  return `<section class="app-page login-page">
    <div class="login-brand"><i>岸</i><span>岸岸</span></div>
    <div class="login-copy">
      <span class="eyebrow">基于真实题与岗位数据的 AI 备考伙伴</span>
      <h1>把分差，<br />变成<em>今天的行动</em>。</h1>
      <p>不承诺上岸，不制造焦虑。只根据你的岗位、能力证据和可用时间，告诉你今天最该练什么。</p>
    </div>
    <div class="login-mascot"><img src="${mascot}" alt="岸岸，黑白猫学习伙伴" /></div>
    <div class="login-actions">
      <button class="wechat-button">${icon("wechat", 21)} 微信登录</button>
      <p class="privacy-note">${icon("shield", 13)} 数据加密存储，可随时导出与删除</p>
    </div>
  </section>`;
}

function todayScreen() {
  return `<section class="app-page">
    <div class="app-scroll">
      <header class="app-topbar">
        <div class="app-topbar__copy"><span>7 月 17 日 · 星期五</span><h2>今日</h2></div>
        <button class="icon-button" aria-label="查看趋势">${icon("chart", 20)}</button>
      </header>
      <section class="card today-hero">
        <div class="today-hero__copy"><span class="screen-kicker">今日可用学习时长</span><h3>78<small>分钟</small></h3><p>目标 120 分钟 · 已按你的精力调整</p></div>
        <div class="progress-ring">${icon("clock", 21)}</div>
        <img class="today-hero__mascot" src="${mascot}" alt="岸岸" />
        <div class="goal-inline"><span>主目标 · 上海税务局</span><strong>分差约 1.8 分</strong></div>
      </section>
      <div class="section-row"><h3>首要任务</h3><span>共 3 项 · 预计 78 分钟</span></div>
      <section class="card task-card">
        <div class="task-card__top"><span class="chip">AI 推荐</span><span class="chip chip--gold">+0.6 分预期</span></div>
        <h4>资料分析 · 基期与比重</h4>
        <p>先收回重复失分，再扩大题型范围。</p>
        <div class="rationale-list">
          <span><i></i>近 3 次模考，本模块共丢 14 分</span>
          <span><i></i>基期量正确率 54%，处于首次复测窗口</span>
          <span><i></i>岗位近 5 年资料题占比稳定在 18%</span>
        </div>
        <div class="task-progress"><i></i></div>
        <div class="task-card__metrics"><span>${icon("report", 14)} 20 题</span><span>${icon("clock", 14)} 25 分钟</span><span>掌握度 54%</span></div>
        <button class="primary-button" style="margin-top:14px">开始训练 ${icon("chevron", 18)}</button>
      </section>
      <div class="daily-adjustment">
        <span class="daily-adjustment__icon">${icon("spark", 18)}</span>
        <div><b>今天状态变了？</b><small>调整时间、精力或压力，计划会重新排序</small></div><span>${icon("chevron", 17)}</span>
      </div>
    </div>
    ${tabbar("today")}
  </section>`;
}

function positionScreen() {
  return `<section class="app-page position-page">
    <div class="app-scroll">
      <header class="app-topbar">
        <div class="app-topbar__copy"><span>国考 · 2027 年度</span><h2>岗位决策</h2></div>
        <button class="icon-button" aria-label="岗位帮助">${icon("target", 20)}</button>
      </header>
      <section class="card position-card">
        <div class="position-card__header"><small>主目标岗位</small><span class="mini-badge">稳妥</span></div>
        <h3>国家税务总局<br />上海市税务局</h3>
        <div class="tag-row"><span>市局机关</span><span>一级行政执法员（一）</span><span>本科及以上</span></div>
      </section>
      <section class="eligibility-card">
        <span class="eligibility-card__icon">${icon("check", 17)}</span>
        <div><b>你已满足该岗位报考条件</b><small>专业、学历、政治面貌等硬条件均已核验；结论来自资格档案 v6。</small></div>
      </section>
      <section class="card score-band-card">
        <div class="score-band-card__title"><span>近 3 年进面分范围</span><small>非录取概率</small></div>
        <div class="band-values"><div><b>125.3</b><small>最低进面</small></div><div><b>133.8</b><small>平均进面</small></div><div><b>142.6</b><small>最高进面</small></div></div>
        <div class="band-line"><i></i></div>
        <div class="score-prediction">你的预测总分 · 132–135</div>
      </section>
      <section class="card evidence-card">
        <div><span>当前分差</span><b>-1.8 ～ +1.2</b><small>以平均进面分 133.8 估计</small></div>
        <div><span>证据等级</span><b>中等可信</b><small>样本 8 人 · 更新至 2026</small></div>
      </section>
      <div class="section-row"><h3>岸岸提醒</h3><span>基于证据，不替你决定</span></div>
      <section class="daily-adjustment">
        <span class="daily-adjustment__icon">${icon("spark", 18)}</span>
        <div><b>岗位热度波动较大</b><small>建议保留 1 个招录人数更多的稳妥岗位</small></div><span>${icon("chevron", 17)}</span>
      </section>
      <button class="primary-button position-cta">查看岗位依据 ${icon("chevron", 18)}</button>
    </div>
    ${tabbar("position")}
  </section>`;
}

function reportScreen() {
  return `<section class="app-page">
    <div class="app-scroll">
      <header class="app-topbar">
        <div class="app-topbar__copy"><span>学习大脑 · 76 条有效证据</span><h2>学习报告</h2></div>
        <button class="icon-button" aria-label="分享报告">${icon("report", 20)}</button>
      </header>
      <div class="segment-tabs"><button class="is-active">本周</button><button>近四周</button><button>全部</button></div>
      <section class="card report-score">
        <div class="report-score__range"><span>预测分区间 · 行测 + 申论</span><h3>118–128<small>分</small></h3><div class="report-score__change">↑ 较上周提升 6 分</div></div>
        <img src="${mascot}" alt="岸岸" />
      </section>
      <div class="dual-score">
        <section class="card"><span>行测得分区间</span><b>64–68</b><small>较上周 ↑ 4 分</small></section>
        <section class="card"><span>申论得分区间</span><b>54–60</b><small>较上周 ↑ 4 分</small></section>
      </div>
      <section class="card trend-card">
        <div class="trend-card__header"><div><span>能力趋势 · 总分预测中位数</span><b>连续四周上升</b></div><small>口径 v8</small></div>
        <svg class="trend-chart" viewBox="0 0 320 86" role="img" aria-label="能力趋势从 106 分上升到 120 分">
          <defs><linearGradient id="trend-fill" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#18aa9b" stop-opacity=".26"/><stop offset="1" stop-color="#18aa9b" stop-opacity="0"/></linearGradient></defs>
          <path d="M8 68 C48 62,55 53,84 55 S136 47,162 43 S214 31,244 30 S286 21,312 18 L312 84 L8 84Z" fill="url(#trend-fill)"/>
          <path d="M8 68 C48 62,55 53,84 55 S136 47,162 43 S214 31,244 30 S286 21,312 18" fill="none" stroke="#00978b" stroke-width="2.4" stroke-linecap="round"/>
          <g fill="#fff" stroke="#00978b" stroke-width="2"><circle cx="8" cy="68" r="3.5"/><circle cx="84" cy="55" r="3.5"/><circle cx="162" cy="43" r="3.5"/><circle cx="244" cy="30" r="3.5"/><circle cx="312" cy="18" r="3.5"/></g>
        </svg>
      </section>
      <section class="card diagnosis-card">
        <span class="diagnosis-card__icon">${icon("spark", 18)}</span>
        <div><span>本周诊断</span><b>问法方向识别仍有偏差</b><small>“原因类”偶尔识别为“对策类”，导致要点偏离题意。下周优先安排两次迁移验证。</small></div>
      </section>
      <button class="primary-button" style="margin-top:12px">查看下周调整 ${icon("chevron", 18)}</button>
    </div>
    ${tabbar("learn")}
  </section>`;
}

function listRow(iconName, title, copy) {
  return `<div class="list-row"><span class="list-row__icon">${icon(iconName, 18)}</span><div class="list-row__copy"><b>${title}</b><small>${copy}</small></div><span class="list-row__chevron">${icon("chevron", 17)}</span></div>`;
}

function profileScreen() {
  return `<section class="app-page">
    <div class="app-scroll">
      <header class="app-topbar">
        <div class="app-topbar__copy"><span>账户、权益与长期学习档案</span><h2>我的</h2></div>
        <button class="icon-button" aria-label="设置">${icon("settings", 20)}</button>
      </header>
      <section class="card profile-hero">
        <div class="profile-head">
          <span class="avatar-wrap"><img src="${avatar}" alt="岸岸头像" /></span>
          <div class="profile-head__copy"><b>岸岸学员</b><span>行测申论一起进步 · 已学习 23 天</span></div>
          <span class="level-badge">Lv.6</span>
        </div>
        <div class="profile-stats"><div><b>23</b><small>连续学习</small></div><div><b>128h</b><small>累计学习</small></div><div><b>2386</b><small>做题总数</small></div><div><b>18</b><small>模考次数</small></div></div>
      </section>
      <section class="card profile-list">
        ${listRow("report", "学习档案", "记录你的学习轨迹、能力证据与成长")}
        ${listRow("spark", "问岸岸", "带着当前任务与证据继续追问")}
        ${listRow("crown", "会员与保障", "创始体验官 · 还剩 26 天")}
        ${listRow("lock", "数据与隐私", "数据使用说明、导出与删除")}
        ${listRow("headset", "帮助与客服", "订单、功能与学习问题")}
      </section>
      <section class="card companion-quote"><span>岸岸 · 今日陪伴</span><p>你已经连续完成 4 次周复盘。稳定不是每天都满负荷，而是知道什么时候该收、什么时候该冲。</p><img src="${mascot}" alt="岸岸" /></section>
    </div>
    ${tabbar("profile")}
  </section>`;
}

const screens = {
  login: {
    code: "A01",
    name: "登录",
    note: "品牌价值、隐私承诺和微信登录在一个安静首屏完成。",
    render: loginScreen,
  },
  today: {
    code: "B01",
    name: "今日",
    note: "目标、容量、依据和首要任务在一个首屏完成交代。",
    render: todayScreen,
  },
  position: {
    code: "D01",
    name: "岗位决策",
    note: "先说能不能报，再给分数区间、证据等级与风险提醒。",
    render: positionScreen,
  },
  report: {
    code: "F13",
    name: "学习报告",
    note: "先呈现双科总趋势，再落到一条下周可执行调整。",
    render: reportScreen,
  },
  profile: {
    code: "F09",
    name: "我的",
    note: "账户、学习档案、岸岸、权益和隐私组成稳定的个人中心。",
    render: profileScreen,
  },
};

const params = new URLSearchParams(window.location.search);
const requested = params.get("screen");
let active = screens[requested] ? requested : "today";

if (params.get("capture") === "1") document.body.classList.add("is-capture");

const appScreen = document.querySelector("#app-screen");
const screenCode = document.querySelector("#screen-code");
const screenName = document.querySelector("#screen-name");
const screenNote = document.querySelector("#screen-note");

function render(screenKey, updateUrl = true) {
  active = screens[screenKey] ? screenKey : "today";
  const screen = screens[active];
  appScreen.innerHTML = screen.render();
  screenCode.textContent = screen.code;
  screenName.textContent = screen.name;
  screenNote.textContent = screen.note;
  document.querySelectorAll("[data-screen]").forEach((button) => {
    button.classList.toggle("is-active", button.dataset.screen === active);
  });
  if (updateUrl && !document.body.classList.contains("is-capture")) {
    const next = new URL(window.location.href);
    next.searchParams.set("screen", active);
    window.history.replaceState({}, "", next);
  }
}

document.addEventListener("click", (event) => {
  const switchButton = event.target.closest("[data-screen]");
  if (switchButton) render(switchButton.dataset.screen);

  const navButton = event.target.closest("[data-nav]");
  if (!navButton) return;
  const target = navButton.dataset.nav;
  if (target === "learn") render("report");
  else if (screens[target]) render(target);
});

render(active, false);
