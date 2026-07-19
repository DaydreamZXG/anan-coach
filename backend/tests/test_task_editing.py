from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from tests.test_mock_exam_calibration import _make_client, _seed_mock_qbank, _seed_user


def test_task_editing_requires_explicit_takeover_and_conflict_choice(
    db_session_factory: sessionmaker[Session],
) -> None:
    _seed_user(db_session_factory, token="task-editing-token")
    _seed_mock_qbank(db_session_factory)
    client = _make_client(db_session_factory)
    auth = {"Authorization": "Bearer task-editing-token"}
    exam = client.post("/api/v1/mock-exams/weekly", headers=auth).json()
    resource_id = exam["sessionId"]
    question = exam["questions"][0]

    missing_lease = client.put(
        f"/api/v1/mock-exams/{resource_id}/checkpoint",
        headers={**auth, "Idempotency-Key": "task-edit-missing-lease"},
        json=_checkpoint(question, revision=1, draft="不应被写入"),
    )
    assert missing_lease.status_code == 409
    assert missing_lease.json()["code"] == "task_edit_lease_required"

    first = _claim(
        client,
        auth,
        resource_id=resource_id,
        device_id="device-a-0000000000000000000001",
        device_label="iPhone A",
        local_base_revision=1,
        has_local_changes=False,
    )
    assert first["state"] == "owned"
    first_headers = _lease_headers(auth, first)
    first_write = client.put(
        f"/api/v1/mock-exams/{resource_id}/checkpoint",
        headers={**first_headers, "Idempotency-Key": "task-edit-first-write"},
        json=_checkpoint(question, revision=1, draft="设备 A 已同步"),
    )
    assert first_write.status_code == 200, first_write.text
    assert first_write.json()["revision"] == 2

    second_view = _claim(
        client,
        auth,
        resource_id=resource_id,
        device_id="device-b-0000000000000000000002",
        device_label="iPad B",
        local_base_revision=1,
        has_local_changes=True,
    )
    assert second_view["state"] == "takeover_required"
    assert second_view["deviceLabel"] == "iPhone A"

    takeover_response = client.post(
        f"/api/v2/task-editing/leases/mock_exam_session/{resource_id}/takeover",
        headers=auth,
        json={
            "expectedLeaseId": second_view["leaseId"],
            "expectedGeneration": second_view["generation"],
            "deviceId": "device-b-0000000000000000000002",
            "deviceLabel": "iPad B",
            "localBaseRevision": 1,
            "hasLocalChanges": True,
        },
    )
    assert takeover_response.status_code == 200, takeover_response.text
    takeover = takeover_response.json()["data"]
    assert takeover["state"] == "conflict_choice_required"
    assert takeover["generation"] == first["generation"] + 1
    second_headers = _lease_headers(auth, takeover)

    blocked_before_choice = client.put(
        f"/api/v1/mock-exams/{resource_id}/checkpoint",
        headers={**second_headers, "Idempotency-Key": "task-edit-blocked-write"},
        json=_checkpoint(question, revision=2, draft="尚未选择"),
    )
    assert blocked_before_choice.status_code == 409
    assert blocked_before_choice.json()["code"] == "task_edit_choice_required"

    resolution_response = client.post(
        f"/api/v2/task-editing/leases/mock_exam_session/{resource_id}/resolve",
        headers=auth,
        json={
            "expectedLeaseId": takeover["leaseId"],
            "expectedGeneration": takeover["generation"],
            "expectedServerRevision": takeover["serverRevision"],
            "choice": "use_this_device",
        },
    )
    assert resolution_response.status_code == 200, resolution_response.text
    resolved = resolution_response.json()["data"]
    assert resolved["state"] == "owned"

    rejected_old_device = client.put(
        f"/api/v1/mock-exams/{resource_id}/checkpoint",
        headers={**first_headers, "Idempotency-Key": "task-edit-old-device-write"},
        json=_checkpoint(question, revision=2, draft="旧设备不能覆盖"),
    )
    assert rejected_old_device.status_code == 409
    assert rejected_old_device.json()["code"] == "task_edit_owned_elsewhere"

    accepted_new_device = client.put(
        f"/api/v1/mock-exams/{resource_id}/checkpoint",
        headers={
            **_lease_headers(auth, resolved),
            "Idempotency-Key": "task-edit-new-write",
        },
        json=_checkpoint(question, revision=2, draft="保留 iPad 草稿"),
    )
    assert accepted_new_device.status_code == 200, accepted_new_device.text
    assert accepted_new_device.json()["checkpoint"]["answers"][0]["draftText"] == (
        "保留 iPad 草稿"
    )

    with db_session_factory() as session:
        event_types = list(
            session.execute(
                text(
                    "select event_type from learning.task_edit_events "
                    "where resource_id = cast(:resource_id as uuid) order by created_at, event_id"
                ),
                {"resource_id": resource_id},
            ).scalars()
        )
    assert "taken_over" in event_types
    assert "conflict_detected" in event_types
    assert "conflict_resolved" in event_types
    assert event_types.count("write_authorized") == 2


def _claim(
    client: TestClient,
    auth: dict[str, str],
    *,
    resource_id: str,
    device_id: str,
    device_label: str,
    local_base_revision: int,
    has_local_changes: bool,
) -> dict:
    response = client.post(
        "/api/v2/task-editing/leases",
        headers=auth,
        json={
            "resourceType": "mock_exam_session",
            "resourceId": resource_id,
            "deviceId": device_id,
            "deviceLabel": device_label,
            "localBaseRevision": local_base_revision,
            "hasLocalChanges": has_local_changes,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _lease_headers(auth: dict[str, str], lease: dict) -> dict[str, str]:
    return {
        **auth,
        "X-Task-Edit-Lease": lease["leaseId"],
        "X-Task-Edit-Generation": str(lease["generation"]),
    }


def _checkpoint(question: dict, *, revision: int, draft: str) -> dict:
    return {
        "expectedRevision": revision,
        "currentQuestionId": question["questionId"],
        "answers": [
            {
                "questionId": question["questionId"],
                "selectedAnswer": "A",
                "eliminatedOptionIds": [],
                "marked": False,
                "draftText": draft,
            }
        ],
    }
