from __future__ import annotations

from fastapi.testclient import TestClient


def claim_task_edit_headers(
    client: TestClient,
    auth: dict[str, str],
    *,
    resource_type: str,
    resource_id: str,
    revision: int,
    device_suffix: str = "primary",
) -> dict[str, str]:
    response = client.post(
        "/api/v2/task-editing/leases",
        headers=auth,
        json={
            "resourceType": resource_type,
            "resourceId": resource_id,
            "deviceId": f"test-device-000000000000-{device_suffix}",
            "deviceLabel": f"测试设备 {device_suffix}",
            "localBaseRevision": revision,
            "hasLocalChanges": False,
        },
    )
    assert response.status_code == 200, response.text
    lease = response.json()["data"]
    assert lease["state"] == "owned"
    return {
        "X-Task-Edit-Lease": lease["leaseId"],
        "X-Task-Edit-Generation": str(lease["generation"]),
    }
