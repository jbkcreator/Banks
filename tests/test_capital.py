from banks.capital import CapitalCandidate, all_candidates_carry_review_flag, candidate_memo, record_candidate


def test_cap_rate_and_cash_on_cash_math():
    candidate = CapitalCandidate(
        title="Duplex on Elm St",
        purchase_price_cents=20_000_000,   # $200,000
        annual_income_cents=2_400_000,      # $24,000
        annual_expenses_cents=800_000,      # $8,000
        cash_invested_cents=5_000_000,      # $50,000
        hold_period_months=60,
    )

    assert candidate.noi_cents == 1_600_000
    assert round(candidate.cap_rate_pct, 2) == 8.0
    assert round(candidate.cash_on_cash_pct, 2) == 32.0


def test_candidate_memo_always_flags_professional_review():
    candidate = CapitalCandidate(
        title="Note portfolio",
        purchase_price_cents=10_000_000,
        annual_income_cents=1_000_000,
        annual_expenses_cents=100_000,
        cash_invested_cents=10_000_000,
        hold_period_months=36,
    )

    memo = candidate_memo(candidate)

    assert "Professional review required" in memo.body
    assert "SDIRA" in memo.body
    assert memo.detailed_financial is True  # never posted inline to Slack


def test_every_recorded_candidate_carries_review_flag(db_path):
    candidate = CapitalCandidate(
        title="Test deal",
        purchase_price_cents=10_000_000,
        annual_income_cents=1_000_000,
        annual_expenses_cents=200_000,
        cash_invested_cents=3_000_000,
        hold_period_months=48,
    )
    record_candidate(db_path, candidate)

    assert all_candidates_carry_review_flag(db_path) is True
