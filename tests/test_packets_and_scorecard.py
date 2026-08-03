from datetime import datetime, timedelta, timezone

from banks.packets import (
    DecisionPacket,
    aging_action_queue,
    apply_defaults_past_deadline,
    create_packet,
    mark_answered,
)
from banks.scorecard import count_reds, render_weekly_scorecard
from banks.store import cursor


def test_packet_lifecycle_answered_vs_completed_are_distinct(db_path):
    packet = DecisionPacket(
        kind="vendor_dispatch",
        decision="Send plumber for the leak at 123 Main?",
        recommendation="Yes — dispatch today",
        default_if_unanswered="Dispatch today",
        dollar_impact_cents=15000,
    )
    packet_id = create_packet(db_path, packet)

    queue = aging_action_queue(db_path)
    assert queue == []  # not yet answered, not in the aging queue

    mark_answered(db_path, packet_id)

    queue = aging_action_queue(db_path)
    assert len(queue) == 1
    assert queue[0]["id"] == packet_id  # answered but not completed = ages here


def test_reversible_packet_applies_default_past_deadline(db_path):
    past_deadline = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    packet = DecisionPacket(
        kind="subscription_review",
        decision="Keep or kill the streaming subscription?",
        recommendation="Kill — unused 90 days",
        default_if_unanswered="Kill",
        deadline=past_deadline,
        reversible=True,
    )
    packet_id = create_packet(db_path, packet)

    defaulted = apply_defaults_past_deadline(db_path)

    assert packet_id in defaulted


def test_weekly_scorecard_renders_seeded_row_and_flags_reds(db_path):
    with cursor(db_path) as cur:
        cur.execute(
            """
            INSERT INTO scorecard_weekly
                (week_ending, occupancy_pct, vacancy_days, inquiries_answered_under_1h_pct,
                 applications_from_inquiries_pct, collections_on_time_pct, bills_on_time_pct,
                 reviews_requested, reviews_received, money_found_cents)
            VALUES ('2026-08-07', 88.0, 3, 92.0, 35.0, 90.0, 100.0, 2, 1, 5000)
            """
        )

    lines = render_weekly_scorecard(db_path, "2026-08-07")

    occupancy_line = next(l for l in lines if l.label == "Occupancy")
    assert occupancy_line.red is True  # 88% < 100% target

    reds = count_reds(lines)
    assert reds >= 4  # occupancy, inquiries<1h, applications, collections all below target
