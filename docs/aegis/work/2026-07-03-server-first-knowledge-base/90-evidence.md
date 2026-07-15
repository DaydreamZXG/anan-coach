# Server-first knowledge base execution - Evidence

## EvidenceBundleDraft

- Artifact key: slice0-full-verify
- Type: command
- Source: scripts/full_verify.sh
- Summary: Backend pytest passed 4 tests; iPhone 17 and iPad Pro 13-inch simulator builds succeeded; Aegis workspace check passed.
- Verifier: codex

## EvidenceBundleDraft

- Artifact key: slice0-baseline-commit
- Type: git
- Source: git commit fe1ba0b
- Summary: Created baseline commit chore: establish gongkao dazi baseline on codex/server-first-knowledge-base.
- Verifier: codex

## EvidenceBundleDraft

- Artifact key: slice1-postgresql-schema
- Type: command
- Source: ssh root@101.96.198.253 postgresql/schema verification
- Summary: PostgreSQL active; pgvector extension installed; schemas source_qbank/canonical_qbank/product/learner/ai_audit/commerce/ops exist; gongkao_api can connect; port 5432 listens only on 127.0.0.1.
- Verifier: codex

## EvidenceBundleDraft

- Artifact key: slice2-qbank-staging
- Type: command
- Source: rsync + remote sha256sum/du/df verification
- Summary: Transferred qbank dump, SQL, reports, and manifest to /srv/gongkao-dazi/import-staging/qbank. Dump checksum matches manifest; staging size is 309M; server root disk has 53G available.
- Verifier: codex

## EvidenceBundleDraft

- Artifact key: slice3-canonical-qbank-rebuild
- Type: command
- Source: scripts/server_restore_canonical_qbank.sh
- Summary: Restored qbank dump into source_qbank and rebuilt canonical_qbank from approved CSV artifacts. Import summary matched canonical total 55,214, objective 53,011, essay 2,203, explanations 53,011, knowledge links 23,103, accepted merge groups 243, removed duplicates 247, review queue 82.
- Verifier: codex

## EvidenceBundleDraft

- Artifact key: slice3-server-smoke
- Type: command
- Source: remote PostgreSQL smoke verification
- Summary: Verified source_qbank/canonical_qbank/ops row counts, public QuestionBank table count 0, ops count checks 12 matched and 6 recorded, PostgreSQL listening only on 127.0.0.1:5432, server disk 50G available.
- Verifier: codex

## EvidenceBundleDraft

- Artifact key: slice4-product-knowledge-import
- Type: command
- Source: scripts/server_import_product_knowledge.sh
- Summary: Imported canonical_qbank into product runtime tables on 101.96.198.253. Product counts matched: questions 55,214; objective 53,011; essay 2,203; explanations 53,011; occurrences 104,453; tags 557,844; knowledge chunks 23,103; assets 52,112.
- Verifier: codex

## EvidenceBundleDraft

- Artifact key: slice5-knowledge-api-tests-and-smoke
- Type: command
- Source: backend/.venv/bin/python -m pytest backend/tests; curl https://kg.xingguoxian.cn/api/v1/knowledge/questions/search; curl https://kg.xingguoxian.cn/api/v1/knowledge/questions/cq_31cdbc7fb615d7e849c02542
- Summary: Backend tests passed 7 tests. Public HTTPS knowledge search returned 2026 吉林 判断推理 questions from product tables. Question detail returned cq_31cdbc7fb615d7e849c02542 with options, answer B, ai_rewrite_accepted preferred explanation, 11 provincial occurrences, tags, and no staging schema ownership in the response.
- Verifier: codex

## EvidenceBundleDraft

- Artifact key: slice6-practice-essay-ios-loop
- Type: command
- Source: scripts/full_verify.sh; public HTTPS smoke; XcodeBuildMCP iPad runtime screenshot/snapshot
- Summary: Implemented taskstream/practice/essay routes and iOS AppState API integration. Public smoke verified taskstream today, practice task start, practice answer feedback, essay task, AI five-dimension review with quota/audit, rewrite comparison, and audit table rows. full_verify passed: backend 14 tests, iPhone 17 build, iPad Pro 13-inch build, Aegis check. iPad simulator launched and displayed server-loaded taskstream/essay workbench.
- Verifier: codex
