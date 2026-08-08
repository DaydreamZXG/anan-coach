from __future__ import annotations

from dataclasses import dataclass
from typing import Final


class JobConsumerPrincipalContractError(RuntimeError):
    """A deployed consumer is not bound to one database login principal."""


@dataclass(frozen=True, slots=True)
class JobConsumerPrincipal:
    database_principal: str
    group_role: str
    consumer_unit: str
    consumer_entrypoint: str


JOB_CONSUMER_PRINCIPALS: Final = (
    JobConsumerPrincipal(
        database_principal="gongkao_worker_heartbeat_data_rights_login",
        group_role="gongkao_worker_heartbeat_data_rights",
        consumer_unit="gongkao-dazi-data-rights-worker",
        consumer_entrypoint="app.workers.data_rights",
    ),
    JobConsumerPrincipal(
        database_principal="gongkao_worker_heartbeat_cause_agent_login",
        group_role="gongkao_worker_heartbeat_cause_agent",
        consumer_unit="gongkao-dazi-cause-agent-worker",
        consumer_entrypoint="app.workers.xingce_cause_agent",
    ),
    JobConsumerPrincipal(
        database_principal="gongkao_worker_heartbeat_external_learning_login",
        group_role="gongkao_worker_heartbeat_external_learning",
        consumer_unit="gongkao-dazi-external-learning-worker",
        consumer_entrypoint="app.workers.external_learning",
    ),
    JobConsumerPrincipal(
        database_principal="gongkao_worker_heartbeat_essay_review_login",
        group_role="gongkao_worker_heartbeat_essay_review",
        consumer_unit="gongkao-dazi-essay-review-worker",
        consumer_entrypoint="app.workers.essay_reviews",
    ),
    JobConsumerPrincipal(
        database_principal="gongkao_worker_heartbeat_coach_notification_login",
        group_role="gongkao_worker_heartbeat_coach_notification",
        consumer_unit="gongkao-dazi-coach-notification-worker",
        consumer_entrypoint="app.workers.coach_notifications",
    ),
    JobConsumerPrincipal(
        database_principal="gongkao_worker_heartbeat_ilink_login",
        group_role="gongkao_worker_heartbeat_ilink",
        consumer_unit="gongkao-dazi-ilink-object-deletion-worker",
        consumer_entrypoint="app.workers.ilink_object_deletions",
    ),
)


def heartbeat_database_principal(
    *,
    consumer_unit: str,
    consumer_entrypoint: str,
) -> str:
    matches = tuple(
        binding.database_principal
        for binding in JOB_CONSUMER_PRINCIPALS
        if binding.consumer_unit == consumer_unit
        and binding.consumer_entrypoint == consumer_entrypoint
    )
    if len(matches) != 1:
        raise JobConsumerPrincipalContractError(
            "consumer heartbeat identity must have one database principal"
        )
    return matches[0]
