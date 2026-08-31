import pytest

from smsammad import setup_check
from smsammad.config import (
    Config,
    TeltonikaConfig,
    TicketToSmsConfig,
    ZammadConfig,
)
from smsammad.zammad import ZammadError

MATCHING_TRIGGER = {
    "id": 14,
    "name": "SMS-Out",
    "active": True,
    "condition": {
        "article.action": {"operator": "is", "value": "create"},
        "article.type_id": {"operator": "is", "value": ["5"]},
        "article.internal": {"operator": "is", "value": ["false"]},
        "article.sender_id": {"operator": "is", "value": ["1"]},
    },
    "perform": {"ticket.tags": {"operator": "add", "value": "sms-out"}},
}


class FakeZammad:
    def __init__(
        self,
        triggers=None,
        groups=None,
        my_user=None,
        raise_on_triggers=False,
        raise_on_my_user=False,
        raise_on_update=False,
    ):
        self._triggers = triggers or []
        self._groups = groups or []
        self._my_user = my_user or {"id": 6296, "group_ids": {}}
        self._raise_on_triggers = raise_on_triggers
        self._raise_on_my_user = raise_on_my_user
        self._raise_on_update = raise_on_update
        self.created_triggers = []
        self.updated_users = []

    def list_triggers(self):
        if self._raise_on_triggers:
            raise ZammadError("GET triggers -> HTTP 403: forbidden")
        return self._triggers

    def create_trigger(self, payload):
        self.created_triggers.append(payload)
        return {"id": 99, "name": payload["name"]}

    def list_groups(self):
        return self._groups

    def get_my_user(self):
        if self._raise_on_my_user:
            raise ZammadError("GET users/me -> HTTP 403: forbidden")
        return self._my_user

    def update_user(self, user_id, **fields):
        if self._raise_on_update:
            raise ZammadError(f"PUT users/{user_id} -> HTTP 403: forbidden")
        self.updated_users.append((user_id, fields))


def _config(self_manage_setup=True, group="Users", new_customer_group="Triage"):
    return Config(
        teltonika=TeltonikaConfig(
            host="h", username="u", password="p", default_country_code="DE"
        ),
        zammad=ZammadConfig(
            url="https://z",
            token="t",
            group=group,
            new_customer_group=new_customer_group,
            phone_field="mobile",
            overflow_priority=3,
            self_manage_setup=self_manage_setup,
        ),
        ticket_to_sms=TicketToSmsConfig(
            max_sms_parts=3,
            max_sms_per_hour=20,
            max_sms_per_24h=100,
            stats_db_file="/nonexistent/should-not-be-used.json",
            budget_notify_cooldown_minutes=60,
        ),
        notification=None,
    )


def test_disabled_by_default_does_nothing():
    zammad = FakeZammad(triggers=[])
    setup_check.run(zammad, _config(self_manage_setup=False), fix=True, dry_run=False)

    assert zammad.created_triggers == []
    assert zammad.updated_users == []


def test_matching_trigger_found_no_create_attempted():
    zammad = FakeZammad(triggers=[MATCHING_TRIGGER])
    setup_check.run(zammad, _config(), fix=True, dry_run=False)

    assert zammad.created_triggers == []


def test_inactive_trigger_does_not_count():
    inactive = {**MATCHING_TRIGGER, "active": False}
    zammad = FakeZammad(triggers=[inactive])
    setup_check.run(zammad, _config(), fix=True, dry_run=False)

    assert len(zammad.created_triggers) == 1


def test_trigger_with_wrong_tag_does_not_count():
    wrong_tag = {
        **MATCHING_TRIGGER,
        "perform": {"ticket.tags": {"operator": "add", "value": "sms-out-wrong"}},
    }
    zammad = FakeZammad(triggers=[wrong_tag])
    setup_check.run(zammad, _config(), fix=True, dry_run=False)

    assert len(zammad.created_triggers) == 1


def test_trigger_with_remove_operator_does_not_count():
    remove_op = {
        **MATCHING_TRIGGER,
        "perform": {"ticket.tags": {"operator": "remove", "value": "sms-out"}},
    }
    zammad = FakeZammad(triggers=[remove_op])
    setup_check.run(zammad, _config(), fix=True, dry_run=False)

    assert len(zammad.created_triggers) == 1


def test_missing_trigger_without_fix_does_not_create():
    zammad = FakeZammad(triggers=[])
    setup_check.run(zammad, _config(), fix=False, dry_run=False)

    assert zammad.created_triggers == []


def test_missing_trigger_with_fix_creates_it():
    zammad = FakeZammad(triggers=[])
    setup_check.run(zammad, _config(), fix=True, dry_run=False)

    assert len(zammad.created_triggers) == 1
    payload = zammad.created_triggers[0]
    assert payload["active"] is True
    assert payload["perform"]["ticket.tags"] == {"operator": "add", "value": "sms-out"}


def test_missing_trigger_with_fix_and_dry_run_does_not_create():
    zammad = FakeZammad(triggers=[])
    setup_check.run(zammad, _config(), fix=True, dry_run=True)

    assert zammad.created_triggers == []


def test_trigger_permission_error_does_not_crash():
    zammad = FakeZammad(raise_on_triggers=True)
    setup_check.run(zammad, _config(), fix=True, dry_run=False)

    assert zammad.created_triggers == []


def test_group_access_already_full_no_update():
    zammad = FakeZammad(
        triggers=[MATCHING_TRIGGER],
        groups=[{"id": 5, "name": "Users"}, {"id": 6, "name": "Triage"}],
        my_user={"id": 6296, "group_ids": {"5": ["full"], "6": ["full"]}},
    )
    setup_check.run(zammad, _config(), fix=True, dry_run=False)

    assert zammad.updated_users == []


def test_group_access_missing_with_fix_grants_full_access():
    zammad = FakeZammad(
        triggers=[MATCHING_TRIGGER],
        groups=[{"id": 5, "name": "Users"}, {"id": 6, "name": "Triage"}],
        my_user={"id": 6296, "group_ids": {}},
    )
    setup_check.run(zammad, _config(), fix=True, dry_run=False)

    assert len(zammad.updated_users) == 2
    user_id, fields = zammad.updated_users[0]
    assert user_id == 6296
    assert fields["group_ids"]["5"] == ["full"]


def test_group_access_missing_without_fix_does_not_grant():
    zammad = FakeZammad(
        triggers=[MATCHING_TRIGGER],
        groups=[{"id": 5, "name": "Users"}, {"id": 6, "name": "Triage"}],
        my_user={"id": 6296, "group_ids": {}},
    )
    setup_check.run(zammad, _config(), fix=False, dry_run=False)

    assert zammad.updated_users == []


def test_group_access_missing_with_fix_and_dry_run_does_not_grant():
    zammad = FakeZammad(
        triggers=[MATCHING_TRIGGER],
        groups=[{"id": 5, "name": "Users"}, {"id": 6, "name": "Triage"}],
        my_user={"id": 6296, "group_ids": {}},
    )
    setup_check.run(zammad, _config(), fix=True, dry_run=True)

    assert zammad.updated_users == []


def test_nonexistent_group_is_not_created():
    zammad = FakeZammad(triggers=[MATCHING_TRIGGER], groups=[], my_user={"id": 6296, "group_ids": {}})
    setup_check.run(zammad, _config(), fix=True, dry_run=False)

    assert zammad.updated_users == []


def test_same_group_and_new_customer_group_checked_only_once():
    zammad = FakeZammad(
        triggers=[MATCHING_TRIGGER],
        groups=[{"id": 5, "name": "Users"}],
        my_user={"id": 6296, "group_ids": {}},
    )
    setup_check.run(
        zammad, _config(group="Users", new_customer_group="Users"), fix=True, dry_run=False
    )

    assert len(zammad.updated_users) == 1


def test_group_permission_error_does_not_crash():
    zammad = FakeZammad(
        triggers=[MATCHING_TRIGGER],
        groups=[{"id": 5, "name": "Users"}],
        raise_on_my_user=True,
    )
    setup_check.run(zammad, _config(), fix=True, dry_run=False)

    assert zammad.updated_users == []


def test_grant_permission_error_does_not_crash():
    zammad = FakeZammad(
        triggers=[MATCHING_TRIGGER],
        groups=[{"id": 5, "name": "Users"}],
        my_user={"id": 6296, "group_ids": {}},
        raise_on_update=True,
    )
    # Sollte weder crashen noch eine Exception nach aussen durchlassen.
    setup_check.run(zammad, _config(), fix=True, dry_run=False)
