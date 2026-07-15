const architectureScreens = [
  [
    "C09",
    "共享文字材料题",
    "题目优先态",
    phone(
      divQuestionHeader("篇章 2 / 5", "02:18") +
        '<div class="material-peek"><div><span>共享材料 · 4 段</span><b>当前定位：第 2 段</b></div><button>查看材料 ↑</button></div>' +
        '<div class="question-stem" style="font-size:14px">根据材料，数字政府建设过程中最需要优先解决的问题是：</div>' +
        option("A", "基层设备采购不足") +
        option("B", "部门数据标准与协同机制不统一", "selected") +
        option("C", "群众使用数字平台意愿不高") +
        option("D", "线下服务网点数量不足") +
        '<div class="tool-rail"><span>划重点</span><span>排除</span><span>草稿</span><span>标记</span></div><div class="answer-actions"><button class="primary">提交答案</button></div>',
      false,
    ),
  ],
  [
    "C10",
    "资料分析材料",
    "表格抽屉态",
    phone(
      divQuestionHeader("资料 4 / 5", "01:36") +
        '<div class="material-sheet"><div class="sheet-handle"></div><header><div><small>材料 1 · 2019—2023 年数据</small><b>全国软件业务收入</b></div><span>全屏 ↗</span></header><div class="table-scroll"><table class="data-table"><tr><th>年份</th><th>收入/亿元</th><th>同比</th></tr><tr><td>2020</td><td>81,616</td><td>13.3%</td></tr><tr class="focus"><td>2021</td><td>94,994</td><td>17.7%</td></tr><tr><td>2022</td><td>108,126</td><td>11.2%</td></tr><tr><td>2023</td><td>123,258</td><td>13.4%</td></tr></table></div><div class="material-tools"><span>−</span><b>100%</b><span>＋</span><span>冻结表头</span><span>行列聚焦</span></div></div><div class="question-under-sheet"><b>2021 年收入约比 2020 年增加多少？</b><span>材料抽屉可降至 28%</span></div>',
      false,
    ),
  ],
  [
    "C11",
    "图形推理",
    "图片工作区",
    phone(
      divQuestionHeader("判断 12 / 35", "00:54") +
        '<div class="image-stage"><div class="pattern-grid"><i></i><i></i><i></i><i></i><i></i><b>?</b></div><div class="zoom-bar"><span>−</span><b>适应屏幕</b><span>＋</span><span>高对比</span></div></div><p class="screen-copy">选择最符合图形变化规律的一项。</p><div class="image-options"><span>A</span><span class="selected">B</span><span>C</span><span>D</span></div><div class="tool-rail"><span>画线</span><span>排除</span><span>草稿</span><span>争议</span></div><div class="answer-actions"><button class="primary">提交答案</button></div>',
      false,
    ),
  ],
  [
    "C12",
    "做题工具抽屉",
    "模式约束",
    phone(
      divQuestionHeader("资料 3 / 10", "01:42") +
        '<div class="question-stem" style="font-size:14px">工具只帮助记录思考，不在作答前泄露方法。</div><div class="tool-drawer"><div class="sheet-handle"></div><header><b>做题工具</b><small>专项训练</small></header><div class="tool-grid"><div><i>✎</i><b>草稿板</b><small>已自动保存</small></div><div><i>⌫</i><b>排除选项</b><small>记录排除轨迹</small></div><div><i>Aa</i><b>字号行距</b><small>跟随阅读偏好</small></div><div><i>◐</i><b>护眼模式</b><small>图表保持可辨</small></div><div><i>⚑</i><b>标记本题</b><small>稍后回看</small></div><div class="disabled"><i>ƒx</i><b>公式卡</b><small>提交后开放</small></div></div><div class="rule"></div><p>国考不允许使用计算器，因此所有模式均不提供。</p></div>',
      false,
    ),
  ],
  [
    "C13",
    "答题卡",
    "完整轨迹",
    phone(
      topbar("专项答题卡", "7 / 10") +
        '<div class="answer-legend"><span><i class="done"></i>已答</span><span><i class="marked"></i>标记</span><span><i class="skipped"></i>跳过</span></div><div class="answer-sheet-grid"><span class="done">1</span><span class="done">2</span><span class="done">3</span><span class="done">4</span><span class="done">5</span><span class="done">6</span><span class="marked">7</span><span class="skipped">8</span><span>9</span><span>10</span></div><div class="task-list"><div class="task-row"><span class="task-index">8</span><div><b>主动跳过</b><p>复杂排列组合 · 已用 42 秒</p></div><time>策略正确</time></div><div class="task-row"><span class="task-index">7</span><div><b>已标记争议</b><p>材料口径可能不完整</p></div><time>待回看</time></div></div><div class="answer-actions"><button class="primary">返回第 7 题</button></div>',
      false,
    ),
  ],
  [
    "C14",
    "题目异常与争议",
    "不污染画像",
    phone(
      topbar("标记题目问题", "第 7 题") +
        '<p class="screen-copy">标记后仍可继续作答。确认存在问题的题目不会进入能力与错因统计。</p><div class="issue-list"><span class="active">材料缺失或模糊</span><span>答案存在争议</span><span>题干条件不完整</span><span>解析与题目不一致</span><span>其他问题</span></div><div class="quote-input">表格脚注中的统计范围没有显示，无法确认“全国”是否包含港澳台。</div><div class="form-block"><div class="form-value"><span>＋ 上传截图或圈选位置</span><span>0 / 3</span></div></div><div class="button-row" style="margin-top:20px"><button class="secondary">取消</button><button class="primary">提交并继续</button></div>',
      false,
    ),
  ],
  [
    "F10",
    "申论材料阅读",
    "目录 + 标注",
    phone(
      topbar("材料阅读", "3 / 6") +
        '<div class="essay-requirement"><span>归纳概括 · 200 字内</span><b>概括基层数字治理存在的主要问题</b></div><div class="material-tabs"><span>材料 1 ✓</span><span>材料 2 ✓</span><span class="active">材料 3</span><span>材料 4</span></div><div class="essay-material"><small>材料 3 · 428 字</small><p>某区上线统一政务平台后，基层工作人员仍需在多个业务系统中重复填报。<mark>不同部门的数据口径并不一致</mark>，同一事项常出现多套统计结果。</p><p>一位社区干部表示：“系统多了，但真正能共享的数据没有增加。”</p></div><div class="annotation-bar"><span>问题</span><span>原因</span><span>影响</span><span>对策</span><b>加入我的找点</b></div>',
      false,
    ),
  ],
  [
    "F11",
    "申论作答工作台",
    "提纲 + 正文",
    phone(
      topbar("归纳概括作答", "18:42") +
        '<div class="segmented"><span>材料</span><span class="active">我的提纲</span><span>正文</span></div><div class="outline-list"><div><span>01</span><p><b>数据标准不统一</b><small>材料 3 · 高亮 1</small></p></div><div><span>02</span><p><b>系统重复建设，基层重复填报</b><small>材料 1、3 · 高亮 3</small></p></div><div><span>03</span><p><b>服务适老化不足</b><small>材料 5 · 高亮 2</small></p></div></div><button class="secondary">＋ 从材料标注加入找点</button><div class="essay-editor"><header><b>正文</b><span>86 / 200 字 · 已保存</span></header><p>一是数据标准不统一，部门统计口径各异，造成数据冲突；二是平台重复建设……</p></div><div class="answer-actions"><button class="primary">提交前自检</button></div>',
      false,
    ),
  ],
  [
    "F12",
    "申论评阅报告",
    "图表 + 原句证据",
    phone(
      topbar("申论 AI 评阅", "中置信") +
        '<div class="score-band"><div><small>本题得分区间</small><b>14–17</b><span>/ 20</span></div><p>同类新材料验证前，不提高长期估分。</p></div><div class="report-bars"><div><span>要点覆盖</span><i><b style="width:78%"></b></i><strong>4 / 5</strong></div><div><span>归纳层级</span><i><b style="width:58%"></b></i><strong>3 / 5</strong></div><div><span>表达精度</span><i><b style="width:68%"></b></i><strong>3 / 5</strong></div><div><span>要求遵守</span><i><b style="width:90%"></b></i><strong>5 / 5</strong></div></div><div class="evidence-card"><span>最高价值问题</span><b>现象写到了，原因没有上升</b><p>你的原句：“多个系统需要重复填报。”</p><p>材料证据：“不同部门的数据口径并不一致。”</p></div><button class="primary">针对这一处重写</button>',
      false,
    ),
  ],
  [
    "F13",
    "模考综合报告",
    "趋势 + 热力",
    phone(
      topbar("标准卷 FORM 03", "高置信") +
        '<div class="report-summary"><div><span>行测估分</span><b>63–67</b><small>较上次 +1～3</small></div><svg viewBox="0 0 180 72"><path d="M4 57C31 52 42 58 66 43C91 28 108 39 131 21C150 8 164 15 176 8" fill="none" stroke="#5268ff" stroke-width="4" stroke-linecap="round"/><path d="M4 66C31 60 42 66 66 50C91 35 108 48 131 28C150 15 164 22 176 15V31C164 38 150 31 131 44C108 64 91 51 66 66C42 72 31 68 4 72Z" fill="#5268ff" fill-opacity=".12"/></svg></div><div class="quadrant"><span class="q-title">速度 × 正确率</span><div class="q-grid"><i style="left:70%;top:24%">资料</i><i style="left:54%;top:38%">判断</i><i style="left:40%;top:56%">言语</i><i class="risk" style="left:72%;top:76%">数量</i></div><small>越靠右用时越长，越靠上正确率越高</small></div><div class="heat-row"><span>错因热力</span><b>条件遗漏</b><i></i><i class="hot"></i><i></i><i class="hot"></i><i></i></div>' +
        anNote("资料速度改善最明显。数量仍处于低收益区，下周继续降权。"),
      false,
    ),
  ],
  [
    "E06",
    "岸岸角色系统",
    "六种状态",
    phone(
      topbar("认识岸岸", "陪伴设置") +
        '<div class="mascot-hero"><img src="./assets/anan-otter.svg" alt="岸岸，一只蓝紫色的水獭备考伙伴"><div><span>你的 AI 备考搭子</span><h3>岸岸</h3><p>记得你的目标和错因，也知道什么时候该休息、该放弃低收益难题。</p></div></div><div class="mood-grid"><div><span>陪伴</span><b>今天先做最值得的</b></div><div><span>思考</span><b>正在整理证据</b></div><div><span>提醒</span><b>这题已经超时</b></div><div><span>鼓励</span><b>验证通过，但还需复测</b></div><div><span>休息</span><b>今天恢复，不积债</b></div><div><span>劝退</span><b>这类题暂时不值得练</b></div></div><p class="screen-copy" style="margin-top:14px">做题首屏不出现大形象；岸岸只在需要判断、复盘或情绪支持时进入。</p>',
      false,
    ),
  ],
];

document
  .getElementById("section-nav")
  .insertAdjacentHTML("beforeend", '<a href="#section-G">G · 复杂题型与体验架构</a>');

document
  .getElementById("design-board")
  .insertAdjacentHTML(
    "beforeend",
    '<section class="design-section" id="section-G"><header class="section-head"><span class="section-code">G</span><h2>复杂题型与体验架构</h2><p>把材料阅读、答题工具、申论工作台、报告图表和岸岸角色从边缘情况提升为正式系统能力。</p></header><div class="screen-grid">' +
      architectureScreens
        .map(
          ([id, name, meta, markup]) =>
            '<article class="screen-item"><div class="screen-label"><b>' +
            id +
            "</b><span>" +
            name +
            "</span><small>" +
            meta +
            "</small></div>" +
            markup +
            "</article>",
        )
        .join("") +
      "</div></section>",
  );

const architectureParams = new URLSearchParams(window.location.search);

if (architectureParams.get("focus") === "architecture") {
  document.body.classList.add("focus-architecture");
  document
    .querySelectorAll("#design-board > section:not(#section-G)")
    .forEach((section) => section.remove());
}

const focusedScreenId = architectureParams.get("screen");
if (focusedScreenId) {
  document.body.classList.add("focus-screen");
  document.querySelectorAll("#section-G .screen-item").forEach((item) => {
    if (item.querySelector(".screen-label b")?.textContent !== focusedScreenId) item.remove();
  });
}
