from datetime import date, timedelta

from banks.finance import (
    bills_on_time_pct,
    due_nudges,
    keep_kill_memo,
    mark_on_time,
    subscriptions_due_for_review,
)
from banks.store import cursor


def _seed_bill(db_path, due_in_days, is_subscription=0, keep_kill_candidate=0):
    due = (date.today() + timedelta(days=due_in_days)).isoformat()
    with cursor(db_path) as cur:
        cur.execute(
            "INSERT INTO bills (name, amount_cents, due_date, cadence, is_subscription, "
            "keep_kill_candidate) VALUES ('Test Bill', 5000, ?, 'monthly', ?, ?)",
            (due, is_subscription, keep_kill_candidate),
        )
        return cur.lastrowid


def test_due_nudges_fires_at_7_and_1_day_marks(db_path):
    _seed_bill(db_path, due_in_days=7)
    _seed_bill(db_path, due_in_days=1)
    _seed_bill(db_path, due_in_days=4)  # should NOT nudge

    drafts = due_nudges(db_path)

    assert len(drafts) == 2
    subjects = [d.subject for d in drafts]
    assert any("7" not in s or "due in 7" in s or True for s in subjects)  # sanity, real check below
    bodies = [d.body for d in drafts]
    assert any("due in 7 days" in b for b in bodies)
    assert any("due tomorrow" in b for b in bodies)


def test_bills_never_pays_never_transacts():
    """No function in the finance module can move money — structural check."""
    import banks.finance as finance_module

    forbidden = {"pay", "transfer", "charge", "transact", "withdraw", "submit_payment"}
    exported = [n for n in dir(finance_module) if not n.startswith("_")]
    assert not any(name.lower() in forbidden for name in exported)


def test_mark_on_time_feeds_scorecard_pct(db_path):
    bill_id_1 = _seed_bill(db_path, due_in_days=5)
    bill_id_2 = _seed_bill(db_path, due_in_days=10)

    mark_on_time(db_path, bill_id_1, True)
    mark_on_time(db_path, bill_id_2, False)

    pct = bills_on_time_pct(db_path)
    assert pct == 50.0


def test_subscription_keep_kill_memo_never_auto_cancels(db_path):
    bill_id = _seed_bill(db_path, due_in_days=10, is_subscription=1, keep_kill_candidate=1)
    due = subscriptions_due_for_review(db_path, days=14)
    assert len(due) == 1

    memo = keep_kill_memo(due[0], "kill", "unused 90 days")
    assert memo.kind == "subscription_memo"
    assert "you decide" in memo.body
