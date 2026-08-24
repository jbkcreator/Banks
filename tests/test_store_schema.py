from banks.store import cursor


def test_all_expected_tables_exist(db_path):
    expected = {
        "rooms", "inquiries", "maintenance_tickets", "vendors", "bills",
        "opportunities", "capital_candidates", "decision_packets", "promises",
        "scorecard_weekly", "job_runs", "fact_freshness",
    }
    with cursor(db_path) as cur:
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        actual = {row["name"] for row in cur.fetchall()}
    assert expected.issubset(actual)


def test_rooms_table_accepts_seeded_row(db_path):
    with cursor(db_path) as cur:
        cur.execute(
            """
            INSERT INTO rooms (property_address, unit_label, rented_by_room,
                                current_rent_cents, occupied, updated_at)
            VALUES ('123 Main St', 'Room 3', 1, 90000, 0, '2026-08-01T00:00:00')
            """
        )
        cur.execute("SELECT COUNT(*) AS n FROM rooms")
        assert cur.fetchone()["n"] == 1
