# 考公搭子 AI 备考代理信息架构设计

Date: 2026-07-03
Status: Draft for user review

## Authority

- Product research report: `/Users/zxg/Desktop/deep-research-report.md`
- Commercial execution master plan: `docs/aegis/plans/2026-07-02-commercial-execution-master-plan.md`
- MVP bridge plan: `docs/aegis/plans/2026-07-02-mvp-bridge-plan.md`
- User correction: the product is AI-driven score improvement, not a user-directed question bank.

## TaskIntentDraft

- Outcome: align the iOS/iPad first-release experience around an AI-orchestrated preparation workflow.
- Goal: make the first screen answer "what should I do next to improve my score?" instead of "which feature should I open?"
- Success evidence: the app opens into a generated task stream; qbank, position matching, essay, and photo explanation are task tools, not primary product identity.
- Stop condition: user approves this information architecture and a separate implementation plan can be written.
- Non-goals: semantic qbank compaction, App Store product creation, open-web Deep Research, full admin console, or Android/Web clients.
- Scope: learner iPhone/iPad IA, onboarding flow, daily task stream, contextual AI assistant, task execution shell, and backend contract shape.
- Risks: over-indexing on chat, hiding useful data too deeply, AI plan quality, AI cost runaway, and iPad becoming a stretched iPhone UI.

## BaselineUsageDraft

- Required baseline refs: research report, commercial execution master plan, MVP bridge plan, current SwiftUI shell.
- Acknowledged before plan refs: iOS-first, iPad first-release support, Sign in with Apple, App Store IAP, tight AI quota, Gemini reverse proxy as temporary provider, announcements deferred.
- Cited in design refs: research report sections describing AI as product OS, current-goal/next-task homepage, cold-start output, and daily task stream; master plan AI boundary and iPad requirement; bridge plan Slice 2 taskstream.
- Missing refs: none for IA design.
- Decision: continue.

## Requirement Ready Check

- Requirement source refs: user conversation plus the three authority docs above.
- Goals and scope refs: AI-first commercial product from scratch, not the abandoned old project and not a generic MVP clone.
- User / scenario refs: learner wants the app to decide the learning path from target, profile, time budget, diagnostic result, and actual performance.
- Requirement item refs: cold-start diagnosis, position shortlist, weekly plan, daily task stream, photo explanation, wrong-book review, contextual AI feedback.
- Acceptance / verification criteria refs: iPhone/iPad screenshots and builds; no first-level "刷题" identity; generated taskstream contract; AI quota/audit boundary preserved.
- Open blocker questions: none before design review.
- Decision: ready.

## Product Thesis

考公搭子 is an AI preparation agent with a high-reliability exam knowledge base. The learner should feel: "I tell it my target and constraints; it arranges today, watches my performance, explains why, and adjusts tomorrow."

This rejects the current mental model of "今日 / 刷题 / 选岗 / 我的" as first-level navigation. That model asks the learner to choose features. The intended product asks the learner to follow a plan.

## NotebookLM Workbench Pattern

The user's live NotebookLM study session on 2026-07-03 shows the closest existing pattern to the desired product: an AI learning workbench. It turns source material into study objects such as audio overview, video overview, report, quiz, flashcards, mind map, briefing document, infographic, and tables, then keeps those generated objects visible in a studio-like workspace.

The value is not generic chat and not a classic question bank. It is source-grounded study production: AI converts materials into things the learner can immediately use, review, and ask about.

NotebookLM is not vertically built for 公考. 考公搭子 should keep the workbench pattern but make the generated objects exam-native:

1. Treat exam materials, position announcements, qbank items, tags, and knowledge chunks as a learner-specific source base.
2. Generate 公考-native study objects: 今日测验, 岗位风险报告, 高频考点卡, 错因复盘, 申论重写稿, 时政速记, 模考分析, and 周计划.
3. Let AI assemble those objects into the daily task stream instead of asking the learner to browse features.
4. Show source grounding when AI explains a question, position risk, policy point, or essay feedback.
5. Keep the task, generated study objects, and AI explanation side by side on iPad, preserving the workbench feel.
6. Add lightweight quality feedback on AI explanations and generated study objects so bad outputs can be audited and improved.

### Source Discovery Boundary

NotebookLM can discover web sources and run deeper online research. 考公搭子 should not attempt broad open-web research in the first iOS release.

First release source scope:

- self-held canonical qbank, explanations, tags, and knowledge chunks;
- imported position data;
- user-provided materials such as screenshots, PDFs, text, and URLs when explicitly added;
- manually curated official announcements or policy files when available.

Deferred source scope:

- web-wide source discovery;
- Deep Research-style multi-site browsing and report generation;
- automatic announcement crawling at scale;
- generated audio/video overviews.

Reason: 公考 trust depends on officialness, recency, and regional correctness. A half-built web search agent would increase hallucination, compliance, cost, and review risk. The first commercial version should feel intelligent by using high-quality owned sources, not by pretending to be a general web research engine.

## Product Principles

1. AI chooses the next useful action; the learner can question, pause, or replan, but does not start by browsing modules.
2. Qbank, positions, essays, photo explanation, and wrong-book are tools inside a workflow.
3. Every AI explanation must end in a concrete next action or mastery update.
4. The home screen is organized by current goal, score gap, time budget, and today's sequence.
5. iPad is a real workbench: plan, task, and feedback can coexist.
6. Commercial-grade paths are explicit. No hidden degraded modes, no fake fallback behavior.

## Two-Track Exam Model

申论 must not be deferred as an optional side feature. 公考笔试 has two first-class preparation tracks:

- 行测: objective-question accuracy, speed, module mastery, wrong-book repair, and same-type transfer.
- 申论: material reading, point extraction, answer structure, language compression, rewrite comparison, and topic accumulation.

The product should not expose "行测" and "申论" as separate bottom tabs because that recreates a feature-browsing app. Instead, 今日 and 工作台 must make the two tracks visible:

- 今日 shows the AI-arranged mix, for example "行测 24 分钟 + 申论 16 分钟".
- 工作台 groups generated study objects by track, with 行测训练 and 申论写作 as persistent sections.
- The planner decides whether today is 行测-heavy, 申论-heavy, or balanced based on target exam, time budget, diagnostic result, and recent completion.
- The learner can ask why the mix is arranged this way and request a replan, but the default is still AI-arranged execution.

First-release 申论 capability should be a complete rewrite loop, not a full human-grade scoring promise:

1. Select a 申论小题 or material segment from the workbench or today's plan.
2. Show material, prompt, word limit, and answer box.
3. Learner submits an answer.
4. AI reviews by dimension: 要点完整度, 概括抽象度, 结构层次, 语言压缩, 格式规范.
5. AI marks missing or verbose points and asks for one targeted rewrite.
6. Learner rewrites; the app compares version 1 and version 2.
7. The result becomes a 申论复盘 object and updates tomorrow's plan.

Full essay-package upsells, human fine review, and full-paper scoring can come later, but the first commercial app should already make users feel that 申论 is part of the daily AI preparation system.

## Options

### Option A: Taskstream-First AI Agent

The app opens to today's AI-generated sequence. Navigation exposes only 今日, 工作台, and 我的. Goal, review, and assistant are contextual surfaces inside the workbench, while practice and positions live inside generated study objects and task execution.

- Pros: matches research report, strongest differentiation, creates daily retention loop, keeps AI cost controllable by task.
- Cons: requires planner quality and clear empty states; less familiar than a classic qbank app.
- Verdict: recommended.

### Option B: Chat-First Companion

The app opens to a chat where the learner asks what to do.

- Pros: strongly signals AI; fast to prototype.
- Cons: still forces users to prompt and plan; hard to verify score improvement; likely becomes generic chatbot.
- Verdict: not first screen.

### Option C: Feature Workbench With AI Summary

Keep feature tabs, add an AI card summarizing plan and suggestions.

- Pros: easiest migration from current app.
- Cons: preserves "刷题软件" perception; AI remains a widget rather than the operating system.
- Verdict: reject.

## Recommended Information Architecture

### iPhone Navigation

Use three learner-facing tabs:

- 今日: the default entry. Shows target, score gap, today's AI-generated sequence, one primary start/continue action, and the immediate post-task feedback loop.
- 工作台: generated study objects and source-grounded materials, grouped by 行测训练, 申论写作, 岗位策略, and 周计划. Includes quizzes, flashcards, reports, mind maps, essay rewrites, position briefs, wrong-book reviews, and weekly plans.
- 我的: Apple account, AI Pro, quota, StoreKit entitlement state, settings.

Do not expose "刷题", "选岗", "目标", "复盘", or "搭子" as first-level tabs.

- 目标 lives as the top context strip and a detail page inside 今日/工作台.
- 复盘 is an automatically generated study object and post-task state.
- 搭子 is a contextual assistant button/composer inside every task or object, not an empty chat destination.
- 刷题 and 选岗 are execution modes inside tasks and generated objects.

### iPad Layout

Use a deliberate three-pane workbench on regular width:

- Left pane: target, week timeline, today's ordered tasks, and generated study objects.
- Center pane: current task execution or selected study object, including quiz, flashcards, report, practice, review, essay, or photo explanation.
- Right pane: AI rationale, source grounding, hints, feedback, mastery delta, quota state, and replan controls.

The iPad should not use the same bottom-tab-first structure as iPhone. It should feel like a focused study desk with a persistent source-grounded assistant panel.

## First-Launch Flow

1. Learner signs in with Apple for persisted commercial state.
2. Learner selects target exam, target year, region, and preferred system.
3. Learner enters education, major, identity, and restrictions needed for position matching.
4. Learner states weekly availability and daily time budget.
5. Learner completes a timed 12-minute diagnostic quiz.
6. AI generates:
   - position shortlist;
   - current estimate and score gap;
   - this week's strategy;
   - today's first task sequence.

The first generated result screen should not be a dashboard of features. It should say, in product terms: "搭子已经排好今天".

## Today Screen Contract

The 今日 screen has one primary job: start or continue the current AI-arranged task.

Required sections:

- Goal strip: target exam/region/position group and target date if known.
- AI diagnosis brief: current estimate, score gap, weakest module, daily available minutes.
- Track mix: today's 行测/申论 time and why the planner chose that balance.
- Primary CTA: "开始今日学习" or "继续第 N 项".
- Ordered task sequence: each task has type, module, reason, count, expected minutes, state.
- Why this plan: one short AI rationale tied to evidence.
- Feedback loop: after completion, show what changed and what tomorrow may adjust.

Example copy direction:

```text
搭子给你排好了今天 38 分钟
当前先补资料分析的比重变化题，因为它拖低了你和广东省考目标线的差距。
顺序不建议打乱：资料 8 题 -> 言语 6 题 -> 错因复盘 4 题。
```

## Task Execution Contract

Each task is entered from the task stream and returns learning evidence.

- Practice task: uses canonical qbank questions, accepted preferred AI-washed explanations when available, and records answer evidence.
- Review task: pulls wrong-book or weak knowledge points, then records whether the learner corrected the error type.
- Essay task: no stored "解析" requirement for 申论; AI feedback is a dimension review and rewrite loop, producing a version comparison and next revision action.
- Photo explanation task: extracts and matches first; matched questions return canonical content, unmatched explanations stay ephemeral and audited.
- Assistant intervention: AI can hint, explain, compare, or replan only inside the current learning context.

## Backend Contract Shape

The UI should move toward these product contracts:

- `POST /api/v1/onboarding/start`
- `POST /api/v1/onboarding/quiz`
- `POST /api/v1/onboarding/generate`
- `GET /api/v1/taskstream/today`
- `POST /api/v1/taskstream/tasks/{task_id}/start`
- `POST /api/v1/taskstream/tasks/{task_id}/answer`
- `POST /api/v1/taskstream/tasks/{task_id}/complete`
- `POST /api/v1/taskstream/regenerate`
- `POST /api/v1/questions/photo-explain`
- `POST /api/v1/assistant/contextual`

AI may write `study_plans`, `daily_task_logs`, contextual feedback, and AI audit logs. AI must not mutate canonical questions, explanations, positions, entitlements, pricing, or App Store transaction authority.

## Initial UI Refactor Boundary

The next implementation slice should replace the current visual shell and preview data with an AI-agent-shaped runnable slice:

- Rename first-level sections to the recommended IA.
- Replace the "刷题" tab with task execution launched from 今日.
- Replace the "选岗" tab with goal context and position briefs inside 今日/工作台.
- Remove standalone first-level 目标, 复盘, and 搭子 navigation.
- Add iPhone today-first copy and state hierarchy.
- Add iPad three-pane workbench structure.
- Keep backend preview endpoints only as explicit preview contracts until real Slice 2 APIs are implemented.

## Acceptance Criteria

- iPhone first screen opens to an AI-arranged daily plan with one dominant "start/continue" CTA.
- iPhone has no more than three first-level tabs: 今日, 工作台, 我的.
- No first-level navigation item is named "刷题", "选岗", "目标", "复盘", or "搭子".
- 今日 and 工作台 visibly support both 行测 and 申论 as first-class study tracks.
- 申论 has a complete material -> answer -> dimension review -> rewrite -> comparison -> next-plan loop.
- Position data is visible through goal/strategy, not as a standalone app identity.
- Practice UI is reachable as task execution, not browsing.
- iPad regular width shows plan, task, and AI feedback simultaneously.
- Open-web Deep Research is not part of the first release.
- AI quota/paywall surfaces are shown only at explicit AI feature gates.
- The design preserves the master-plan AI boundary and StoreKit entitlement authority.
- Build verification covers iPhone and iPad simulator targets.

## Product Risk Lens

- Value: stronger differentiation and retention because the app sells "follow this plan to improve" rather than "we have many questions".
- Non-goals: hiding all controls, making AI infallible, or replacing canonical qbank reliability with generated content.
- Trade-offs: less familiar than classic qbank apps, but more aligned with the original opportunity.
- Decision needed: approve Option A as the implementation target.

## Architecture Integrity Lens

- Canonical owner: backend taskstream and study plan contracts own what should happen next; SwiftUI renders and records learner actions.
- Responsibility overlap: qbank, positions, essay, and photo features must not each become independent first-level planners.
- Higher-level simplification: "task" is the shared execution unit across practice, review, essay, and photo.
- Retirement / falsifier: if users cannot understand why AI arranged a task or cannot find core capabilities from the taskstream, the IA needs revision.
- Verdict: proceed with taskstream-first IA.

## Spec Self-Review

- No implementation tasks are authorized until user approval of this spec.
- No old-project runtime dependency is introduced.
- No hidden fallback or degraded AI path is specified.
- The iPad requirement is explicit.
- Commercial boundaries remain intact: Apple auth, StoreKit/IAP authority, server-side quota/audit.
