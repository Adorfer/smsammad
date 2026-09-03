from datetime import datetime, timedelta, timezone

from smsammad.sms_budget import GroupStat, SmsBudget


def _budget(tmp_path, max_per_hour=5, max_per_24h=10):
    return SmsBudget(tmp_path / "stats.db", max_per_hour, max_per_24h)


def test_fresh_budget_has_full_capacity(tmp_path):
    budget = _budget(tmp_path)
    assert budget.can_send(5)
    status = budget.status()
    assert (status.sent_last_hour, status.sent_last_24h) == (0, 0)


def test_record_sent_reduces_capacity(tmp_path):
    budget = _budget(tmp_path, max_per_hour=3, max_per_24h=10)
    budget.record_sent(3)
    assert not budget.can_send(1)
    assert budget.can_send(0)


def test_hourly_limit_blocks_before_daily_limit(tmp_path):
    budget = _budget(tmp_path, max_per_hour=2, max_per_24h=100)
    budget.record_sent(2)
    assert not budget.can_send(1)


def test_daily_limit_blocks_even_with_hourly_capacity(tmp_path):
    budget = _budget(tmp_path, max_per_hour=100, max_per_24h=2)
    budget.record_sent(2)
    assert not budget.can_send(1)


def test_old_entries_roll_out_of_hourly_window(tmp_path):
    budget = _budget(tmp_path, max_per_hour=2, max_per_24h=100)
    two_hours_ago = datetime.now(timezone.utc) - timedelta(hours=2)
    budget.record_sent(2, now=two_hours_ago)
    assert budget.can_send(2)


def test_old_entries_roll_out_of_24h_window(tmp_path):
    budget = _budget(tmp_path, max_per_hour=100, max_per_24h=2)
    yesterday = datetime.now(timezone.utc) - timedelta(hours=25)
    budget.record_sent(2, now=yesterday)
    assert budget.can_send(2)


def test_should_notify_true_on_fresh_state(tmp_path):
    budget = _budget(tmp_path)
    assert budget.should_notify(cooldown_minutes=60)


def test_should_notify_false_within_cooldown(tmp_path):
    budget = _budget(tmp_path)
    budget.mark_notified()
    assert not budget.should_notify(cooldown_minutes=60)


def test_should_notify_true_after_cooldown_elapsed(tmp_path):
    budget = _budget(tmp_path)
    long_ago = datetime.now(timezone.utc) - timedelta(hours=2)
    budget.mark_notified(now=long_ago)
    assert budget.should_notify(cooldown_minutes=60)


def test_state_persists_across_instances(tmp_path):
    path = tmp_path / "budget.json"
    SmsBudget(path, max_per_hour=5, max_per_24h=10).record_sent(3)
    reloaded = SmsBudget(path, max_per_hour=5, max_per_24h=10)
    assert reloaded.status().sent_last_hour == 3


def test_next_available_at_is_now_when_capacity_free(tmp_path):
    budget = _budget(tmp_path)
    now = datetime.now(timezone.utc)
    assert budget.next_available_at(1, now=now) == now


def test_next_available_at_waits_for_oldest_hourly_entry_to_expire(tmp_path):
    budget = _budget(tmp_path, max_per_hour=2, max_per_24h=100)
    now = datetime.now(timezone.utc)
    budget.record_sent(2, now=now)
    eta = budget.next_available_at(1, now=now)
    assert eta == now + timedelta(hours=1)


def test_next_available_at_uses_oldest_relevant_entry_with_staggered_sends(tmp_path):
    budget = _budget(tmp_path, max_per_hour=2, max_per_24h=100)
    t0 = datetime.now(timezone.utc)
    budget.record_sent(1, now=t0)
    budget.record_sent(1, now=t0 + timedelta(minutes=30))
    eta = budget.next_available_at(1, now=t0 + timedelta(minutes=45))
    assert eta == t0 + timedelta(hours=1)


def test_next_available_at_waits_for_daily_limit(tmp_path):
    budget = _budget(tmp_path, max_per_hour=100, max_per_24h=2)
    now = datetime.now(timezone.utc)
    budget.record_sent(2, now=now)
    eta = budget.next_available_at(1, now=now)
    assert eta == now + timedelta(hours=24)


def test_next_available_at_takes_the_later_of_both_constraints(tmp_path):
    budget = _budget(tmp_path, max_per_hour=1, max_per_24h=3)
    t0 = datetime.now(timezone.utc)
    budget.record_sent(1, now=t0 - timedelta(minutes=90))  # nur noch im 24h-Fenster
    budget.record_sent(1, now=t0)  # noch im Stunden-Fenster
    eta = budget.next_available_at(1, now=t0)
    # Stunden-Limit (1) ist durch den juengsten Eintrag (t0) blockiert -> t0+1h.
    # Tages-Limit (3) hat noch Luft (2 von 3 belegt) -> nicht bindend.
    assert eta == t0 + timedelta(hours=1)


def test_record_sent_with_group_agent_ticket_number(tmp_path):
    budget = _budget(tmp_path)
    now = datetime.now(timezone.utc)
    budget.record_sent(2, group="Users", agent="Andreas Dorfer", ticket_number="1001", now=now)

    rows = budget.summary_by_group_and_agent(now - timedelta(hours=1))
    assert rows == [GroupStat("out", "Users", "Andreas Dorfer", 2)]


def test_record_received_is_direction_in(tmp_path):
    budget = _budget(tmp_path)
    now = datetime.now(timezone.utc)
    budget.record_received(group="SMS-unbekannt", ticket_number="1002", now=now)

    # Eingehende SMS zaehlen NICHT gegen das Ausgehend-Budget (Default in
    # _budget(): max_per_hour=5).
    assert budget.can_send(5)

    rows = budget.summary_by_group(now - timedelta(hours=1))
    assert len(rows) == 1
    assert rows[0].direction == "in"
    assert rows[0].group_name == "SMS-unbekannt"
    assert rows[0].agent is None
    assert rows[0].count == 1


def test_summary_by_group_and_agent_groups_correctly(tmp_path):
    budget = _budget(tmp_path)
    now = datetime.now(timezone.utc)
    budget.record_sent(1, group="Users", agent="Alice", now=now)
    budget.record_sent(2, group="Users", agent="Bob", now=now)
    budget.record_sent(1, group="SMS-unbekannt", agent="Alice", now=now)

    rows = budget.summary_by_group_and_agent(now - timedelta(hours=1))
    as_dict = {(r.group_name, r.agent): r.count for r in rows}
    assert as_dict == {
        ("Users", "Alice"): 1,
        ("Users", "Bob"): 2,
        ("SMS-unbekannt", "Alice"): 1,
    }


def test_summary_by_group_and_agent_respects_since(tmp_path):
    budget = _budget(tmp_path)
    now = datetime.now(timezone.utc)
    old = now - timedelta(hours=25)
    budget.record_sent(1, group="Users", agent="Alice", now=old)
    budget.record_sent(1, group="Users", agent="Alice", now=now)

    rows = budget.summary_by_group_and_agent(now - timedelta(hours=1))
    assert len(rows) == 1
    assert rows[0].count == 1


def test_summary_by_group_combines_in_and_out_without_agent(tmp_path):
    budget = _budget(tmp_path)
    now = datetime.now(timezone.utc)
    budget.record_sent(2, group="Users", agent="Alice", now=now)
    budget.record_received(group="Users", now=now)
    budget.record_received(group="SMS-unbekannt", now=now)

    rows = budget.summary_by_group(now - timedelta(hours=1))
    as_dict = {(r.direction, r.group_name): r.count for r in rows}
    assert as_dict == {
        ("out", "Users"): 2,
        ("in", "Users"): 1,
        ("in", "SMS-unbekannt"): 1,
    }
    assert all(r.agent is None for r in rows)


def test_summary_by_group_empty_db_returns_empty_list(tmp_path):
    budget = _budget(tmp_path)
    now = datetime.now(timezone.utc)
    assert budget.summary_by_group(now - timedelta(days=7)) == []


def test_latest_balance_none_when_empty(tmp_path):
    budget = _budget(tmp_path)
    assert budget.latest_balance() is None


def test_record_balance_and_latest_balance(tmp_path):
    budget = _budget(tmp_path)
    now = datetime.now(timezone.utc)
    budget.record_balance(5.0, now=now - timedelta(hours=1))
    budget.record_balance(4.5, now=now)

    ts, amount = budget.latest_balance()
    assert amount == 4.5
    assert ts == now


def test_balance_history_since_is_sorted_ascending(tmp_path):
    budget = _budget(tmp_path)
    now = datetime.now(timezone.utc)
    budget.record_balance(5.0, now=now - timedelta(hours=2))
    budget.record_balance(4.5, now=now - timedelta(hours=1))
    budget.record_balance(4.0, now=now)

    history = budget.balance_history_since(now - timedelta(hours=3))
    assert [amount for _, amount in history] == [5.0, 4.5, 4.0]


def test_balance_history_since_respects_since(tmp_path):
    budget = _budget(tmp_path)
    now = datetime.now(timezone.utc)
    budget.record_balance(5.0, now=now - timedelta(days=10))
    budget.record_balance(4.0, now=now)

    history = budget.balance_history_since(now - timedelta(days=1))
    assert [amount for _, amount in history] == [4.0]


def test_should_query_balance_true_on_fresh_state(tmp_path):
    budget = _budget(tmp_path)
    assert budget.should_query_balance(interval_hours=24)


def test_should_query_balance_false_within_interval(tmp_path):
    budget = _budget(tmp_path)
    budget.mark_balance_queried()
    assert not budget.should_query_balance(interval_hours=24)


def test_should_query_balance_true_after_interval_elapsed(tmp_path):
    budget = _budget(tmp_path)
    long_ago = datetime.now(timezone.utc) - timedelta(hours=25)
    budget.mark_balance_queried(now=long_ago)
    assert budget.should_query_balance(interval_hours=24)


def test_access_blocked_until_none_when_never_failed(tmp_path):
    budget = _budget(tmp_path)
    assert budget.access_blocked_until("cgi") is None


def test_record_access_failure_blocks_for_first_stage(tmp_path):
    budget = _budget(tmp_path)
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    blocked_until = budget.record_access_failure("cgi", now=now)
    assert blocked_until == now + timedelta(hours=4)
    assert budget.access_blocked_until("cgi", now=now) == blocked_until


def test_record_access_failure_escalates_to_next_stage(tmp_path):
    budget = _budget(tmp_path)
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    budget.record_access_failure("cgi", now=now)
    later = now + timedelta(hours=5)  # nach Ablauf der ersten Sperre
    blocked_until = budget.record_access_failure("cgi", now=later)
    assert blocked_until == later + timedelta(hours=8)


def test_record_access_failure_caps_at_last_stage(tmp_path):
    budget = _budget(tmp_path)
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    budget.record_access_failure("cgi", now=now)  # level 1 -> 4h
    budget.record_access_failure("cgi", now=now)  # level 2 -> 8h
    budget.record_access_failure("cgi", now=now)  # level 3 -> 24h
    blocked_until = budget.record_access_failure("cgi", now=now)  # level 4 -> gedeckelt bei 24h
    assert blocked_until == now + timedelta(hours=24)


def test_access_blocked_until_none_after_block_expires(tmp_path):
    budget = _budget(tmp_path)
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    budget.record_access_failure("cgi", now=now)
    later = now + timedelta(hours=5)
    assert budget.access_blocked_until("cgi", now=later) is None


def test_access_state_is_independent_per_scope(tmp_path):
    budget = _budget(tmp_path)
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    budget.record_access_failure("cgi", now=now)
    assert budget.access_blocked_until("api", now=now) is None


def test_record_access_success_resets_state_and_signals_recovery(tmp_path):
    budget = _budget(tmp_path)
    budget.record_access_failure("cgi")
    assert budget.record_access_success("cgi") is True
    assert budget.access_blocked_until("cgi") is None
    # zweiter Erfolg ohne vorherigen Fehler -- keine erneute "Erholung"
    assert budget.record_access_success("cgi") is False


def test_record_access_success_false_when_never_failed(tmp_path):
    budget = _budget(tmp_path)
    assert budget.record_access_success("cgi") is False
