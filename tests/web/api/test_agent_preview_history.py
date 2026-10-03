"""Builder history reuses owner-scoped tasks and their persisted transcript."""

import pytest

from xagent.web.models.chat_message import TaskChatMessage
from xagent.web.models.task import Task, TaskStatus

from .conftest import (
    _admin_headers,
    _direct_db_session,
    _register_second_user,
    client,
)

pytestmark = pytest.mark.usefixtures("_test_db")


def create_agent(headers, name="Preview agent"):
    response = client.post("/api/agents", headers=headers, json={"name": name})
    assert response.status_code == 200, response.text
    return response.json()


def create_preview(user_id, agent_id=None, **overrides):
    values = {
        "user_id": user_id,
        "title": "Sample",
        "description": "Original sample",
        "status": TaskStatus.COMPLETED,
        "is_visible": False,
        "agent_config": {
            "is_preview": True,
            "preview_agent_id": agent_id,
            "preview_config_key": "config-v1",
        },
    }
    values.update(overrides)
    with _direct_db_session() as db:
        task = Task(**values)
        db.add(task)
        db.flush()
        task_id = int(task.id)
        db.add(
            TaskChatMessage(
                task_id=task_id,
                user_id=user_id,
                role="user",
                message_type="message",
                content="Original sample",
                attachments=[
                    {"file_id": "sample-id", "name": "sample.csv", "size": 12}
                ],
            )
        )
        db.commit()
    return task_id


def test_latest_preview_restores_original_sample_not_followup():
    headers = _admin_headers()
    agent = create_agent(headers)
    url = f"/api/agents/{agent['id']}/preview-task"
    assert client.get(url, headers=headers).json() is None
    create_preview(agent["user_id"], agent["id"])
    latest = create_preview(agent["user_id"], agent["id"], status=TaskStatus.FAILED)
    with _direct_db_session() as db:
        db.add(
            TaskChatMessage(
                task_id=latest,
                user_id=agent["user_id"],
                role="user",
                message_type="message",
                content="Follow-up",
            )
        )
        db.commit()
    # Newer unrelated and visible tasks must not displace the latest preview.
    create_preview(agent["user_id"], agent["id"], is_visible=True)
    create_preview(agent["user_id"], agent["id"], agent_config={})
    response = client.get(url, headers=headers)
    assert response.status_code == 200
    assert response.json() == {
        "task_id": latest,
        "config_key": "config-v1",
        "message": "Original sample",
        "attachments": [{"file_id": "sample-id", "name": "sample.csv", "size": 12}],
    }


def test_link_presave_preview_without_changing_runtime_identity():
    headers = _admin_headers()
    agent = create_agent(headers)
    task_id = create_preview(agent["user_id"])
    url = f"/api/agents/{agent['id']}/preview-task"
    for _ in range(2):
        response = client.put(url, headers=headers, json={"task_id": task_id})
        assert response.status_code == 204, response.text
    assert client.get(url, headers=headers).json()["task_id"] == task_id
    with _direct_db_session() as db:
        task = db.get(Task, task_id)
        assert task.agent_id is None
        assert task.agent_config == {
            "is_preview": True,
            "preview_agent_id": None,
            "preview_config_key": "config-v1",
            "preview_history_agent_id": agent["id"],
        }
    other = create_agent(headers, "Other")
    assert (
        client.put(
            f"/api/agents/{other['id']}/preview-task",
            headers=headers,
            json={"task_id": task_id},
        ).status_code
        == 409
    )


@pytest.mark.parametrize("overrides", [{"is_visible": True}, {"agent_config": {}}])
def test_cannot_link_normal_tasks(overrides):
    headers = _admin_headers()
    agent = create_agent(headers)
    task_id = create_preview(agent["user_id"], **overrides)
    assert (
        client.put(
            f"/api/agents/{agent['id']}/preview-task",
            headers=headers,
            json={"task_id": task_id},
        ).status_code
        == 404
    )


def test_cross_user_tasks_are_private_even_for_admin():
    admin = _admin_headers()
    bob = _register_second_user()
    admin_agent = create_agent(admin)
    bob_agent = create_agent(bob)
    admin_task = create_preview(admin_agent["user_id"], admin_agent["id"])
    bob_task = create_preview(bob_agent["user_id"], admin_agent["id"])
    admin_url = f"/api/agents/{admin_agent['id']}/preview-task"
    bob_url = f"/api/agents/{bob_agent['id']}/preview-task"
    assert client.get(admin_url, headers=admin).json()["task_id"] == admin_task
    assert client.get(admin_url, headers=bob).status_code == 404
    assert client.get(bob_url, headers=admin).status_code == 404
    assert (
        client.put(
            admin_url,
            headers=admin,
            json={"task_id": bob_task},
        ).status_code
        == 404
    )


def test_legacy_preview_without_snapshot_is_not_current_config():
    headers = _admin_headers()
    agent = create_agent(headers)
    task_id = create_preview(
        agent["user_id"],
        agent_config={
            "is_preview": True,
            "preview_agent_id": agent["id"],
        },
    )
    response = client.get(f"/api/agents/{agent['id']}/preview-task", headers=headers)
    assert response.json()["task_id"] == task_id
    assert response.json()["config_key"] is None


def test_deleted_preview_is_not_returned():
    headers = _admin_headers()
    agent = create_agent(headers)
    task_id = create_preview(agent["user_id"], agent["id"])
    with _direct_db_session() as db:
        db.delete(db.get(Task, task_id))
        db.commit()
    assert (
        client.get(f"/api/agents/{agent['id']}/preview-task", headers=headers).json()
        is None
    )
