import SwiftUI

/// 阶段一开发夹具：覆盖全部 14 种词表卡 + 1 种词表外类型（验证 fallback 不崩溃）。
/// 服务端 /api/v1/feed/today 就绪后（阶段二），今日页切换为真实数据，本夹具仅保留给渲染器回归用。
enum FeedFixtures {
    static var sample: FeedResponse {
        let data = Data(json.utf8)
        let decoder = JSONDecoder()
        do {
            return try decoder.decode(FeedResponse.self, from: data)
        } catch {
            assertionFailure("Feed 夹具解码失败: \(error)")
            return FeedResponse(generatedAt: "", planBasis: "rules_v1", cards: [])
        }
    }

    private static let json = """
    {
      "generatedAt": "2026-07-04T08:00:00Z",
      "planBasis": "rules_v1",
      "cards": [
        {"cardId": "c1", "type": "mock_review_pending", "priority": 100,
         "reason": "周三的模考复盘还没看，看完才算完成本周模考",
         "payload": {"sessionId": "mock-1", "estimatedScore": 61.5}},
        {"cardId": "c2", "type": "coach_brief", "priority": 95,
         "reason": "根据你的时间预算与近期正确率生成",
         "payload": {"text": "今天 2 小时：先清资料分析的 8 道错题，再做 15 题言语保持手感。模考复盘记得看。", "planBasis": "rules_v1"}},
        {"cardId": "c3", "type": "goal_gap", "priority": 90,
         "reason": "距 2027 国考 145 天，你与目标差 8.5 分",
         "dataLine": "基于 1 次摸底 + 214 题练习 + 2 次模考",
         "payload": {"targetScore": 131.2, "estimatedScore": 122.7, "daysLeft": 145, "positionName": "国家税务总局江西省税务局兴国县税务局一级行政执法员"}},
        {"cardId": "c4", "type": "task_practice", "priority": 80,
         "reason": "资料分析近两周正确率 58%，低于其他模块",
         "payload": {"taskId": "t-1", "moduleName": "资料分析", "questionCount": 10, "estMinutes": 25}},
        {"cardId": "c5", "type": "task_method_card", "priority": 75,
         "reason": "这批错题里 6 题属于基期量计算",
         "payload": {"taskId": "t-2", "cardId": "mc-jbl-001", "cardTitle": "基期量：一步除法的三种伪装", "estMinutes": 8}},
        {"cardId": "c6", "type": "task_essay", "priority": 70,
         "reason": "距上次申论练习已 5 天",
         "payload": {"taskId": "t-3", "questionType": "概括归纳", "estMinutes": 30}},
        {"cardId": "c7", "type": "mock_exam_due", "priority": 65,
         "reason": "每周模考：本周还没考",
         "payload": {"sessionId": "mock-2", "estMinutes": 120}},
        {"cardId": "c8", "type": "wrong_cluster", "priority": 60,
         "reason": "近 7 天新增错题集中在此",
         "payload": {"nodeId": "n-1", "displayPath": "判断推理 · 定义判断 · 多定义", "wrongCount": 7}},
        {"cardId": "c9", "type": "streak_restart", "priority": 55,
         "reason": "中断不影响累计数据，从小任务恢复节奏最有效",
         "payload": {"daysIdle": 3, "miniTaskId": "t-mini"}},
        {"cardId": "c10", "type": "calibration_note", "priority": 50,
         "reason": "第 2 次模考已计入能力估计",
         "dataLine": "基于 1 次摸底 + 214 题练习 + 2 次模考",
         "payload": {"statusLabel": "预估行测 62.7（校准中）", "delta": 1.2}},
        {"cardId": "c11", "type": "companion_prompt", "priority": 45,
         "reason": "你上周在多定义题上连错 3 题",
         "payload": {"suggestedQuestion": "多定义判断总选错，怎么读题干？", "contextObjectId": "obj-1"}},
        {"cardId": "c12", "type": "external_capture_nudge", "priority": 40,
         "reason": "外部错题可参与聚合分析",
         "payload": {"text": "在别处做错的题，拍照收进来一起分析"}},
        {"cardId": "c13", "type": "quota_notice", "priority": 35,
         "reason": "免费档 AI 配额状态",
         "payload": {"remaining": 3, "resetAt": "明天 0 点重置"}},
        {"cardId": "c14", "type": "hologram_tutor", "priority": 30,
         "reason": "这是一张客户端词表外的卡，用于验证降级",
         "payload": {"title": "全息陪练", "body": "当前版本暂不支持展示这项内容，升级 App 后可见。"}}
      ]
    }
    """
}

#if DEBUG
/// 开发预览页：夹具驱动的渲染器回归入口（仅 DEBUG 构建可见）。
struct FeedPreviewView: View {
    var body: some View {
        ScrollView {
            FeedCardList(cards: FeedFixtures.sample.cards) { taskId in
                print("[feed-preview] open task: \(taskId)")
            }
            .padding(20)
        }
        .background(DaziPalette.paper)
        .navigationTitle("Feed 预览（夹具）")
        .navigationBarTitleDisplayMode(.inline)
    }
}
#endif
