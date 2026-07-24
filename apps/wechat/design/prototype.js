const nav = `
  <div class="bottom-nav">
    <span class="active"><i></i>今日</span><span><i></i>训练</span><span><i></i>岗位</span><span><i></i>我的</span>
  </div>`;

const anNote = (text) =>
  `<div class="an-note"><div class="an-orbit"><span></span></div><div><b>岸岸判断</b><p>${text}</p></div></div>`;
const status = `<div class="status"><span>9:41</span><span>●●● ◒</span></div>`;
const topbar = (title, meta = "") =>
  `<div class="topbar"><span class="back">‹</span><b>${title}</b><small>${meta}</small></div>`;
const task = (n, title, copy, time) =>
  `<div class="task-row"><span class="task-index">${n}</span><div><b>${title}</b><p>${copy}</p></div><time>${time}</time></div>`;
const option = (key, text, state = "") =>
  `<div class="option ${state}"><span>${key}</span>${text}</div>`;
const phone = (body, hasNav = true) =>
  `<div class="phone"><div class="phone-inner ${hasNav ? "" : "no-nav"}">${status}${body}</div>${hasNav ? nav : ""}</div>`;

const sections = [
  {
    code: "A",
    title: "身份与建档",
    desc: "不用一页一问的问卷感。用完成路径、条件摘要和实时影响让用户知道为什么要填。",
    screens: [
      [
        "A01",
        "登录/价值主张",
        "入口",
        phone(
          `<div style="padding-top:36px"><div class="brand-orbit" style="width:54px;height:54px"><span></span></div><div class="kicker" style="margin-top:26px">AI 国考上岸教练</div><h2 class="screen-title" style="font-size:34px">不催你多刷题，<br>只帮你缩小分差。</h2><p class="screen-copy">知道你能报什么岗、现在多少分、为什么错，以及今天最该做什么。</p><div class="rule"></div><div class="form-block"><div class="form-value"><span>岗位资格与学习画像分开管理</span><span>✓</span></div></div><div class="form-block"><div class="form-value"><span>AI 结论必须有证据</span><span>✓</span></div></div><button class="primary" style="margin-top:34px">微信登录并开始</button><p class="screen-copy" style="text-align:center;margin-top:12px">可随时导出或删除个人档案</p></div>`,
          false,
        ),
      ],
      [
        "A02",
        "资格建档",
        "硬条件",
        phone(
          `${topbar("建立报考资格", "2 / 6")}<div class="progress"><i style="width:34%"></i></div><h2 class="screen-title" style="margin-top:28px">先确定你能报什么</h2><p class="screen-copy">只收集岗位筛选所必需的信息。</p><div class="form-block"><label>学历 / 学位</label><div class="form-value"><span>本科 · 学士</span><span>›</span></div></div><div class="form-block"><label>专业</label><div class="form-value"><span>计算机科学与技术</span><span>›</span></div></div><div class="form-block"><label>应届身份</label><div class="segmented"><span class="active">应届</span><span>非应届</span><span>不确定</span></div></div><div class="ai-candidate"><b>实时影响</b><p>已筛出 216 个候选岗位，其中 38 个条件待确认。</p></div><button class="primary">继续完善条件</button>`,
          false,
        ),
      ],
      [
        "A03",
        "目标选择",
        "双入口",
        phone(
          `${topbar("你准备报哪里？", "3 / 6")}<h2 class="screen-title">有目标就直接找，<br>没目标就交给系统筛。</h2><div class="ai-candidate" style="background:white"><b>我有明确目标</b><p>搜索部门、职位代码或岗位名称</p><div class="form-value" style="margin-top:12px"><span>🔎 输入目标岗位</span><span>›</span></div></div><div class="ai-candidate"><b>还不确定</b><p>基于已填资格生成冲、稳、保目标组合。</p><button class="primary" style="margin-top:12px">看我能报什么</button></div>${anNote("目标不是一次选定。岗位发布、资格或成绩变化时，我会重新评估。")}`,
          false,
        ),
      ],
      [
        "A04",
        "成绩与证据",
        "导入",
        phone(
          `${topbar("你现在大概多少分", "4 / 6")}<div class="segmented"><span class="active">导入成绩</span><span>快速短测</span><span>稍后再说</span></div><div class="hero-panel" style="margin-top:18px"><small>最近一次可靠成绩</small><h3>124.5</h3><p>粉笔模考 · 2026.07.06 · 限时完成</p></div><div class="form-block"><label>报告截图</label><div class="form-value"><span>已识别 5 个模块</span><span class="text-link">查看原图</span></div></div><div class="bar-list"><div class="bar-row"><span>资料分析</span><div class="bar"><i style="width:58%"></i></div><b>58%</b></div><div class="bar-row"><span>判断推理</span><div class="bar"><i style="width:72%"></i></div><b>72%</b></div><div class="bar-row"><span>言语理解</span><div class="bar"><i style="width:66%"></i></div><b>66%</b></div></div><div class="ai-candidate"><b>待你确认</b><p>AI 识别结果不会自动写入学习档案。</p></div><button class="primary">确认这份成绩</button>`,
          false,
        ),
      ],
      [
        "A05",
        "时间预算",
        "周节奏",
        phone(
          `${topbar("你真正能学多久", "5 / 6")}<h2 class="screen-title">计划要尊重你的时间，<br>不是把你压垮。</h2><div class="form-block"><label>工作日晚上</label><div class="form-value"><span>45 分钟</span><span>− · +</span></div></div><div class="form-block"><label>周末整块时间</label><div class="form-value"><span>120 分钟</span><span>− · +</span></div></div><div class="form-block"><label>常规休息日</label><div class="chip-row"><span class="chip">一</span><span class="chip">二</span><span class="chip">三</span><span class="chip">四</span><span class="chip">五</span><span class="chip">六</span><span class="chip mint">日</span></div></div><div class="data-strip"><div><b>5</b><small>针对训练</small></div><div><b>1</b><small>整卷模考</small></div><div><b>1</b><small>恢复复盘</small></div></div><button class="primary">生成我的第一周</button>`,
          false,
        ),
      ],
      [
        "A06",
        "建档结果",
        "起点",
        phone(
          `${topbar("你的备考起点", "已建档")}<div class="hero-panel"><small>目标岗位基准分差</small><h3>11 分</h3><div class="gap-line"><span>121–125</span><strong>→</strong><span>132–136</span></div><p>当前是中等置信估计，会随作答和模考持续更新。</p></div>${anNote("资料分析是当前收益最高的模块。先用 14 天验证是否能稳定提升。")}<div class="data-strip"><div><b>94</b><small>已有证据</small></div><div><b>45m</b><small>日均预算</small></div><div><b>14d</b><small>首次验证</small></div></div><button class="primary">开始今日第一任务</button>`,
          false,
        ),
      ],
    ],
  },
  {
    code: "B",
    title: "今日与规划",
    desc: "首页不是功能入口集合，而是规划 Agent 的最终决策。终点是一键开始，不是继续浏览。",
    screens: [
      [
        "B01",
        "今日总览",
        "普通日",
        phone(
          `<div class="today-overview"><div class="kicker">距 2027 国考笔试 132 天</div><h2 class="screen-title">今天有 45 分钟，<br>先拿下最稳的 1 分。</h2><div class="hero-panel"><small>主目标 · 中央机关综合管理岗</small><h3>差 11 分</h3><p>121–125 → 建议备考线 132–136</p></div>${anNote("先练资料分析的基期量。昨天复盘的言语错因已到首次复测时间。")}<div class="task-list">${task("1", "资料分析 · 基期量", "10 题，正确率目标 80%", "20m")}${task("2", "言语中心理解复测", "3 道变式题 + 判断依据", "10m")}${task("3", "复盘两道高价值错题", "一题超时，一题蒙对", "15m")}</div><button class="primary">开始第 1 项</button></div>`,
        ),
      ],
      [
        "B02",
        "计划依据",
        "可解释",
        phone(
          `${topbar("今天为什么这样排", "PLAN 018")}<div class="timeline"><div class="timeline-item"><b>目标岗位</b><p>基准分差 11 分，资料分析可训练收益最高。</p></div><div class="timeline-item"><b>新作答证据</b><p>近 30 题资料正确率 58%，平均 104 秒/题。</p></div><div class="timeline-item"><b>到期掌握验证</b><p>言语“中心与意图混淆”已到 1–3 天复测窗口。</p></div><div class="timeline-item"><b>当日容量</b><p>45 分钟，精力 3/5，未超过近 7 日可完成阈值。</p></div></div><div class="ai-candidate"><b>算法版本</b><p>planner-v1.3 · 生成于 08:00 · 5 条原始证据</p></div><button class="secondary">查看全部证据</button>`,
          false,
        ),
      ],
      [
        "B03",
        "当日调整",
        "重排",
        phone(
          `${topbar("今天实际能学多久", "重排")}<h2 class="screen-title">计划不积债，<br>只重新找最高收益。</h2><div class="data-strip"><div><b>10m</b><small>最少</small></div><div style="background:var(--blue-soft)"><b>30m</b><small>当前</small></div><div><b>60m</b><small>最多</small></div></div><div class="form-block"><label>当前精力</label><div class="segmented"><span>很累</span><span class="active">一般</span><span>很好</span></div></div><div class="result-banner"><b>重排后保留 2 项</b><p>15 分钟资料专项 + 10 分钟到期复测，剩余 5 分钟留给复盘。</p></div><button class="primary" style="margin-top:22px">确认新计划</button><button class="secondary" style="margin-top:8px">今天完全休息</button>`,
          false,
        ),
      ],
      [
        "B04",
        "每日结算",
        "闭环",
        phone(
          `${topbar("今天已收官", "42 分钟")}<div class="hero-panel"><small>有效学习闭环</small><h3>2 / 3</h3><p>针对训练完成，复测通过，一条错因待延时验证。</p></div><div class="data-strip"><div><b>80%</b><small>资料正确</small></div><div><b>-14s</b><small>平均用时</small></div><div><b>1</b><small>新错因</small></div></div>${anNote("今天的资料训练达标。但“基期量分母忘加 1”还只是初步掌握，3 天后再验证。")}<div class="timeline"><div class="timeline-item"><b>明天计划已预留</b><p>45 分钟 · 判断推理 + 错题复测</p></div></div><button class="primary">结束今天</button>`,
        ),
      ],
      [
        "B05",
        "周计划",
        "5+1+1",
        phone(
          `${topbar("本周节奏", "7.13–7.19")}<div class="hero-panel"><small>本周主目标</small><h3 style="font-size:23px">资料 + 1.2 分</h3><p>以自有标准卷验证，不用普通练习正确率代替。</p></div><div class="timeline" style="margin-top:20px"><div class="timeline-item"><b>周一—周三 · 针对训练</b><p>资料基期量、比重与速算</p></div><div class="timeline-item"><b>周四—周五 · 迁移验证</b><p>混合题组 + 到期错因复测</p></div><div class="timeline-item"><b>周六 · 整卷模考</b><p>120 分钟，中途不给反馈</p></div><div class="timeline-item"><b>周日 · 恢复复盘</b><p>不安排新知识，整理下周证据</p></div></div>`,
        ),
      ],
    ],
  },
  {
    code: "C",
    title: "行测做题与掌握",
    desc: "做题页是核心工具，不延续首页的仪表盘。阅读、选择和作答轨迹比品牌元素更重要。",
    screens: [
      [
        "C01",
        "任务说明",
        "开始前",
        phone(
          `${topbar("资料分析专项", "TASK 104")}<div class="hero-panel"><small>今日第 1 项</small><h3 style="font-size:25px">10 题 · 20 分钟</h3><p>范围：基期量与比重。不追求全部做完，先稳定正确率。</p></div><div class="data-strip"><div><b>58%</b><small>近 30 题</small></div><div><b>104s</b><small>平均用时</small></div><div><b>80%</b><small>本次验收</small></div></div><div class="form-block"><label>本次特别记录</label><div class="chip-row"><span class="chip">修改答案</span><span class="chip">跳题</span><span class="chip">单题用时</span><span class="chip">信心度</span></div></div>${anNote("如果单题超过 120 秒，我会记录为时间风险，但不会中断你。")}<button class="primary">开始计时训练</button>`,
          false,
        ),
      ],
      [
        "C02",
        "读题/作答",
        "主做题页",
        phone(
          `${divQuestionHeader("3 / 10", "01:42")}<div class="chip-row"><span class="chip">资料分析</span><span class="chip amber">建议 90 秒</span></div><div class="question-stem">某地区今年完成投资 126 亿元，同比增长 12.5%。去年完成投资约为多少亿元？</div>${option("A", "108")}${option("B", "112", "selected")}${option("C", "116")}${option("D", "120")}<div class="answer-actions"><div class="button-row"><button class="secondary">不会 / 跳过</button><button class="primary">提交答案</button></div><div class="segmented"><span>蒙的</span><span class="active">不确定</span><span>确定</span></div></div>`,
          false,
        ),
      ],
      [
        "C03",
        "即时反馈",
        "错 + 超时",
        phone(
          `${divQuestionHeader("3 / 10", "03:06")}<div class="result-banner wrong"><b>答错，而且超时 96 秒</b><p>正确答案 B。这道题会进入高价值复盘。</p></div><div class="question-stem" style="font-size:14px;margin-top:18px">基期量 = 现期量 ÷（1 + 增长率）</div>${option("B", "112", "correct")}${option("D", "120", "wrong")}<div class="ai-candidate"><b>先看方法，不先看 AI 归因</b><p>126 ÷ 1.125 ≈ 112。你的真实错因要等你表达后再判断。</p></div><div class="answer-actions"><button class="primary">先说说我当时怎么想</button><button class="secondary" style="margin-top:8px">标记题目有争议</button></div>`,
          false,
        ),
      ],
      [
        "C04",
        "复盘表达",
        "用户先说",
        phone(
          `${topbar("还原当时的思路", "高价值错题")}<p class="screen-copy">请写你刚才真正的判断过程，不要只写“粗心”。</p><div class="quote-input">我看到 12.5% 就直接用 126 乘了 0.875，当时觉得“减去增长的部分”就是去年。</div><div class="chip-row"><span class="chip amber">理解偏差</span><span class="chip">方法错误</span><span class="chip">计算失误</span><span class="chip">时间压力</span></div><div class="form-block"><div class="form-value"><span>🎙 按住说出当时思路</span><span>00:00</span></div></div><button class="primary" style="margin-top:24px">提交我的思路</button><button class="secondary" style="margin-top:8px">暂存，稍后复盘</button>`,
          false,
        ),
      ],
      [
        "C05",
        "AI 候选错因",
        "确认权",
        phone(
          `${topbar("AI 帮你提炼了一条错因", "待确认")}<div class="an-note" style="border-top:0"><div class="an-orbit"><span></span></div><div><b>这不是最终结论</b><p>只有你确认后，才会写入长期学习档案。</p></div></div><div class="ai-candidate"><b>候选错因</b><p>你把“同比增长 12.5%”理解为“现期量减少 12.5%即得基期量”，没有识别现期量已包含增长后的整体。</p></div><div class="form-block"><label>下次识别信号</label><div class="form-value"><span>看到“现期 + 增长率求基期”</span></div></div><div class="form-block"><label>触发方法</label><div class="form-value"><span>先写出 A = B ×（1+r）</span></div></div><div class="button-row" style="margin-top:24px"><button class="secondary">不准确</button><button class="primary">符合我的想法</button></div>`,
          false,
        ),
      ],
      [
        "C06",
        "即时变式题",
        "初步验证",
        phone(
          `${divQuestionHeader("变式验证", "00:48")}<div class="result-banner"><b>不看原题，再验证一次</b><p>题面已经变化，验证你是否掌握方法，而不是记住答案。</p></div><div class="question-stem">某市今年一般公共预算收入 225 亿元，比上年增长 12.5%。上年收入为多少？</div>${option("A", "180")}${option("B", "195")}${option("C", "200", "selected")}${option("D", "205")}<div class="answer-actions"><button class="primary">提交并说明依据</button></div>`,
          false,
        ),
      ],
      [
        "C07",
        "整组结果",
        "达标",
        phone(
          `${topbar("资料分析专项完成", "19:24")}<div class="hero-panel"><small>本次验收</small><h3>8 / 10</h3><p>正确率 80%，刚好达标。平均用时比近 30 题缩短 14 秒。</p></div><div class="data-strip"><div><b>80%</b><small>正确率</small></div><div><b>90s</b><small>平均用时</small></div><div><b>2</b><small>高价值题</small></div></div><div class="timeline"><div class="timeline-item"><b>1 条候选错因已确认</b><p>现期与基期关系式理解偏差</p></div><div class="timeline-item"><b>1 条证据不足</b><p>比重差计算是方法问题还是计算失误，需要新作答验证</p></div></div><button class="primary">继续今日第 2 项</button>`,
          false,
        ),
      ],
      [
        "C08",
        "错题/复测队列",
        "掌握状态",
        phone(
          `${topbar("错题与掌握", "12 项")}<div class="segmented"><span>待复盘 3</span><span class="active">待复测 5</span><span>已掌握 4</span></div><div class="timeline"><div class="timeline-item"><b>今天 · 中心与意图区分</b><p>首次复测 · 3 道变式题 · 中置信</p></div><div class="timeline-item"><b>明天 · 基期量关系式</b><p>1–3 天复测 · 来自今日错题</p></div><div class="timeline-item"><b>7 月 19 日 · 比重差</b><p>7–14 天延时验证 · 暂时掌握</p></div></div><div class="result-banner"><b>掌握不是打钩</b><p>只有即时变式、首次复测和延时验证均通过，才标记为稳定掌握。</p></div>`,
        ),
      ],
    ],
  },
  {
    code: "D",
    title: "岗位决策",
    desc: "岗位页不做商品列表。用资格确定性、目标分证据和备考成本完成真实决策。",
    screens: [
      [
        "D01",
        "岗位工作区",
        "主目标",
        phone(
          `<div class="kicker">岗位 AGENT · 2027 国考</div><h2 class="screen-title">你的目标组合</h2><div class="hero-panel"><small>主目标 · 条件待确认 1 项</small><h3 style="font-size:20px">中央机关综合管理岗</h3><div class="gap-line"><span>132–136 分</span><strong>差 11</strong></div><p>岗位样本 2 个 + 同系统类似岗 6 个</p></div><div class="position-row"><header><span class="bucket safe">保</span><h4>县级税务行政执法岗</h4><span class="chip mint">资格确认</span></header><p>税务系统 · 江西</p><div class="position-score"><b>124–128</b><strong>差 3</strong></div></div><div class="position-row"><header><span class="bucket stretch">冲</span><h4>中央机关一级主任科员岗</h4></header><p>综合管理系统 · 北京</p><div class="position-score"><b>138–142</b><strong>差 17</strong></div></div>`,
        ),
      ],
      [
        "D02",
        "搜索/筛选",
        "资格约束",
        phone(
          `${topbar("查找可报岗位", "216 个")}<div class="form-block" style="padding-top:0"><div class="form-value"><span>🔎 搜部门、地区或岗位代码</span></div></div><div class="chip-row"><span class="chip">本科</span><span class="chip">计算机类</span><span class="chip">应届</span><span class="chip amber">1 项待确认</span></div><div class="data-strip"><div><b>216</b><small>条件匹配</small></div><div><b>38</b><small>待确认</small></div><div><b>12</b><small>高适配</small></div></div>${[1, 2, 3, 4].map((n) => `<div class="position-row"><header><h4>${["海关监管岗", "税务行政执法岗", "中央机关综合管理岗", "银保监管岗"][n - 1]}</h4><span class="chip ${n === 3 ? "amber" : "mint"}">${n === 3 ? "待确认" : "可报"}</span></header><p>招 1–3 人 · 本科及以上</p></div>`).join("")}`,
        ),
      ],
      [
        "D03",
        "岗位详情",
        "证据和风险",
        phone(
          `${topbar("岗位详情", "职位 10031")}<h2 class="screen-title" style="font-size:22px">中央机关综合管理岗</h2><p class="screen-copy">目标单位 · 北京 · 招录 2 人</p><div class="hero-panel"><small>建议备考线</small><h3>132–136</h3><p>中置信 · 样本 8 个 · 最新 2026</p></div><div class="form-block"><label>资格匹配</label><div class="chip-row"><span class="chip mint">学历符合</span><span class="chip mint">专业符合</span><span class="chip amber">基层经历待确认</span></div></div><div class="form-block"><label>主要风险</label><div class="form-value"><span>招录人数少，分数波动较大</span></div></div><div class="form-block"><label>对每日计划的影响</label><div class="form-value"><span>需优先缩小资料与申论共 7 分</span></div></div><button class="primary" style="margin-top:18px">设为主目标</button>`,
          false,
        ),
      ],
      [
        "D04",
        "岗位对比",
        "决策表",
        phone(
          `${topbar("对比 3 个岗位", "冲稳保")}<table class="compare-table"><tr><th></th><th>保</th><th>稳</th><th>冲</th></tr><tr><th>备考线</th><td>124–128</td><td>132–136</td><td>138–142</td></tr><tr><th>当前分差</th><td>3</td><td>11</td><td>17</td></tr><tr><th>资格</th><td>已确认</td><td>待确认</td><td>已确认</td></tr><tr><th>样本证据</th><td>高</td><td>中</td><td>中</td></tr><tr><th>招录人数</th><td>3</td><td>2</td><td>1</td></tr><tr><th>额外考试</th><td>无</td><td>无</td><td>有</td></tr></table>${anNote("稳妥岗适合作为主目标，但必须先确认基层工作经历。保底岗的限制条件更强，建议保留。")}<button class="primary">确认目标组合</button>`,
          false,
        ),
      ],
      [
        "D05",
        "目标变更确认",
        "影响说明",
        phone(
          `${topbar("更换主目标", "重要变更")}<div class="result-banner wrong"><b>这会重算你的学习计划</b><p>目标分从 132–136 调整为 138–142，基准分差增加 6 分。</p></div><div class="timeline" style="margin-top:22px"><div class="timeline-item"><b>申论权重提高</b><p>每周增加 1 次申论小题重写</p></div><div class="timeline-item"><b>数量关系仍降权</b><p>高难排列组合的预期收益仍然较低</p></div><div class="timeline-item"><b>当日任务不立即中断</b><p>新计划从明天生效</p></div></div><div class="button-row"><button class="secondary">保留原目标</button><button class="primary">确认更换</button></div>`,
          false,
        ),
      ],
    ],
  },
  {
    code: "E",
    title: "学习档案与 AI",
    desc: "档案页展示可信度、反证和变化，不只展示统计。岸岸对话始终带当前上下文和引用。",
    screens: [
      [
        "E01",
        "学习档案",
        "全局",
        phone(
          `<div class="kicker">共享学习大脑</div><h2 class="screen-title">这些是系统对你的当前理解</h2><div class="data-strip"><div><b>61–65</b><small>行测估分</small></div><div><b>60–64</b><small>申论估分</small></div><div><b>94</b><small>有效证据</small></div></div><div class="chart"><svg viewBox="0 0 280 120"><polyline points="0,92 45,85 90,88 135,66 180,71 225,44 280,34" fill="none" stroke="#4057e3" stroke-width="3"/><circle cx="280" cy="34" r="5" fill="#63e6be"/></svg></div><div class="bar-list"><div class="bar-row"><span>资料分析</span><div class="bar"><i style="width:58%"></i></div><b>58%</b></div><div class="bar-row"><span>判断推理</span><div class="bar"><i style="width:72%"></i></div><b>72%</b></div><div class="bar-row"><span>言语理解</span><div class="bar"><i style="width:66%"></i></div><b>66%</b></div><div class="bar-row"><span>数量关系</span><div class="bar"><i style="width:31%"></i></div><b>31%</b></div></div><button class="secondary">查看结论与证据</button>`,
        ),
      ],
      [
        "E02",
        "模块详情",
        "资料分析",
        phone(
          `${topbar("资料分析", "中置信")}<div class="hero-panel"><small>预计得分空间</small><h3>+2.4 分</h3><p>按当前每周 120 分钟投入估计，有效期 14 天。</p></div><div class="data-strip"><div><b>58%</b><small>正确率</small></div><div><b>104s</b><small>单题用时</small></div><div><b>0.62</b><small>稳定性</small></div></div><div class="section-title" style="font-size:13px;margin:18px 0 6px">主要失分结构</div><div class="bar-list"><div class="bar-row"><span>基期量</span><div class="bar"><i style="width:74%"></i></div><b>7 次</b></div><div class="bar-row"><span>比重差</span><div class="bar"><i style="width:52%"></i></div><b>5 次</b></div><div class="bar-row"><span>速算</span><div class="bar"><i style="width:38%"></i></div><b>3 次</b></div></div>${anNote("暂时不建议扩大难题范围。先把基期量和比重差做到稳定掌握。")}`,
        ),
      ],
      [
        "E03",
        "错误模式详情",
        "可反证",
        phone(
          `${topbar("条件定位不完整", "高置信")}<div class="data-strip"><div><b>7</b><small>支持证据</small></div><div><b>2</b><small>反证</small></div><div><b>21d</b><small>观测窗口</small></div></div><div class="timeline"><div class="timeline-item"><b>7.11 · 判断推理</b><p>忽略“除了…之外”的排除条件，用户已确认。</p></div><div class="timeline-item"><b>7.08 · 资料分析</b><p>未区分“占全部”和“占增量”，变式题未通过。</p></div><div class="timeline-item"><b>7.05 · 言语理解 · 反证</b><p>虽然题干长，但关键否定词定位正确。</p></div></div><div class="ai-candidate"><b>当前结论</b><p>该模式在跨模块中重复出现，但存在 2 条反证，不应扩大为“审题差”的泛化标签。</p></div>`,
        ),
      ],
      [
        "E04",
        "岸岸对话",
        "有上下文",
        phone(
          `${topbar("岸岸", "今日额度 8 / 10")}<div class="an-note" style="border-top:0"><div class="an-orbit"><span></span></div><div><b>当前上下文</b><p>资料分析专项 · 基期量错题</p></div></div><div class="chat"><div class="bubble user">为什么不能直接用现期量乘（1-增长率）？</div><div class="bubble ai">因为增长率的基数是上期量，不是现期量。关系式是 A = B ×（1+r），所以 B = A ÷（1+r）。<div class="citation">依据 1 · 基期量方法卡</div></div><div class="bubble ai">你刚才的错因不是算错，而是把增长部分错放在了现期量上。下一道变式题会专门验证这一点。<div class="citation">依据 2 · 你的原始复盘</div></div></div><div class="chat-input"><span>继续问这道题…</span><b>↑</b></div>`,
          false,
        ),
      ],
      [
        "E05",
        "计划变更记录",
        "决策审计",
        phone(
          `${topbar("计划为什么变了", "近 30 天")}<div class="timeline"><div class="timeline-item"><b>今天 08:00 · 增加言语复测</b><p>错因“中心与意图混淆”进入首次复测窗口。</p></div><div class="timeline-item"><b>昨天 21:42 · 下调数量关系</b><p>近 4 次复杂排列组合平均 190 秒，正确率 25%，当前收益低于资料。</p></div><div class="timeline-item"><b>7.10 · 目标分区间收窄</b><p>新增 1 次外部模考，行测估分从 59–65 调整为 61–65。</p></div><div class="timeline-item"><b>7.08 · 周六改为模考日</b><p>用户修改周时间预算。</p></div></div><button class="secondary">导出决策与证据</button>`,
          false,
        ),
      ],
    ],
  },
  {
    code: "F",
    title: "模考、申论与商业",
    desc: "高投入页面使用应用级工具界面，商业页则明确呈现权益和额度，不用“无限 AI”诱导。",
    screens: [
      [
        "F01",
        "模考中心",
        "自有 + 外部",
        phone(
          `<div class="kicker">周六 · 整卷模考日</div><h2 class="screen-title">用一把稳定的尺，<br>重新校准能力。</h2><div class="hero-panel"><small>自有标准卷 · FORM 03</small><h3 style="font-size:24px">135 题 · 120 分钟</h3><p>固定难度与知识覆盖，用于与自己的历史表现对比。</p></div><button class="primary" style="margin-top:16px">开始自有标准卷</button><div class="rule"></div><div class="form-block"><label>已在其他平台完成模考？</label><div class="form-value"><span>导入外部模考报告</span><span>›</span></div></div>${anNote("同一周不会强迫你做两套整卷。外部报告用于趋势参考，自有卷用于内部标尺。")}`,
        ),
      ],
      [
        "F02",
        "整卷作答",
        "专注模式",
        phone(
          `${divQuestionHeader("言语 21 / 40", "76:18")}<div class="progress"><i style="width:46%"></i></div><div class="question-stem" style="font-size:14px">在整卷模式中，界面不显示对错、分差、AI 解析或激励信息。用户只与题目、时间和答题卡交互。</div>${option("A", "答案选项示例")}${option("B", "答案选项示例", "selected")}${option("C", "答案选项示例")}${option("D", "答案选项示例")}<div class="answer-actions"><div class="button-row"><button class="secondary">答题卡</button><button class="primary">下一题</button></div><div class="form-value" style="margin-top:10px;color:var(--muted);font-size:9px"><span>标记本题</span><span>不会 / 跳过</span></div></div>`,
          false,
        ),
      ],
      [
        "F03",
        "模考报告",
        "区间 + 轨迹",
        phone(
          `${topbar("标准卷 FORM 03", "已校准")}<div class="hero-panel"><small>行测估分区间</small><h3>63–67</h3><p>比上次标准卷提高 1–3 分，置信度高。</p></div><div class="chart"><svg viewBox="0 0 280 120"><polyline points="0,94 56,84 112,87 168,62 224,52 280,40" fill="none" stroke="#4057e3" stroke-width="3"/><circle cx="280" cy="40" r="5" fill="#63e6be"/></svg></div><div class="data-strip"><div><b>71%</b><small>正确率</small></div><div><b>14</b><small>超时题</small></div><div><b>9</b><small>高价值题</small></div></div>${anNote("本次提分主要来自资料速度，言语的中心理解出现回退。下周不扩大数量难题。")}`,
        ),
      ],
      [
        "F04",
        "外部报告确认",
        "OCR 后确认",
        phone(
          `${topbar("确认外部模考", "待入档")}<div class="result-banner"><b>已识别报告</b><p>请核对场次、是否限时、总分和模块成绩。</p></div><div class="form-block"><label>平台 / 场次</label><div class="form-value"><span>粉笔 · 7 月国考模考</span><span>修改</span></div></div><div class="form-block"><label>考试条件</label><div class="form-value"><span>限时 120 分钟 · 完整作答</span><span>修改</span></div></div><div class="form-block"><label>总分</label><div class="form-value"><span>124.5</span><span>修改</span></div></div><div class="bar-list"><div class="bar-row"><span>资料</span><div class="bar"><i style="width:58%"></i></div><b>58%</b></div><div class="bar-row"><span>判断</span><div class="bar"><i style="width:72%"></i></div><b>72%</b></div><div class="bar-row"><span>言语</span><div class="bar"><i style="width:66%"></i></div><b>66%</b></div></div><button class="primary">确认后写入学习档案</button>`,
          false,
        ),
      ],
      [
        "F05",
        "申论任务",
        "阅读 + 作答",
        phone(
          `${topbar("申论归纳概括", "25 分钟")}<div class="chip-row"><span class="chip">概括问题</span><span class="chip amber">200 字内</span></div><div class="question-stem" style="font-size:13px;margin-top:18px">请根据“给定资料 1–3”，概括基层数字治理中存在的主要问题。</div><div class="quote-input" style="min-height:180px">在此输入或拍照导入作答…</div><div class="form-block"><div class="form-value"><span>📷 拍照识别手写答案</span><span>›</span></div></div><div class="answer-actions"><button class="primary">提交答案与找点思路</button></div>`,
          false,
        ),
      ],
      [
        "F06",
        "申论评阅",
        "证据化",
        phone(
          `${topbar("申论 AI 评阅", "中置信")}<div class="hero-panel"><small>分数区间 · 非精确分</small><h3>14–17 / 20</h3><p>基于要点覆盖、结构、表达和字数，需要同类新材料继续验证。</p></div><div class="bar-list"><div class="bar-row"><span>要点覆盖</span><div class="bar"><i style="width:76%"></i></div><b>4/5</b></div><div class="bar-row"><span>归纳结构</span><div class="bar"><i style="width:62%"></i></div><b>3/5</b></div><div class="bar-row"><span>表达精度</span><div class="bar"><i style="width:68%"></i></div><b>3/5</b></div></div><div class="ai-candidate"><b>最高价值问题</b><p>你找到了“数据重复填报”，但没有上升为“部门数据标准不统一”，导致归纳层级偏低。</p></div><button class="primary">开始重写</button>`,
          false,
        ),
      ],
      [
        "F07",
        "申论重写",
        "前后对比",
        phone(
          `${topbar("重写并验证", "第 2 版")}<div class="segmented"><span>原稿</span><span class="active">重写</span><span>差异</span></div><div class="quote-input" style="min-height:190px;color:var(--ink)">一是数据标准不统一，不同部门口径各异，导致重复填报与数据冲突；二是……</div><div class="result-banner" style="margin-top:14px"><b>这次改好了什么</b><p>从表面现象“重复填报”上升到原因“标准不统一”，概括层级提高。</p></div><div class="result-banner wrong" style="margin-top:8px"><b>还没有证明迁移</b><p>下一次将使用新材料验证，不会因为本篇改好就提高长期估分。</p></div><button class="primary" style="margin-top:14px">提交重写</button>`,
          false,
        ),
      ],
      [
        "F08",
        "会员/额度",
        "不卖无限",
        phone(
          `${topbar("上岸教练会员", "权益透明")}<div class="price-panel"><small>国考备考周期服务</small><h3>¥199</h3><p>持续计划、深度复盘、申论评阅、模考分析和长期记忆。</p><button class="primary" style="background:var(--mint);color:var(--ink);margin-top:10px">开通备考周期服务</button></div><div class="section-title" style="font-size:13px;margin:20px 0 4px">本周剩余权益</div><div class="quota-row"><span>深度错题复盘</span><b>18 / 30</b></div><div class="quota-row"><span>申论 AI 评阅</span><b>4 / 6</b></div><div class="quota-row"><span>外部模考导入</span><b>2 / 3</b></div><div class="quota-row"><span>周度深度诊断</span><b>1 / 1</b></div><p class="screen-copy" style="margin-top:14px">达到权益边界后明确提示，不会静默更换低质量模型。</p>`,
          false,
        ),
      ],
      [
        "F09",
        "账户与隐私",
        "数据控制",
        phone(
          `${topbar("账户与数据", "微信用户")}<div class="form-block"><label>报考资格画像</label><div class="form-value"><span>已完成 86%</span><span>›</span></div></div><div class="form-block"><label>学习行为与长期画像</label><div class="form-value"><span>94 条证据 · 3 条错误模式</span><span>›</span></div></div><div class="form-block"><label>原始图片与语音</label><div class="form-value"><span>7 份 · 按保留策略到期</span><span>›</span></div></div><div class="form-block"><label>AI 画像和决策记录</label><div class="form-value"><span>可查看所有证据引用</span><span>›</span></div></div><button class="secondary" style="margin-top:24px">导出我的完整档案</button><div class="rule"></div><div class="form-value" style="color:var(--red)"><span>删除账户与全部派生数据</span><span>›</span></div><p class="screen-copy" style="margin-top:14px">删除覆盖原始表达、派生画像、AI 摘要和向量索引。</p>`,
          false,
        ),
      ],
    ],
  },
];

function divQuestionHeader(progress, time) {
  return `<div class="question-meta"><span>退出</span><b>${progress}</b><time>${time}</time></div>`;
}

document.getElementById("section-nav").innerHTML = sections
  .map((section) => `<a href="#section-${section.code}">${section.code} · ${section.title}</a>`)
  .join("");

document.getElementById("design-board").innerHTML = sections
  .map(
    (section) => `<section class="design-section" id="section-${section.code}">
      <header class="section-head"><span class="section-code">${section.code}</span><h2>${section.title}</h2><p>${section.desc}</p></header>
      <div class="screen-grid">${section.screens
        .map(
          ([id, name, meta, markup]) =>
            `<article class="screen-item"><div class="screen-label"><b>${id}</b><span>${name}</span><small>${meta}</small></div>${markup}</article>`,
        )
        .join("")}</div>
    </section>`,
  )
  .join("");
