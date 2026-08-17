# Cause-agent run budget constraint baseline

Observed during the frozen RU-REAL-DEVICE-CAUSE-RECOVERY-033 failure scenario on
2026-08-17. The primary and official routes closed as failed audited calls and
the user relay returned a valid candidate. Publication then failed because
`ai_audit.agent_run_versions.model_call_count` still has the original
`between 0 and 2` check constraint while the frozen definition permits six
bounded calls.

The required repair is limited to that check constraint. Existing run rows are
preserved. Downgrade must fail closed when any persisted run count is above two.
