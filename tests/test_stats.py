"""Per-user generation analytics (`GET /stats`)."""

from __future__ import annotations

PASSWORD = "correct-horse-battery-staple-42"


def _add_generation(app, *, user_id: int, module: str = "ansible.builtin.debug", valid: bool = True):
    from models import Generation, db

    with app.app_context():
        row = Generation(
            user_id=user_id,
            request="demo request",
            module=module,
            filename="demo.yml",
            playbook="- hosts: all\n  tasks: []\n",
            is_valid=valid,
            warnings=1 if valid else 0,
            errors=0 if valid else 2,
        )
        db.session.add(row)
        db.session.commit()
        return row.id


def test_stats_are_scoped_to_current_user(app, client, make_user, login):
    alice = make_user("alice@example.com", PASSWORD)
    bob = make_user("bob@example.com", PASSWORD)
    _add_generation(app, user_id=alice, module="amazon.aws.ec2_instance", valid=True)
    _add_generation(app, user_id=alice, module="kubernetes.core.k8s", valid=False)
    _add_generation(app, user_id=bob, module="azure.azcollection.azure_rm_aks", valid=True)

    login("alice@example.com", PASSWORD)
    resp = client.get("/stats")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["total"] == 2
    assert body["valid"] == 1
    assert body["invalid"] == 1
    assert body["warns"] == 1
    modules = {row["module"]: row["count"] for row in body["modules"]}
    assert modules == {"amazon.aws.ec2_instance": 1, "kubernetes.core.k8s": 1}


def test_stats_do_not_leak_across_admins(app, client, make_user, login):
    admin_a = make_user("admin-a@example.com", PASSWORD, role="admin")
    admin_b = make_user("admin-b@example.com", PASSWORD, role="admin")
    _add_generation(app, user_id=admin_a)
    _add_generation(app, user_id=admin_b)
    _add_generation(app, user_id=admin_b)

    login("admin-a@example.com", PASSWORD)
    body = client.get("/stats").get_json()
    assert body["total"] == 1

    login("admin-b@example.com", PASSWORD)
    body = client.get("/stats").get_json()
    assert body["total"] == 2
