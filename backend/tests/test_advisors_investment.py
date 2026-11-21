"""Investment agent: the shared surplus (free cash) and term deposits that pass the buffer test."""
from datetime import date
from decimal import Decimal

import pytest

import service
from advisors import investment
from advisors.model import ActionType, Agent
from domain import DashboardQuery, EventType, RiskAssessment, RiskStatus
from fakes import expense
from service import build_workspace
from texts import build_insights, floor_to_surplus_step, surplus_eur

YEAR_END = date(2025, 12, 31)


def _workspace(repo, as_of=YEAR_END, horizon=60, buffer="3000"):
    return build_workspace(repo, DashboardQuery(as_of, horizon, Decimal(buffer)))


def _risk(status: RiskStatus, min_balance: str) -> RiskAssessment:
    return RiskAssessment(status=status, min_balance_eur=Decimal(min_balance), min_balance_date=date(2025, 7, 1),
                          floor_eur=Decimal("0"), credit_limit_eur=None, days_until_min=10, risk_window=None)


# --------------------------------------------------------------------------- the shared surplus

@pytest.mark.parametrize("balance, lowest, buffer, expected", [
    ("33266.35", "30430.81", "3000", "27000"),  # the forecast minimum decides
    ("12540.34", "13534.46", "3000", "9000"),  # today's balance decides
    ("20000", "25000", "0", "20000"),
    ("3999.99", "9000", "3000", "0"),  # rounded down to whole thousands
    ("2000", "9000", "3000", "0"),  # never negative
])
def test_surplus_is_the_lower_balance_less_the_buffer_rounded_down(balance, lowest, buffer, expected):
    assert surplus_eur(_risk(RiskStatus.GREEN, lowest), Decimal(balance), Decimal(buffer)) == Decimal(expected)


@pytest.mark.parametrize("status", [RiskStatus.YELLOW, RiskStatus.RED])
def test_no_surplus_unless_green(status):
    assert surplus_eur(_risk(status, "50000"), Decimal("60000"), Decimal("3000")) == 0


def test_surplus_ignores_a_credit_line():
    """A negative balance that is fine thanks to a credit line is still no free cash."""
    risk = RiskAssessment(status=RiskStatus.GREEN, min_balance_eur=Decimal("-2000"), min_balance_date=YEAR_END,
                          floor_eur=Decimal("-20000"), credit_limit_eur=Decimal("20000"), days_until_min=10,
                          risk_window=None)
    assert surplus_eur(risk, Decimal("30000"), Decimal("3000")) == 0


def test_floor_to_surplus_step():
    assert floor_to_surplus_step(Decimal("12150.35")) == Decimal("12000")
    assert floor_to_surplus_step(Decimal("999.99")) == 0 == floor_to_surplus_step(Decimal("-0.01"))


def test_insight_is_unchanged_and_uses_the_shared_surplus(repo):
    dashboard = service.build_dashboard(repo, DashboardQuery(YEAR_END, 60, Decimal("3000")))
    [insight] = dashboard.insights
    assert insight.kind == "surplus" and insight.title == "Cash you could put to work"
    assert insight.amount_eur == Decimal("27000") == surplus_eur(dashboard.risk, dashboard.current_balance_eur,
                                                                 dashboard.buffer_eur)
    assert insight.detail == (
        "You have €33,266 in the account and the balance is projected to stay above €30,431 for the next 60 days. "
        "About €27,000 is not needed for day-to-day payments or your safety buffer — consider a short-term deposit.")


def test_insight_keeps_its_ten_thousand_threshold():
    green = _risk(RiskStatus.GREEN, "12999")
    assert surplus_eur(green, Decimal("20000"), Decimal("3000")) == Decimal("9000")
    assert build_insights(green, Decimal("20000"), 60, Decimal("3000")) == ()
    [insight] = build_insights(_risk(RiskStatus.GREEN, "13000"), Decimal("20000"), 60, Decimal("3000"))
    assert insight.amount_eur == Decimal("10000")
    assert build_insights(_risk(RiskStatus.YELLOW, "50000"), Decimal("60000"), 60, Decimal("3000")) == ()


def test_free_cash_on_the_real_statement(repo):
    assert investment.free_cash(_workspace(repo)) == Decimal("27000")
    assert investment.free_cash(_workspace(repo, buffer="8000")) == Decimal("22000")
    assert investment.free_cash(_workspace(repo, date(2025, 10, 15))) == Decimal("9000")
    assert investment.free_cash(_workspace(repo, date(2025, 5, 10))) == 0  # RED


# --------------------------------------------------------------------------- term deposits

def test_one_candidate_per_term_longest_first(repo):
    workspace = _workspace(repo)
    candidates = investment.deposit_candidates(workspace, Decimal("12000"))
    assert [(candidate.date_from, candidate.date_to, candidate.interest_eur) for candidate in candidates] == [
        (date(2026, 1, 1), date(2026, 7, 1), Decimal("119.01")),  # 12,000 * 2% * 181 / 365
        (date(2026, 1, 1), date(2026, 4, 1), Decimal("59.18")),  # 90 days
        (date(2026, 1, 1), date(2026, 2, 1), Decimal("20.38")),  # 31 days
    ]
    for candidate in candidates:
        [event] = candidate.events_to_add
        assert (candidate.id, candidate.agent, candidate.action_type) == (
            "I1", Agent.INVESTMENT, ActionType.TERM_DEPOSIT)
        assert (event.event_type, event.category, event.amount_eur) == (EventType.TERM_DEPOSIT, "investment", 12000)
        assert candidate.new_status is RiskStatus.GREEN
        assert candidate.new_min_balance_eur == workspace.dashboard.risk.min_balance_eur - Decimal("12000")
    assert len({candidate.fingerprint for candidate in candidates}) == 3


def test_deposit_within_the_horizon_shows_the_interest_in_the_forecast(repo):
    workspace = _workspace(repo)
    one_month = investment.deposit_candidates(workspace, Decimal("12000"))[-1]
    assert one_month.forecast[-1].balance_eur == workspace.dashboard.forecast.days[-1].balance_eur + Decimal("20.38")


def test_buffer_test_rejects_a_deposit_larger_than_the_free_cash(repo):
    workspace = _workspace(repo)
    assert len(investment.deposit_candidates(workspace, Decimal("27000"))) == 3
    assert investment.deposit_candidates(workspace, Decimal("28000")) == ()


def test_buffer_test_is_made_per_term(repo):
    """A large bill in six weeks: only the deposit that is back in time keeps the buffer."""
    repo.add_event(expense(date(2026, 2, 14), "45000", "New kitchen"))
    workspace = _workspace(repo, horizon=90)
    assert workspace.dashboard.risk.status is RiskStatus.GREEN
    candidates = investment.deposit_candidates(workspace, Decimal("10000"))
    assert [candidate.date_to for candidate in candidates] == [date(2026, 2, 1)]


def test_deposit_texts(repo):
    longest = investment.deposit_candidates(_workspace(repo), Decimal("12000"))[0]
    assert longest.title == "Investment deposit of €12,000"
    assert longest.description == (
        "About €12,000 is not needed for day-to-day payments or your €3,000 safety buffer in the next 60 days. "
        "Put it into a 6-month term deposit with Commerzbank: it earns €119 interest and is back in the account "
        "on 1 July.")
    assert longest.effect == ("€12,000 leaves the account on 1 January and comes back on 1 July with €119 "
                              "interest. Lowest projected balance: €18,431 on 1 January.")
    assert longest.assumptions == (
        "Interest at 2% p.a. (assumed rate, not a bank quote).",
        "Deposit terms of 1, 3, 6 months are assumed and the longest is shown. The forecast only covers the next "
        "60 days: whether you can spare the money until 1 July is not checked.")
    assert longest.simulated_actions == (
        "Investment: 6-month term deposit of €12,000 with Commerzbank opened on 1 January, maturing on 1 July "
        "(simulated).",)


def test_assumption_says_how_far_the_buffer_test_reaches(repo):
    """Only a deposit that matures inside the forecast was checked for its whole term."""
    one_month, six_months = (investment.deposit_candidates(_workspace(repo, horizon=90), Decimal("10000"))[index]
                             for index in (-1, 0))
    assert one_month.assumptions[-1].endswith("The forecast only covers the next 90 days, which includes the whole "
                                              "term.")
    assert six_months.assumptions[-1].endswith("The forecast only covers the next 90 days: whether you can spare "
                                               "the money until 1 July is not checked.")
    assert not [text for deposit in (one_month, six_months) for text in deposit.assumptions
                if "keeps your safety buffer" in text]


def test_a_zero_buffer_is_not_mentioned(repo):
    workspace = _workspace(repo, date(2025, 12, 8), horizon=30, buffer="0")
    deposit = investment.deposit_candidates(workspace, Decimal("11000"))[0]
    assert deposit.description.startswith("About €11,000 is not needed for day-to-day payments in the next 30 days. ")
    assert "safety buffer" not in deposit.description


def test_facts_document_holds_formatted_strings(repo):
    facts = investment.facts_document(_workspace(repo), claimed_first_eur=Decimal("14849.65"),
                                      deposit_eur=Decimal("12000"), no_deposit_reason=None)
    assert facts == {
        "balance_today": "€33,266",
        "lowest_projected_balance": "€30,431",
        "lowest_projected_balance_date": "2026-01-01",
        "safety_buffer": "€3,000",
        "forecast_days": 60,
        "free_cash_already_proposed_for_tax_reserve_or_chf_purchase": "€14,850",
        "free_cash_left_for_a_deposit": "€12,000",
        "assumed_interest_rate_per_year": "2%",
        "deposit_terms_months": [1, 3, 6],
    }


def test_facts_document_says_why_no_deposit_is_proposed(repo):
    facts = investment.facts_document(_workspace(repo), claimed_first_eur=Decimal("17000"), deposit_eur=Decimal("0"),
                                      no_deposit_reason="Too little is left.")
    assert facts["free_cash_left_for_a_deposit"] == "€0"
    assert facts["why_no_deposit_is_proposed"] == "Too little is left."
