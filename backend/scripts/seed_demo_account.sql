-- 演示种子账号：备考第5周考生
-- 用途：Demo 录屏、视频拍摄
-- 执行方式：sudo -u postgres psql -d gongkao_dazi -f seed_demo_account.sql

BEGIN;

-- 固定 UUID
DO $$
DECLARE
  v_user_id uuid := 'a0000000-0000-0000-0000-000000000001';
  v_identity_id uuid := 'a0000000-0000-0000-0000-000000000002';
  v_session_id uuid := 'a0000000-0000-0000-0000-000000000003';
  v_placement_id uuid := 'a0000000-0000-0000-0000-000000000004';
  v_mock_1 uuid := 'a0000000-0000-0000-0000-000000000010';
  v_mock_2 uuid := 'a0000000-0000-0000-0000-000000000020';
BEGIN
  -- 如果已存在先删除（幂等）
  DELETE FROM learner.external_wrong_questions WHERE user_id = v_user_id;
  DELETE FROM learner.external_question_intakes WHERE user_id = v_user_id;
  DELETE FROM learner.mock_exam_sessions WHERE user_id = v_user_id;
  DELETE FROM learner.essay_attempts WHERE learner_key = v_user_id::text;
  DELETE FROM learner.practice_answers WHERE user_id = v_user_id;
  DELETE FROM learner.placement_attempts WHERE user_id = v_user_id;
  DELETE FROM learner.app_sessions WHERE user_id = v_user_id;
  DELETE FROM learner.profiles WHERE user_id = v_user_id;
  DELETE FROM learner.apple_identities WHERE user_id = v_user_id;
  DELETE FROM ai_audit.usage_daily WHERE learner_key = v_user_id::text;
  DELETE FROM ai_audit.interaction_logs WHERE learner_key = v_user_id::text;
  DELETE FROM learner.users WHERE user_id = v_user_id;

  -- 1. 用户（5周前注册）
  INSERT INTO learner.users (user_id, display_name, email, is_active, created_at, updated_at)
  VALUES (
    v_user_id,
    '演示账号',
    'demo@xingguoxian.cn',
    true,
    now() - interval '35 days',
    now()
  );

  -- 2. Apple 身份（用特殊 sub，不会和真实 Apple 用户冲突）
  INSERT INTO learner.apple_identities (
    identity_id, user_id, apple_sub, email, email_verified, is_private_email,
    last_identity_token_hash, created_at, updated_at
  ) VALUES (
    v_identity_id, v_user_id, 'demo_seed_account_001',
    'demo@xingguoxian.cn', false, false,
    'demo_seed_hash_placeholder',
    now() - interval '35 days', now()
  );

  -- 3. 会话（有效 token，30天过期）
  INSERT INTO learner.app_sessions (
    session_id, user_id, session_token_hash, device_id, expires_at, created_at, last_seen_at
  ) VALUES (
    v_session_id, v_user_id,
    'demo_session_token_hash_001',
    'demo-device-001',
    now() + interval '30 days',
    now() - interval '35 days',
    now()
  );

  -- 4. Profile（已完成 onboarding，国考目标）
  INSERT INTO learner.profiles (
    user_id, target_exam, target_region, position_group, daily_minutes,
    education, major, fresh_graduate, political_status, grassroots_years,
    study_mode, target_score,
    baseline_source, baseline_xingce_score, baseline_shenlun_score,
    baseline_weak_modules, baseline_module_snapshot,
    onboarding_complete, created_at, updated_at
  ) VALUES (
    v_user_id,
    '国考', '北京', '综合管理类', 120,
    '本科', '法学', false, '群众', 0,
    'parttime', 135.00,
    'placement_test', 58.50, 52.00,
    '["数量关系","资料分析"]'::jsonb,
    '{"言语理解与表达":65,"数量关系":45,"判断推理":60,"资料分析":50,"常识判断":70}'::jsonb,
    true,
    now() - interval '35 days', now()
  );

  -- 5. 摸底测试记录
  INSERT INTO learner.placement_attempts (
    attempt_id, user_id, task_id, question_ids, answers, module_snapshot,
    estimated_xingce_score, created_at
  ) VALUES (
    v_placement_id, v_user_id, 'placement_test_week0',
    '["cq_66ca5fecbe7f9ec322859c54","cq_1869e7e820148e459058ee1f","cq_cdf15cf21a19c08df15e4117","cq_00fbb69db15a30b9ed0f22ca","cq_43d150848d86e80045f98eef","cq_426529cd154f55ee462feef8","cq_3734ecbaceded38139e65c1f","cq_ce303f0381f565d41f31057d","cq_08c3d1be14c3210344cd6202","cq_188aafc660a8b75af58bfc38"]'::jsonb,
    '{"cq_66ca5fecbe7f9ec322859c54":"D","cq_1869e7e820148e459058ee1f":"C","cq_cdf15cf21a19c08df15e4117":"A","cq_00fbb69db15a30b9ed0f22ca":"B","cq_43d150848d86e80045f98eef":"C","cq_426529cd154f55ee462feef8":"A","cq_3734ecbaceded38139e65c1f":"B","cq_ce303f0381f565d41f31057d":"C","cq_08c3d1be14c3210344cd6202":"B","cq_188aafc660a8b75af58bfc38":"C"}'::jsonb,
    '{"言语理解与表达":{"correct":2,"total":2},"数量关系":{"correct":1,"total":2},"判断推理":{"correct":2,"total":3},"资料分析":{"correct":1,"total":2},"常识判断":{"correct":1,"total":1}}'::jsonb,
    58.50,
    now() - interval '35 days'
  );

  -- 6. 练习记录（5周，共40题，正确率约65%）
  -- 第1周（35-29天前）：言语理解 8题
  INSERT INTO learner.practice_answers (answer_id, user_id, task_id, question_id, selected_answer, correct_answer, correct, answered_at) VALUES
  ('b0000000-0000-0000-0000-000000000101', v_user_id, 'practice_w1_yanyu', 'cq_66ca5fecbe7f9ec322859c54', 'D', 'D', true,  now() - interval '33 days'),
  ('b0000000-0000-0000-0000-000000000102', v_user_id, 'practice_w1_yanyu', 'cq_1869e7e820148e459058ee1f', 'C', 'C', true,  now() - interval '33 days'),
  ('b0000000-0000-0000-0000-000000000103', v_user_id, 'practice_w1_yanyu', 'cq_c5b9230fdb2126488b68f4de', 'B', 'C', false, now() - interval '33 days'),
  ('b0000000-0000-0000-0000-000000000104', v_user_id, 'practice_w1_yanyu', 'cq_dcae3925369b8e14cd305dab', 'B', 'B', true,  now() - interval '32 days'),
  ('b0000000-0000-0000-0000-000000000105', v_user_id, 'practice_w1_yanyu', 'cq_c17f900444e8a4a8d253ba6f', 'A', 'B', false, now() - interval '32 days'),
  ('b0000000-0000-0000-0000-000000000106', v_user_id, 'practice_w1_yanyu', 'cq_1b0f0398183c9ce01ac0abe7', 'A', 'A', true,  now() - interval '31 days'),
  ('b0000000-0000-0000-0000-000000000107', v_user_id, 'practice_w1_yanyu', 'cq_43d150848d86e80045f98eef', 'C', 'C', true,  now() - interval '31 days'),
  ('b0000000-0000-0000-0000-000000000108', v_user_id, 'practice_w1_yanyu', 'cq_3734ecbaceded38139e65c1f', 'C', 'B', false, now() - interval '30 days');

  -- 第2周（28-22天前）：判断推理 8题
  INSERT INTO learner.practice_answers (answer_id, user_id, task_id, question_id, selected_answer, correct_answer, correct, answered_at) VALUES
  ('b0000000-0000-0000-0000-000000000201', v_user_id, 'practice_w2_panduan', 'cq_00fbb69db15a30b9ed0f22ca', 'C', 'C', true,  now() - interval '26 days'),
  ('b0000000-0000-0000-0000-000000000202', v_user_id, 'practice_w2_panduan', 'cq_cdf15cf21a19c08df15e4117', 'A', 'A', true,  now() - interval '26 days'),
  ('b0000000-0000-0000-0000-000000000203', v_user_id, 'practice_w2_panduan', 'cq_426529cd154f55ee462feef8', 'B', 'A', false, now() - interval '25 days'),
  ('b0000000-0000-0000-0000-000000000204', v_user_id, 'practice_w2_panduan', 'cq_ce303f0381f565d41f31057d', 'D', 'D', true,  now() - interval '25 days'),
  ('b0000000-0000-0000-0000-000000000205', v_user_id, 'practice_w2_panduan', 'cq_188aafc660a8b75af58bfc38', 'C', 'C', true,  now() - interval '24 days'),
  ('b0000000-0000-0000-0000-000000000206', v_user_id, 'practice_w2_panduan', 'cq_b7a97fac681bba0434347b36', 'B', 'B', true,  now() - interval '24 days'),
  ('b0000000-0000-0000-0000-000000000207', v_user_id, 'practice_w2_panduan', 'cq_0c7a7d4d326e36b10d0fb760', 'B', 'A', false, now() - interval '23 days'),
  ('b0000000-0000-0000-0000-000000000208', v_user_id, 'practice_w2_panduan', 'cq_40f1a09db03f01cef68e1c6a', 'C', 'C', true,  now() - interval '22 days');

  -- 第3周（21-15天前）：常识判断 8题 + 第一次模考
  INSERT INTO learner.practice_answers (answer_id, user_id, task_id, question_id, selected_answer, correct_answer, correct, answered_at) VALUES
  ('b0000000-0000-0000-0000-000000000301', v_user_id, 'practice_w3_changshi', 'cq_08c3d1be14c3210344cd6202', 'B', 'B', true,  now() - interval '19 days'),
  ('b0000000-0000-0000-0000-000000000302', v_user_id, 'practice_w3_changshi', 'cq_8993c213b95af6c1fe2796d7', 'D', 'D', true,  now() - interval '19 days'),
  ('b0000000-0000-0000-0000-000000000303', v_user_id, 'practice_w3_changshi', 'cq_f0a97eea6ffe6c3792898bb8', 'C', 'D', false, now() - interval '18 days'),
  ('b0000000-0000-0000-0000-000000000304', v_user_id, 'practice_w3_changshi', 'cq_edaa6e2aee7d23eed0b53832', 'A', 'A', true,  now() - interval '18 days'),
  ('b0000000-0000-0000-0000-000000000305', v_user_id, 'practice_w3_changshi', 'cq_34d5a90dae8daf95b8066d67', 'B', 'B', true,  now() - interval '17 days'),
  ('b0000000-0000-0000-0000-000000000306', v_user_id, 'practice_w3_changshi', 'cq_078f8eb6ae5af2b2e3266f20', 'C', 'D', false, now() - interval '17 days'),
  ('b0000000-0000-0000-0000-000000000307', v_user_id, 'practice_w3_changshi', 'cq_cb56373af2ba768db751a54f', 'A', 'A', true,  now() - interval '16 days'),
  ('b0000000-0000-0000-0000-000000000308', v_user_id, 'practice_w3_changshi', 'cq_b92d22f427ec2a62666a8b1d', 'A', 'A', true,  now() - interval '15 days');

  -- 第4周（14-8天前）：言语理解+判断推理混合 8题
  INSERT INTO learner.practice_answers (answer_id, user_id, task_id, question_id, selected_answer, correct_answer, correct, answered_at) VALUES
  ('b0000000-0000-0000-0000-000000000401', v_user_id, 'practice_w4_mix', 'cq_bfd9c44b78b3fd08ef481c10', 'D', 'D', true,  now() - interval '12 days'),
  ('b0000000-0000-0000-0000-000000000402', v_user_id, 'practice_w4_mix', 'cq_7c3eab1be861c5e714c3ff39', 'C', 'D', false, now() - interval '12 days'),
  ('b0000000-0000-0000-0000-000000000403', v_user_id, 'practice_w4_mix', 'cq_3f988adb79d2b3a4df8f1477', 'C', 'C', true,  now() - interval '11 days'),
  ('b0000000-0000-0000-0000-000000000404', v_user_id, 'practice_w4_mix', 'cq_be6a5d3bfdafcd444d8a494d', 'D', 'D', true,  now() - interval '11 days'),
  ('b0000000-0000-0000-0000-000000000405', v_user_id, 'practice_w4_mix', 'cq_c97868a202584e9a354d628d', 'B', 'B', true,  now() - interval '10 days'),
  ('b0000000-0000-0000-0000-000000000406', v_user_id, 'practice_w4_mix', 'cq_ebcc1bd0c4ac359fcb4cb25b', 'C', 'C', true,  now() - interval '10 days'),
  ('b0000000-0000-0000-0000-000000000407', v_user_id, 'practice_w4_mix', 'cq_2303762555366884c07c8670', 'A', 'D', false, now() - interval '9 days'),
  ('b0000000-0000-0000-0000-000000000408', v_user_id, 'practice_w4_mix', 'cq_00c952d1917357d66f18735b', 'B', 'B', true,  now() - interval '8 days');

  -- 第5周（7-1天前）：数量关系+资料分析 8题（弱项突击）
  INSERT INTO learner.practice_answers (answer_id, user_id, task_id, question_id, selected_answer, correct_answer, correct, answered_at) VALUES
  ('b0000000-0000-0000-0000-000000000501', v_user_id, 'practice_w5_weak', 'cq_7c412296393dd25140562dcb', 'C', 'C', true,  now() - interval '5 days'),
  ('b0000000-0000-0000-0000-000000000502', v_user_id, 'practice_w5_weak', 'cq_bbc7be610335d8fafc175536', 'A', 'A', true,  now() - interval '5 days'),
  ('b0000000-0000-0000-0000-000000000503', v_user_id, 'practice_w5_weak', 'cq_48d972ad427e8ba464e18bc2', 'B', 'C', false, now() - interval '4 days'),
  ('b0000000-0000-0000-0000-000000000504', v_user_id, 'practice_w5_weak', 'cq_87e674c7e4f959d8a03edba3', 'B', 'B', true,  now() - interval '4 days'),
  ('b0000000-0000-0000-0000-000000000505', v_user_id, 'practice_w5_weak', 'cq_810b54fe89cf6d4abbc9895d', 'D', 'D', true,  now() - interval '3 days'),
  ('b0000000-0000-0000-0000-000000000506', v_user_id, 'practice_w5_weak', 'cq_1a98adcda817b43ddfa457b2', 'C', 'C', true,  now() - interval '3 days'),
  ('b0000000-0000-0000-0000-000000000507', v_user_id, 'practice_w5_weak', 'cq_a595854fc064c6bbc45d28f4', 'A', 'B', false, now() - interval '2 days'),
  ('b0000000-0000-0000-0000-000000000508', v_user_id, 'practice_w5_weak', 'cq_6495dd1a1b04c0af0446f735', 'C', 'C', true,  now() - interval '1 days');

  -- 7. 模考记录（第3周和第5周各一次）

  -- 第一次模考（第3周，已交卷已复盘）
  INSERT INTO learner.mock_exam_sessions (
    session_id, user_id, exam_scope, track, status,
    question_ids, blueprint, answers, review_payload, review_viewed_at,
    submitted_at, created_at, updated_at
  ) VALUES (
    v_mock_1, v_user_id, 'guokao', 'xingce', 'reviewed',
    '["cq_66ca5fecbe7f9ec322859c54","cq_1869e7e820148e459058ee1f","cq_c5b9230fdb2126488b68f4de","cq_dcae3925369b8e14cd305dab","cq_c17f900444e8a4a8d253ba6f","cq_1b0f0398183c9ce01ac0abe7","cq_43d150848d86e80045f98eef","cq_3734ecbaceded38139e65c1f","cq_08c3d1be14c3210344cd6202","cq_8993c213b95af6c1fe2796d7","cq_00fbb69db15a30b9ed0f22ca","cq_cdf15cf21a19c08df15e4117","cq_426529cd154f55ee462feef8","cq_ce303f0381f565d41f31057d","cq_188aafc660a8b75af58bfc38","cq_b7a97fac681bba0434347b36","cq_0c7a7d4d326e36b10d0fb760","cq_40f1a09db03f01cef68e1c6a","cq_c97868a202584e9a354d628d","cq_ebcc1bd0c4ac359fcb4cb25b"]'::jsonb,
    '{"total_questions":20,"modules":{"言语理解与表达":4,"判断推理":4,"数量关系":4,"资料分析":4,"常识判断":4},"duration_minutes":120}'::jsonb,
    '{"cq_66ca5fecbe7f9ec322859c54":"D","cq_1869e7e820148e459058ee1f":"C","cq_c5b9230fdb2126488b68f4de":"B","cq_dcae3925369b8e14cd305dab":"B","cq_c17f900444e8a4a8d253ba6f":"A","cq_1b0f0398183c9ce01ac0abe7":"A","cq_43d150848d86e80045f98eef":"C","cq_3734ecbaceded38139e65c1f":"C","cq_08c3d1be14c3210344cd6202":"B","cq_8993c213b95af6c1fe2796d7":"D","cq_00fbb69db15a30b9ed0f22ca":"C","cq_cdf15cf21a19c08df15e4117":"A","cq_426529cd154f55ee462feef8":"B","cq_ce303f0381f565d41f31057d":"D","cq_188aafc660a8b75af58bfc38":"C","cq_b7a97fac681bba0434347b36":"B","cq_0c7a7d4d326e36b10d0fb760":"B","cq_40f1a09db03f01cef68e1c6a":"C","cq_c97868a202584e9a354d628d":"B","cq_ebcc1bd0c4ac359fcb4cb25b":"C"}'::jsonb,
    '{"total_score":62.5,"correct_count":13,"wrong_count":7,"accuracy":0.65,"module_breakdown":{"言语理解与表达":{"correct":3,"wrong":1,"accuracy":0.75},"判断推理":{"correct":3,"wrong":1,"accuracy":0.75},"数量关系":{"correct":2,"wrong":2,"accuracy":0.50},"资料分析":{"correct":2,"wrong":2,"accuracy":0.50},"常识判断":{"correct":3,"wrong":1,"accuracy":0.75}},"weak_clusters":[{"module":"数量关系","tags":["数学运算"],"count":2},{"module":"资料分析","tags":["综合分析"],"count":2}],"next_week_suggestion":"数量关系与资料分析正确率偏低，建议本周重点突破数学运算中的行程问题和资料分析的速算技巧"}'::jsonb,
    now() - interval '20 days',
    now() - interval '21 days',
    now() - interval '21 days',
    now() - interval '20 days'
  );

  -- 第二次模考（第5周，已交卷已复盘，有进步）
  INSERT INTO learner.mock_exam_sessions (
    session_id, user_id, exam_scope, track, status,
    question_ids, blueprint, answers, review_payload, review_viewed_at,
    submitted_at, created_at, updated_at
  ) VALUES (
    v_mock_2, v_user_id, 'guokao', 'xingce', 'reviewed',
    '["cq_2303762555366884c07c8670","cq_00c952d1917357d66f18735b","cq_7c412296393dd25140562dcb","cq_bbc7be610335d8fafc175536","cq_48d972ad427e8ba464e18bc2","cq_87e674c7e4f959d8a03edba3","cq_810b54fe89cf6d4abbc9895d","cq_1a98adcda817b43ddfa457b2","cq_a595854fc064c6bbc45d28f4","cq_6495dd1a1b04c0af0446f735","cq_bfd9c44b78b3fd08ef481c10","cq_7c3eab1be861c5e714c3ff39","cq_3f988adb79d2b3a4df8f1477","cq_be6a5d3bfdafcd444d8a494d","cq_f0a97eea6ffe6c3792898bb8","cq_edaa6e2aee7d23eed0b53832","cq_34d5a90dae8daf95b8066d67","cq_078f8eb6ae5af2b2e3266f20","cq_cb56373af2ba768db751a54f","cq_b92d22f427ec2a62666a8b1d"]'::jsonb,
    '{"total_questions":20,"modules":{"言语理解与表达":4,"判断推理":4,"数量关系":4,"资料分析":4,"常识判断":4},"duration_minutes":120}'::jsonb,
    '{"cq_2303762555366884c07c8670":"D","cq_00c952d1917357d66f18735b":"B","cq_7c412296393dd25140562dcb":"C","cq_bbc7be610335d8fafc175536":"A","cq_48d972ad427e8ba464e18bc2":"C","cq_87e674c7e4f959d8a03edba3":"B","cq_810b54fe89cf6d4abbc9895d":"D","cq_1a98adcda817b43ddfa457b2":"C","cq_a595854fc064c6bbc45d28f4":"B","cq_6495dd1a1b04c0af0446f735":"C","cq_bfd9c44b78b3fd08ef481c10":"D","cq_7c3eab1be861c5e714c3ff39":"D","cq_3f988adb79d2b3a4df8f1477":"C","cq_be6a5d3bfdafcd444d8a494d":"D","cq_f0a97eea6ffe6c3792898bb8":"D","cq_edaa6e2aee7d23eed0b53832":"A","cq_34d5a90dae8daf95b8066d67":"B","cq_078f8eb6ae5af2b2e3266f20":"D","cq_cb56373af2ba768db751a54f":"A","cq_b92d22f427ec2a62666a8b1d":"A"}'::jsonb,
    '{"total_score":68.0,"correct_count":15,"wrong_count":5,"accuracy":0.75,"module_breakdown":{"言语理解与表达":{"correct":4,"wrong":0,"accuracy":1.0},"判断推理":{"correct":3,"wrong":1,"accuracy":0.75},"数量关系":{"correct":3,"wrong":1,"accuracy":0.75},"资料分析":{"correct":2,"wrong":2,"accuracy":0.50},"常识判断":{"correct":3,"wrong":1,"accuracy":0.75}},"weak_clusters":[{"module":"资料分析","tags":["综合分析"],"count":2}],"next_week_suggestion":"资料分析仍是短板，但较上次提升5.5分，言语理解全对，继续保持。下周建议针对资料分析的图表速算做专项训练","score_change":5.5}'::jsonb,
    now() - interval '2 days',
    now() - interval '3 days',
    now() - interval '3 days',
    now() - interval '2 days'
  );

  -- 8. 申论记录（1次批改）
  INSERT INTO learner.essay_attempts (
    attempt_id, learner_key, task_id, original_answer, review_payload,
    rewritten_answer, comparison_payload, created_at, updated_at
  ) VALUES (
    'essay_demo_001',
    v_user_id::text,
    'today-shenlun-summary-rewrite',
    '群众诉求响应慢，主要是热线、网格、信箱等平台分散，事项在部门间反复流转，责任边界不清，基层多头填报，反馈也不够明确。建议整合诉求渠道，建立统一派单中心，明确各环节办理时限，实现全流程可追溯。',
    '{"total_score":16,"dimensions":{"观点明确":{"score":4,"max":5,"comment":"核心观点抓住整合诉求渠道，但表述可更精炼"},"结构清晰":{"score":3,"max":5,"comment":"结构偏简单，缺少层次递进"},"语言规范":{"score":4,"max":5,"comment":"用语较规范，但公文感不足"},"对策可行":{"score":3,"max":5,"comment":"对策方向正确但可操作性不足，缺少具体措施"},"亮点":{"score":2,"max":5,"comment":"全流程可追溯是亮点"}},"overall_comment":"基本抓住了问题核心，但在结构层次和对策可操作性上还有提升空间。建议学习金字塔结构，先总后分。"}'::jsonb,
    '当前群众诉求响应存在渠道分散、流转冗余、权责不清、多头填报、反馈失当等问题。建议：一、整合诉求渠道，建立统一派单中心；二、明确权责清单与办理时限；三、推行首问负责制；四、建设全流程追溯平台，实现闭环管理。',
    '{"improvements":["增加层次结构：从问题到对策用金字塔结构","对策具体化：每条对策增加具体措施","公文用语：使用规范行政用语","逻辑递进：问题→原因→对策→预期效果"]}'::jsonb,
    now() - interval '7 days',
    now() - interval '7 days'
  );

  -- 9. AI 使用记录（申论批改用了1次，搭子问答用了2次）
  INSERT INTO ai_audit.usage_daily (learner_key, feature, usage_date, used_count, limit_count, updated_at) VALUES
  (v_user_id::text, 'essay_review', (now() - interval '7 days')::date, 1, 1, now()),
  (v_user_id::text, 'companion_ask', (now() - interval '6 days')::date, 1, 3, now()),
  (v_user_id::text, 'companion_ask', (now() - interval '4 days')::date, 1, 3, now());

END $$;

COMMIT;

-- 验证
SELECT '--- 种子数据验证 ---' as info;
SELECT count(*) as users FROM learner.users;
SELECT count(*) as profiles FROM learner.profiles WHERE onboarding_complete;
SELECT count(*) as placement FROM learner.placement_attempts;
SELECT count(*) as practice FROM learner.practice_answers;
SELECT count(*) as mocks FROM learner.mock_exam_sessions;
SELECT count(*) as essays FROM learner.essay_attempts;
SELECT count(*) as ai_usage FROM ai_audit.usage_daily;
