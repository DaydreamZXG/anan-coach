from __future__ import annotations

from pathlib import Path

import yaml


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def test_commercial_alerts_cover_api_db_workers_ai_ocr_backup_and_restore() -> None:
    path = REPOSITORY_ROOT / "ops" / "alerts" / "commercial-readiness.rules.yml"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    alerts = {rule["alert"]: rule for group in document["groups"] for rule in group["rules"]}

    assert set(alerts) == {
        "GongkaoApiUnavailable",
        "GongkaoReadyProbeUnavailable",
        "GongkaoDatabaseNotReady",
        "GongkaoCommercialDataPublicationUnavailable",
        "GongkaoBackgroundJobFailure",
        "GongkaoBackgroundBusinessTermination",
        "GongkaoBackgroundJobRegistryDrift",
        "GongkaoUnregisteredBackgroundJob",
        "GongkaoEnabledJobConsumerHeartbeatUnhealthy",
        "GongkaoDisabledJobConsumerBacklog",
        "GongkaoJobQueueStale",
        "GongkaoJobClaimStale",
        "GongkaoAIModelLedgerStale",
        "GongkaoAIQuotaReservationStale",
        "GongkaoOCRCapabilityUnavailable",
        "GongkaoCauseAICapabilityUnavailable",
        "GongkaoEncryptedBackupMissing",
        "GongkaoRestoreRehearsalStale",
    }
    assert all(rule["for"] for rule in alerts.values())
    assert alerts["GongkaoBackgroundJobFailure"]["expr"] == (
        "anan_background_jobs_failed_recent > 0"
    )
    assert alerts["GongkaoBackgroundBusinessTermination"]["labels"]["severity"] == ("warning")
    assert alerts["GongkaoUnregisteredBackgroundJob"]["expr"] == (
        "anan_background_jobs_unregistered_active > 0"
    )
    heartbeat_expression = alerts["GongkaoEnabledJobConsumerHeartbeatUnhealthy"]["expr"]
    assert "anan_job_consumer_deployed == 1" in heartbeat_expression
    assert "anan_job_consumer_enabled == 1" in heartbeat_expression
    assert "anan_job_consumer_heartbeat_healthy != 1" in heartbeat_expression
    disabled_backlog_expression = alerts["GongkaoDisabledJobConsumerBacklog"]["expr"]
    assert "anan_job_consumer_enabled == 0" in disabled_backlog_expression
    assert "anan_job_queued" in disabled_backlog_expression
    assert "anan_job_running" in disabled_backlog_expression


def test_commercial_alert_rule_cases_cover_worker_and_orphan_fail_closed_states() -> None:
    path = REPOSITORY_ROOT / "ops" / "alerts" / "commercial-readiness.test.yml"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    cases = {case["name"]: case for case in document["tests"]}

    assert set(cases) == {
        "mandatory empty queue consumer death still alerts",
        "conditional disabled empty queue is exempt",
        "conditional disabled backlog alerts",
        "active orphan alerts",
    }
    mandatory = cases["mandatory empty queue consumer death still alerts"]
    assert mandatory["alert_rule_test"][0]["alertname"] == (
        "GongkaoEnabledJobConsumerHeartbeatUnhealthy"
    )
    assert mandatory["alert_rule_test"][0]["exp_alerts"]
    exempt = cases["conditional disabled empty queue is exempt"]
    assert all(not expectation["exp_alerts"] for expectation in exempt["alert_rule_test"])
    backlog = cases["conditional disabled backlog alerts"]
    assert backlog["alert_rule_test"][0]["alertname"] == ("GongkaoDisabledJobConsumerBacklog")
    assert backlog["alert_rule_test"][0]["exp_alerts"]
    orphan = cases["active orphan alerts"]
    assert orphan["alert_rule_test"][0]["alertname"] == ("GongkaoUnregisteredBackgroundJob")
    assert orphan["alert_rule_test"][0]["exp_alerts"]


def test_backup_and_restore_publish_atomic_textfile_metrics() -> None:
    backup = (REPOSITORY_ROOT / "ops" / "scripts" / "backup.sh").read_text(encoding="utf-8")
    restore = (REPOSITORY_ROOT / "ops" / "scripts" / "restore_rehearsal.sh").read_text(
        encoding="utf-8"
    )

    assert "anan_backup_last_success_unixtime" in backup
    assert 'mv "${metrics_partial}" "${BACKUP_METRICS_FILE}"' in backup
    assert "anan_restore_rehearsal_last_success_unixtime" in restore
    assert 'mv "${metrics_partial}" "${RESTORE_METRICS_FILE}"' in restore


def test_monitoring_examples_require_external_hosts_and_secret_files() -> None:
    prometheus = (REPOSITORY_ROOT / "ops" / "monitoring" / "prometheus.yml.example").read_text(
        encoding="utf-8"
    )
    alertmanager = (REPOSITORY_ROOT / "ops" / "monitoring" / "alertmanager.yml.example").read_text(
        encoding="utf-8"
    )

    assert "credentials_file:" in prometheus
    assert "REPLACE_WITH_API_HOST" in prometheus
    assert "job_name: gongkao-dazi-ready" in prometheus
    assert "https://REPLACE_WITH_API_HOST/ready" in prometheus
    assert "REPLACE_WITH_BLACKBOX_EXPORTER_HOST" in prometheus
    assert "REPLACE_WITH_APPROVED_HTTPS_ONCALL_WEBHOOK" in alertmanager
