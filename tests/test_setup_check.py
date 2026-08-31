import pytest

from smsammad import setup_check
from smsammad.config import (
    BalanceConfig,
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

# "Alles in Ordnung"-Defaults, passend zu _config() unten -- Tests, die
# sich nur fuer Trigger ODER nur fuer Gruppen/Diagnose-Checks
# interessieren, muessen den jeweils anderen Teil so nicht extra
# mitschleppen, um nicht an unrelated "Problemen" zu scheitern.
DEFAULT_GROUPS = [{"id": 5, "name": "Users"}, {"id": 6, "name": "Triage"}]
DEFAULT_MY_USER = {"id": 6296, "group_ids": {"5": ["full"], "6": ["full"]}}
DEFAULT_STATES = [
    {"id": 2, "name": "open", "state_type_id": 2},
    {"id": 4, "name": "closed", "state_type_id": 5},
]
DEFAULT_PRIORITIES = [{"id": 3, "name": "3 high"}]
DEFAULT_USER_ATTRS = [
    {"object": "User", "name": "mobile", "active": True},
    {"object": "User", "name": "phone", "active": True},
]
DEFAULT_TOKENS = [
    {
        "id": 1,
        "name": "smsammad",
        "last_used_at": "2026-08-31T03:30:00.000Z",
        "preferences": {"permission": ["admin", "ticket.agent"]},
    },
    {
        "id": 2,
        "name": "irgendein-anderer-token",
        "last_used_at": "2026-01-01T00:00:00.000Z",
        "preferences": {"permission": ["report"]},
    },
]


class FakeZammad:
    def __init__(
        self,
        triggers=None,
        groups=None,
        my_user=None,
        states=None,
        priorities=None,
        user_attrs=None,
        tokens=None,
        raise_on_triggers=False,
        raise_on_my_user=False,
        raise_on_update=False,
        raise_on_groups=False,
        raise_on_states=False,
        raise_on_priorities=False,
        raise_on_user_attrs=False,
        raise_on_tokens=False,
    ):
        self._triggers = triggers or []
        self._groups = DEFAULT_GROUPS if groups is None else groups
        self._my_user = DEFAULT_MY_USER if my_user is None else my_user
        self._states = DEFAULT_STATES if states is None else states
        self._priorities = DEFAULT_PRIORITIES if priorities is None else priorities
        self._user_attrs = DEFAULT_USER_ATTRS if user_attrs is None else user_attrs
        self._tokens = DEFAULT_TOKENS if tokens is None else tokens
        self._raise_on_triggers = raise_on_triggers
        self._raise_on_my_user = raise_on_my_user
        self._raise_on_update = raise_on_update
        self._raise_on_groups = raise_on_groups
        self._raise_on_states = raise_on_states
        self._raise_on_priorities = raise_on_priorities
        self._raise_on_user_attrs = raise_on_user_attrs
        self._raise_on_tokens = raise_on_tokens
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
        if self._raise_on_groups:
            raise ZammadError("GET groups -> HTTP 403: forbidden")
        return self._groups

    def get_my_user(self):
        if self._raise_on_my_user:
            raise ZammadError("GET users/me -> HTTP 403: forbidden")
        return self._my_user

    def update_user(self, user_id, **fields):
        if self._raise_on_update:
            raise ZammadError(f"PUT users/{user_id} -> HTTP 403: forbidden")
        self.updated_users.append((user_id, fields))

    def list_ticket_states(self):
        if self._raise_on_states:
            raise ZammadError("GET ticket_states -> HTTP 403: forbidden")
        return self._states

    def list_ticket_priorities(self):
        if self._raise_on_priorities:
            raise ZammadError("GET ticket_priorities -> HTTP 403: forbidden")
        return self._priorities

    def list_user_attributes(self):
        if self._raise_on_user_attrs:
            raise ZammadError("GET object_manager_attributes -> HTTP 403: forbidden")
        return self._user_attrs

    def list_my_tokens(self):
        if self._raise_on_tokens:
            raise ZammadError("GET user_access_token -> HTTP 403: forbidden")
        return self._tokens


def _config(
    self_manage_setup=True,
    group="Users",
    new_customer_group="Triage",
    open_state_id=2,
    overflow_priority=3,
    phone_field="mobile",
    phone_field_fallback="phone",
    balance=None,
):
    return Config(
        teltonika=TeltonikaConfig(
            host="h", username="u", password="p", default_country_code="DE"
        ),
        zammad=ZammadConfig(
            url="https://z",
            token="t",
            group=group,
            new_customer_group=new_customer_group,
            phone_field=phone_field,
            overflow_priority=overflow_priority,
            phone_field_fallback=phone_field_fallback,
            open_state_id=open_state_id,
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
        balance=balance,
    )


def _balance_config(closed_state_id=4):
    return BalanceConfig(
        warn_threshold_eur=5.0,
        alarm_threshold_eur=1.0,
        closed_state_id=closed_state_id,
    )


# --- self_manage_setup Schalter --------------------------------------------


def test_disabled_by_default_does_nothing():
    zammad = FakeZammad(triggers=[])
    setup_check.run(zammad, _config(self_manage_setup=False), fix=True, dry_run=False)

    assert zammad.created_triggers == []
    assert zammad.updated_users == []


# --- Trigger-Check -----------------------------------------------------------


def test_all_ok_does_not_raise():
    zammad = FakeZammad(triggers=[MATCHING_TRIGGER])
    setup_check.run(zammad, _config(), fix=True, dry_run=False)

    assert zammad.created_triggers == []


def test_inactive_trigger_does_not_count():
    inactive = {**MATCHING_TRIGGER, "active": False}
    zammad = FakeZammad(triggers=[inactive])
    # Trigger fehlt, wird per --fix erfolgreich angelegt -> kein
    # verbleibendes Problem, kein raise (alles andere ist per Default ok).
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


def test_missing_trigger_without_fix_reports_problem_and_does_not_create():
    zammad = FakeZammad(triggers=[])
    with pytest.raises(RuntimeError):
        setup_check.run(zammad, _config(), fix=False, dry_run=False)

    assert zammad.created_triggers == []


def test_missing_trigger_with_fix_creates_it_and_no_longer_raises():
    zammad = FakeZammad(triggers=[])
    # Fix behebt das einzige Problem (kein Guthaben-Abschnitt konfiguriert,
    # alles andere per Default "in Ordnung") -> kein verbleibendes Problem,
    # also KEIN raise.
    setup_check.run(zammad, _config(), fix=True, dry_run=False)

    assert len(zammad.created_triggers) == 1
    payload = zammad.created_triggers[0]
    assert payload["active"] is True
    assert payload["perform"]["ticket.tags"] == {"operator": "add", "value": "sms-out"}


def test_missing_trigger_with_fix_and_dry_run_does_not_create_or_raise():
    zammad = FakeZammad(triggers=[])
    # dry_run: nie ein raise, auch wenn Probleme (noch) offen sind.
    setup_check.run(zammad, _config(), fix=True, dry_run=True)

    assert zammad.created_triggers == []


def test_trigger_permission_error_is_reported_and_not_double_created():
    zammad = FakeZammad(raise_on_triggers=True)
    with pytest.raises(RuntimeError):
        setup_check.run(zammad, _config(), fix=True, dry_run=False)

    assert zammad.created_triggers == []


# --- Gruppenzugriff-Check ------------------------------------------------


def test_group_access_already_full_no_update():
    zammad = FakeZammad(triggers=[MATCHING_TRIGGER])
    setup_check.run(zammad, _config(), fix=True, dry_run=False)

    assert zammad.updated_users == []


def test_group_access_missing_with_fix_grants_full_access_and_no_longer_raises():
    zammad = FakeZammad(
        triggers=[MATCHING_TRIGGER],
        groups=DEFAULT_GROUPS,
        my_user={"id": 6296, "group_ids": {}},
    )
    setup_check.run(zammad, _config(), fix=True, dry_run=False)

    assert len(zammad.updated_users) == 2
    user_id, fields = zammad.updated_users[0]
    assert user_id == 6296
    assert fields["group_ids"]["5"] == ["full"]


def test_group_access_missing_without_fix_reports_problem():
    zammad = FakeZammad(
        triggers=[MATCHING_TRIGGER],
        groups=DEFAULT_GROUPS,
        my_user={"id": 6296, "group_ids": {}},
    )
    with pytest.raises(RuntimeError):
        setup_check.run(zammad, _config(), fix=False, dry_run=False)

    assert zammad.updated_users == []


def test_group_access_missing_with_fix_and_dry_run_does_not_grant_or_raise():
    zammad = FakeZammad(
        triggers=[MATCHING_TRIGGER],
        groups=DEFAULT_GROUPS,
        my_user={"id": 6296, "group_ids": {}},
    )
    setup_check.run(zammad, _config(), fix=True, dry_run=True)

    assert zammad.updated_users == []


def test_nonexistent_group_is_not_created():
    zammad = FakeZammad(
        triggers=[MATCHING_TRIGGER], groups=[], my_user={"id": 6296, "group_ids": {}}
    )
    with pytest.raises(RuntimeError):
        setup_check.run(zammad, _config(), fix=True, dry_run=False)

    assert zammad.updated_users == []


def test_nonexistent_nested_group_suggests_full_path(caplog):
    zammad = FakeZammad(
        triggers=[MATCHING_TRIGGER],
        groups=[{"id": 14, "name": "Neanderfunk::Neanderfunk NIC"}],
        my_user={"id": 6296, "group_ids": {}},
    )
    with caplog.at_level("WARNING"), pytest.raises(RuntimeError):
        setup_check.run(
            zammad,
            _config(group="Neanderfunk NIC", new_customer_group="Neanderfunk NIC"),
            fix=True,
            dry_run=False,
        )

    assert "Neanderfunk::Neanderfunk NIC" in caplog.text
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


def test_group_list_permission_error_is_reported():
    zammad = FakeZammad(triggers=[MATCHING_TRIGGER], raise_on_groups=True)
    with pytest.raises(RuntimeError):
        setup_check.run(zammad, _config(), fix=True, dry_run=False)

    assert zammad.updated_users == []


def test_group_my_user_permission_error_is_reported():
    zammad = FakeZammad(
        triggers=[MATCHING_TRIGGER], groups=DEFAULT_GROUPS, raise_on_my_user=True
    )
    with pytest.raises(RuntimeError):
        setup_check.run(zammad, _config(), fix=True, dry_run=False)

    assert zammad.updated_users == []


def test_grant_permission_error_is_reported_not_swallowed():
    zammad = FakeZammad(
        triggers=[MATCHING_TRIGGER],
        groups=DEFAULT_GROUPS,
        my_user={"id": 6296, "group_ids": {}},
        raise_on_update=True,
    )
    with pytest.raises(RuntimeError):
        setup_check.run(zammad, _config(), fix=True, dry_run=False)


# --- Diagnose-Checks (rein lesend, kein --fix) --------------------------


def test_wrong_open_state_id_reports_problem():
    zammad = FakeZammad(triggers=[MATCHING_TRIGGER])
    with pytest.raises(RuntimeError) as exc_info:
        setup_check.run(zammad, _config(open_state_id=4), fix=True, dry_run=False)

    assert "open_state_id" in str(exc_info.value)


def test_open_state_id_pointing_at_closed_state_reports_problem():
    """id=4 existiert (siehe DEFAULT_STATES), ist aber semantisch 'closed'
    -- open_state_id darauf zu setzen waere ein Konfigurationsfehler."""
    zammad = FakeZammad(triggers=[MATCHING_TRIGGER])
    with pytest.raises(RuntimeError) as exc_info:
        setup_check.run(zammad, _config(open_state_id=4), fix=True, dry_run=False)

    assert "semantisch" in str(exc_info.value) or "open_state_id" in str(exc_info.value)


def test_nonexistent_open_state_id_reports_problem():
    zammad = FakeZammad(triggers=[MATCHING_TRIGGER])
    with pytest.raises(RuntimeError):
        setup_check.run(zammad, _config(open_state_id=999), fix=True, dry_run=False)


def test_closed_state_id_only_checked_when_balance_configured():
    zammad = FakeZammad(triggers=[MATCHING_TRIGGER])
    # Kein balance= gesetzt -> closed_state_id wird gar nicht geprueft.
    setup_check.run(zammad, _config(), fix=True, dry_run=False)


def test_wrong_closed_state_id_reports_problem_when_balance_configured():
    zammad = FakeZammad(triggers=[MATCHING_TRIGGER])
    with pytest.raises(RuntimeError) as exc_info:
        setup_check.run(
            zammad,
            _config(balance=_balance_config(closed_state_id=999)),
            fix=True,
            dry_run=False,
        )

    assert "closed_state_id" in str(exc_info.value)


def test_correct_closed_state_id_does_not_raise():
    zammad = FakeZammad(triggers=[MATCHING_TRIGGER])
    setup_check.run(
        zammad, _config(balance=_balance_config(closed_state_id=4)), fix=True, dry_run=False
    )


def test_nonexistent_overflow_priority_reports_problem():
    zammad = FakeZammad(triggers=[MATCHING_TRIGGER])
    with pytest.raises(RuntimeError) as exc_info:
        setup_check.run(zammad, _config(overflow_priority=999), fix=True, dry_run=False)

    assert "overflow_priority" in str(exc_info.value)


def test_nonexistent_phone_field_reports_problem():
    zammad = FakeZammad(triggers=[MATCHING_TRIGGER])
    with pytest.raises(RuntimeError) as exc_info:
        setup_check.run(zammad, _config(phone_field="handynummer"), fix=True, dry_run=False)

    assert "phone_field" in str(exc_info.value)


def test_inactive_phone_field_reports_problem():
    zammad = FakeZammad(
        triggers=[MATCHING_TRIGGER],
        user_attrs=[{"object": "User", "name": "mobile", "active": False}],
    )
    with pytest.raises(RuntimeError):
        setup_check.run(zammad, _config(), fix=True, dry_run=False)


def test_nonexistent_phone_field_fallback_reports_problem():
    zammad = FakeZammad(triggers=[MATCHING_TRIGGER])
    with pytest.raises(RuntimeError) as exc_info:
        setup_check.run(
            zammad, _config(phone_field_fallback="festnetz"), fix=True, dry_run=False
        )

    assert "phone_field_fallback" in str(exc_info.value)


def test_states_permission_error_reports_problem():
    zammad = FakeZammad(triggers=[MATCHING_TRIGGER], raise_on_states=True)
    with pytest.raises(RuntimeError):
        setup_check.run(zammad, _config(), fix=True, dry_run=False)


def test_priorities_permission_error_reports_problem():
    zammad = FakeZammad(triggers=[MATCHING_TRIGGER], raise_on_priorities=True)
    with pytest.raises(RuntimeError):
        setup_check.run(zammad, _config(), fix=True, dry_run=False)


def test_user_attrs_permission_error_reports_problem():
    zammad = FakeZammad(triggers=[MATCHING_TRIGGER], raise_on_user_attrs=True)
    with pytest.raises(RuntimeError):
        setup_check.run(zammad, _config(), fix=True, dry_run=False)


# --- Token-Scope-Check (Live entdeckt: Token traegt eigenen, vom User
# unabhaengigen Berechtigungs-Scope, siehe zammad.list_my_tokens) -------


def test_token_without_ticket_agent_reports_problem():
    tokens = [
        {
            "id": 1,
            "name": "eingeschraenkter-token",
            "last_used_at": "2026-08-31T03:30:00.000Z",
            "preferences": {"permission": ["admin", "report"]},
        }
    ]
    zammad = FakeZammad(triggers=[MATCHING_TRIGGER], tokens=tokens)
    with pytest.raises(RuntimeError) as exc_info:
        setup_check.run(zammad, _config(), fix=True, dry_run=False)

    assert "ticket.agent" in str(exc_info.value)
    assert "eingeschraenkter-token" in str(exc_info.value)


def test_admin_permission_alone_does_not_satisfy_ticket_agent():
    """Live verifiziert (Zammad-Quellcode lib/auth/permissions.rb):
    'admin' und 'ticket.agent' sind unabhaengige Top-Level-Scopes, 'admin'
    deckt 'ticket.agent' NICHT automatisch mit ab."""
    tokens = [
        {
            "id": 1,
            "name": "nur-admin",
            "last_used_at": "2026-08-31T03:30:00.000Z",
            "preferences": {"permission": ["admin"]},
        }
    ]
    zammad = FakeZammad(triggers=[MATCHING_TRIGGER], tokens=tokens)
    with pytest.raises(RuntimeError):
        setup_check.run(zammad, _config(), fix=True, dry_run=False)


def test_token_with_ticket_agent_does_not_raise():
    tokens = [
        {
            "id": 1,
            "name": "korrekter-token",
            "last_used_at": "2026-08-31T03:30:00.000Z",
            "preferences": {"permission": ["ticket.agent"]},
        }
    ]
    zammad = FakeZammad(triggers=[MATCHING_TRIGGER], tokens=tokens)
    setup_check.run(zammad, _config(), fix=True, dry_run=False)


def test_token_with_parent_ticket_permission_satisfies_check():
    tokens = [
        {
            "id": 1,
            "name": "eltern-scope",
            "last_used_at": "2026-08-31T03:30:00.000Z",
            "preferences": {"permission": ["ticket"]},
        }
    ]
    zammad = FakeZammad(triggers=[MATCHING_TRIGGER], tokens=tokens)
    setup_check.run(zammad, _config(), fix=True, dry_run=False)


def test_token_with_wildcard_permission_satisfies_check():
    tokens = [
        {
            "id": 1,
            "name": "wildcard-scope",
            "last_used_at": "2026-08-31T03:30:00.000Z",
            "preferences": {"permission": ["ticket.*"]},
        }
    ]
    zammad = FakeZammad(triggers=[MATCHING_TRIGGER], tokens=tokens)
    setup_check.run(zammad, _config(), fix=True, dry_run=False)


def test_picks_most_recently_used_token_among_several():
    tokens = [
        {
            "id": 1,
            "name": "alt-und-ohne-rechte",
            "last_used_at": "2020-01-01T00:00:00.000Z",
            "preferences": {"permission": []},
        },
        {
            "id": 2,
            "name": "aktuell-verwendet",
            "last_used_at": "2026-08-31T03:30:00.000Z",
            "preferences": {"permission": ["ticket.agent"]},
        },
    ]
    zammad = FakeZammad(triggers=[MATCHING_TRIGGER], tokens=tokens)
    # Der Token mit dem juengsten last_used_at hat 'ticket.agent' -> kein
    # Problem, obwohl ein ANDERER (aelterer) Token keine Rechte hat.
    setup_check.run(zammad, _config(), fix=True, dry_run=False)


def test_ambiguous_last_used_at_is_not_checkable():
    """Zwei Tokens mit IDENTISCHEM juengstem Zeitstempel -- lieber 'nicht
    pruefbar' berichten als den falschen zu raten."""
    tokens = [
        {
            "id": 1,
            "name": "token-a",
            "last_used_at": "2026-08-31T03:30:00.000Z",
            "preferences": {"permission": ["ticket.agent"]},
        },
        {
            "id": 2,
            "name": "token-b",
            "last_used_at": "2026-08-31T03:30:00.000Z",
            "preferences": {"permission": []},
        },
    ]
    zammad = FakeZammad(triggers=[MATCHING_TRIGGER], tokens=tokens)
    with pytest.raises(RuntimeError) as exc_info:
        setup_check.run(zammad, _config(), fix=True, dry_run=False)

    assert "nicht eindeutig identifizierbar" in str(exc_info.value)


def test_no_tokens_with_timestamp_is_not_checkable():
    tokens = [{"id": 1, "name": "nie-benutzt", "preferences": {"permission": ["ticket.agent"]}}]
    zammad = FakeZammad(triggers=[MATCHING_TRIGGER], tokens=tokens)
    with pytest.raises(RuntimeError):
        setup_check.run(zammad, _config(), fix=True, dry_run=False)


def test_token_list_permission_error_reports_problem():
    zammad = FakeZammad(triggers=[MATCHING_TRIGGER], raise_on_tokens=True)
    with pytest.raises(RuntimeError):
        setup_check.run(zammad, _config(), fix=True, dry_run=False)


def test_diagnose_checks_never_raise_in_dry_run():
    zammad = FakeZammad(
        triggers=[MATCHING_TRIGGER],
        raise_on_states=True,
        raise_on_priorities=True,
        raise_on_user_attrs=True,
        raise_on_tokens=True,
    )
    setup_check.run(zammad, _config(open_state_id=999), fix=True, dry_run=True)
