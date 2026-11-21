"""FX agent: the remaining CHF instalments to Zurich, the rates paid so far and the two ways to fix the cost."""
from datetime import date
from decimal import Decimal

import pytest

from advisors import fx
from advisors.model import ActionType, Agent
from domain import DashboardQuery, DecisionStatus, EventDraft, EventType, LedgerEntry
from service import build_workspace


def _workspace(repo, as_of, horizon=60, buffer="3000"):
    return build_workspace(repo, DashboardQuery(as_of, horizon, Decimal(buffer)))


def _facts(repo, as_of, **kwargs):
    return fx.assess(_workspace(repo, as_of, **kwargs), {})


def _deferral(due: date, new_due: date) -> EventDraft:
    return EventDraft(event_type=EventType.PAYMENT_DEFERRAL, date_from=due, date_to=new_due,
                      amount_eur=Decimal("4358.34"), category="investment_outflow", description="Transfer postponed")


def _prebuy(purchase: date, last_instalment: date) -> EventDraft:
    return EventDraft(event_type=EventType.FX_PREBUY, date_from=purchase, date_to=last_instalment,
                      amount_eur=Decimal("8716.67"), description="Swiss francs bought in advance")


# --------------------------------------------------------------------------- facts

@pytest.mark.parametrize("as_of, count, first, chf, last, lowest, highest, average, exposure, swing", [
    (date(2025, 1, 15), 11, date(2025, 2, 15), "45833.37", "1.042", "1.042", "1.042", "1.0420", "47758.37", "0.00"),
    (date(2025, 2, 20), 10, date(2025, 3, 15), "41666.70", "1.045", "1.042", "1.045", "1.0435", "43541.70", "125.00"),
    (date(2025, 5, 10), 8, date(2025, 5, 15), "33333.36", "1.040", "1.038", "1.045", "1.0413", "34666.69", "233.33"),
    (date(2025, 10, 15), 2, date(2025, 11, 15), "8333.34", "1.046", "1.038", "1.050", "1.0432", "8716.67", "100.00"),
    (date(2025, 11, 20), 1, date(2025, 12, 15), "4166.67", "1.044", "1.038", "1.050", "1.0433", "4350.00", "50.00"),
])
def test_facts_on_the_real_statement(repo, as_of, count, first, chf, last, lowest, highest, average, exposure,
                                     swing):
    facts = _facts(repo, as_of)
    assert facts.remaining_count == count
    assert (facts.remaining_dates[0], facts.last_date) == (first, date(2025, 12, 15))
    assert facts.instalment_chf == Decimal("4166.67") and facts.remaining_chf == Decimal(chf)
    assert (facts.last_rate, facts.min_rate, facts.max_rate) == (Decimal(last), Decimal(lowest), Decimal(highest))
    assert facts.average_rate == Decimal(average)
    assert facts.exposure_eur == Decimal(exposure)  # remaining CHF * last rate
    assert facts.sensitivity_eur == Decimal(swing)  # remaining CHF * (highest - lowest rate)
    assert facts.sensitivity_is_small is (swing != "0.00")  # one paid rate (15 January) is no range to call small


def test_remaining_dates_are_the_fifteenth_of_each_month(repo):
    facts = _facts(repo, date(2025, 10, 15))
    assert facts.remaining_dates == (date(2025, 11, 15), date(2025, 12, 15))
    assert facts.forward_is_worthwhile
    assert not _facts(repo, date(2025, 11, 20)).forward_is_worthwhile


def test_instalments_beyond_the_forecast_horizon_still_count(repo):
    assert _facts(repo, date(2025, 5, 10), horizon=30).remaining_count == 8


def test_a_postponed_instalment_is_still_remaining_on_its_new_date(repo):
    repo.add_event(_deferral(date(2025, 11, 15), date(2026, 1, 15)))
    facts = _facts(repo, date(2025, 10, 15))
    assert facts.remaining_dates == (date(2025, 12, 15), date(2026, 1, 15))
    assert facts.exposure_eur == Decimal("8716.67")


def test_a_postponement_beyond_the_forecast_horizon_is_seen(repo):
    # the 15 June instalment falls outside a 30-day forecast from 10 May, and so does its postponement
    repo.add_event(_deferral(date(2025, 6, 15), date(2025, 8, 15)))
    for horizon in (30, 60, 90):
        facts = _facts(repo, date(2025, 5, 10), horizon=horizon)
        assert facts.remaining_count == 8
        assert facts.remaining_dates[:4] == (date(2025, 5, 15), date(2025, 7, 15), date(2025, 8, 15),
                                             date(2025, 8, 15))


@pytest.mark.parametrize("as_of, paid, remaining", [
    (date(2025, 5, 10), 4, 8),   # both postponed instalments are still to come
    (date(2025, 5, 20), 5, 7),   # the statement shows the 15 May instalment as paid: it is not remaining too
    (date(2025, 6, 20), 6, 6),
    (date(2025, 7, 14), 6, 6),
    (date(2025, 7, 20), 7, 5),
    (date(2025, 8, 20), 8, 4),
])
def test_a_postponed_instalment_the_statement_shows_as_paid_is_not_counted_twice(repo, as_of, paid, remaining):
    repo.add_event(_deferral(date(2025, 5, 15), date(2025, 7, 15)))
    repo.add_event(_deferral(date(2025, 6, 15), date(2025, 8, 15)))
    workspace = _workspace(repo, as_of)
    booked = [entry for entry in workspace.ledger if entry.category == "investment_outflow"]
    facts = fx.assess(workspace, {})
    assert (len(booked), facts.remaining_count) == (paid, remaining)
    assert len(booked) + facts.remaining_count == 12  # never more than the 12 instalments of the commitment
    assert facts.remaining_chf == Decimal("4166.67") * remaining


def test_rates_seen_later_are_not_used(repo):
    # the highest rate of the year (1.050) is paid on 15 June
    assert _facts(repo, date(2025, 6, 14)).max_rate == Decimal("1.048")
    assert _facts(repo, date(2025, 6, 15)).max_rate == Decimal("1.050")


def test_swing_above_the_threshold_is_not_called_small():
    entries = [
        LedgerEntry(date(2025, 1, 15), Decimal("-40000"), "investment_outflow", "Luigi",
                    "Capital Tranche 01/12 (40000.00 CHF @ 1.000 EUR/CHF)"),
        LedgerEntry(date(2025, 2, 15), Decimal("-42000"), "investment_outflow", "Luigi",
                    "Capital Tranche 02/12 (40000.00 CHF @ 1.050 EUR/CHF)"),
    ]
    facts = fx.fx_facts(entries, date(2025, 2, 20), ())
    assert facts.remaining_count == 10 and facts.sensitivity_eur == Decimal("20000.00")
    assert not facts.sensitivity_is_small


# --------------------------------------------------------------------------- silence

def test_silent_before_the_first_transfer(repo):
    assert _facts(repo, date(2025, 1, 10)) is None


def test_silent_once_the_last_instalment_is_paid(repo):
    assert _facts(repo, date(2025, 12, 14)).remaining_count == 1
    assert _facts(repo, date(2025, 12, 15)) is None
    assert _facts(repo, date(2025, 12, 31)) is None


def test_silent_while_an_approved_purchase_covers_the_instalments(repo):
    repo.add_event(_prebuy(date(2025, 10, 16), date(2025, 12, 15)))
    assert _facts(repo, date(2025, 10, 15)) is None
    assert _facts(repo, date(2025, 11, 20)) is None


def test_silent_after_the_rate_lock_was_approved_but_not_after_a_rejection(repo):
    workspace = _workspace(repo, date(2025, 10, 15))
    fingerprint = fx.forward(workspace, fx.assess(workspace, {})).fingerprint
    assert fx.assess(workspace, {fingerprint: DecisionStatus.APPROVED}) is None
    assert fx.assess(workspace, {fingerprint: DecisionStatus.REJECTED}) is not None
    # one instalment later the same instalments are still the ones that were locked
    assert fx.assess(_workspace(repo, date(2025, 11, 20)), {fingerprint: DecisionStatus.APPROVED}) is None


def test_postponing_an_instalment_keeps_the_approved_rate_lock(repo):
    workspace = _workspace(repo, date(2025, 10, 15))
    fingerprint = fx.forward(workspace, fx.assess(workspace, {})).fingerprint
    repo.add_event(_deferral(date(2025, 11, 15), date(2026, 1, 15)))
    postponed = _workspace(repo, date(2025, 10, 15))
    assert fx.assess(postponed, {}).last_date == date(2026, 1, 15)
    assert fx.forward(postponed, fx.assess(postponed, {})).fingerprint == fingerprint
    assert fx.assess(postponed, {fingerprint: DecisionStatus.APPROVED}) is None


def test_silent_when_the_transfers_carry_no_rate():
    entries = [LedgerEntry(date(2025, 1, 15), Decimal("-4350"), "investment_outflow", "Luigi",
                           "Capital Tranche 01/12")]
    assert fx.fx_facts(entries, date(2025, 1, 20), ()) is None


# --------------------------------------------------------------------------- proposals and texts

def test_prebuy_buys_all_remaining_francs_tomorrow_at_the_last_rate(repo):
    workspace = _workspace(repo, date(2025, 10, 15), horizon=90)
    proposal = fx.prebuy(workspace, fx.assess(workspace, {}))
    [event] = proposal.events_to_add
    assert (proposal.id, proposal.agent, proposal.action_type) == ("F1", Agent.FX, ActionType.FX_PREBUY)
    assert (event.event_type, event.date_from, event.date_to) == (
        EventType.FX_PREBUY, date(2025, 10, 16), date(2025, 12, 15))
    assert event.amount_eur == proposal.amount_eur == Decimal("8716.67")
    assert (proposal.risk_removed_eur, proposal.interest_eur) == (Decimal("100.00"), None)
    assert not [item for day in proposal.forecast for item in day.items if item.category == "investment_outflow"]
    baseline = {day.date: day.balance_eur for day in workspace.dashboard.forecast.days}
    assert proposal.forecast[0].balance_eur == baseline[date(2025, 10, 16)] - Decimal("8716.67")
    # both instalments (2 x 4,358.34) are gone; buying all the francs at once is one cent cheaper
    assert proposal.forecast[-1].balance_eur == baseline[proposal.forecast[-1].date] + Decimal("0.01")
    assert proposal.simulated_actions == (
        "Order placed with Commerzbank: buy 8,333.34 CHF for €8,717 on 16 October (simulated).",)


def test_forward_changes_no_balance_and_creates_no_event(repo):
    workspace = _workspace(repo, date(2025, 10, 15))
    proposal = fx.forward(workspace, fx.assess(workspace, {}))
    assert (proposal.id, proposal.action_type, proposal.events_to_add) == ("F2", ActionType.FX_FORWARD, ())
    assert (proposal.amount_eur, proposal.date_from, proposal.date_to) == (None, None, date(2025, 12, 15))
    assert proposal.risk_removed_eur == Decimal("100.00")
    assert proposal.scenario.forecast == workspace.dashboard.forecast
    assert proposal.assumptions == (
        "The locked rate is assumed to equal the last rate you paid, 1.046 EUR/CHF (assumed rate, not a bank "
        "quote).",)
    assert proposal.simulated_actions == (
        "Forward contract request sent to Commerzbank: 8,333.34 CHF at 1.046 EUR/CHF (simulated).",)


def test_texts_say_honestly_that_the_swing_is_small_and_stay_in_their_lane(repo):
    workspace = _workspace(repo, date(2025, 10, 15))
    facts = fx.assess(workspace, {})
    for proposal in (fx.prebuy(workspace, facts), fx.forward(workspace, facts)):
        assert "To be honest, the possible swing is small" in proposal.description
        assert "about €100" in proposal.description
        text = " ".join([proposal.title, proposal.description, proposal.effect, *proposal.assumptions,
                         *proposal.simulated_actions]).lower()
        for forbidden in ("fee", "spread", "usd", "dollar", "pound", "expect", "forecast the rate", "will rise",
                          "will fall", "commission"):
            assert forbidden not in text


def test_one_paid_rate_is_no_history_and_no_swing_is_claimed(repo):
    workspace = _workspace(repo, date(2025, 1, 17))
    facts = fx.assess(workspace, {})
    assert (facts.observed_rates, facts.has_rate_history, facts.risk_removed_eur) == (1, False, None)
    for proposal in (fx.prebuy(workspace, facts), fx.forward(workspace, facts)):
        assert proposal.risk_removed_eur is None
        assert "Only one transfer has been paid so far, so there is no rate history yet" in proposal.description
        assert "no longer depends on the exchange rate" in proposal.effect
        for text in (proposal.description, proposal.effect):
            assert "€0" not in text and "swing is small" not in text and "swing disappears" not in text
    document = fx.facts_document(facts)
    assert document["rate_history"].startswith("Only one transfer has been paid so far")
    assert not {"possible_swing", "possible_swing_is_small", "lowest_rate_paid_so_far",
                "highest_rate_paid_so_far", "average_rate_paid_so_far"} & set(document)


def test_the_second_paid_rate_starts_the_history(repo):
    facts = _facts(repo, date(2025, 2, 20))
    assert (facts.observed_rates, facts.has_rate_history) == (2, True)
    assert facts.risk_removed_eur == facts.sensitivity_eur == Decimal("125.00")
    assert "rate_history" not in fx.facts_document(facts)


def test_single_remaining_transfer_reads_naturally(repo):
    workspace = _workspace(repo, date(2025, 11, 20))
    proposal = fx.prebuy(workspace, fx.assess(workspace, {}))
    assert proposal.description.startswith("One transfer to Zurich is still to come: 4,166.67 CHF on 15 December.")
    assert "the remaining transfer no longer depends" in proposal.description
    assert proposal.amount_eur == Decimal("4350.00")


def test_facts_document_holds_formatted_strings(repo):
    document = fx.facts_document(_facts(repo, date(2025, 10, 15)))
    assert document == {
        "remaining_transfers": 2,
        "remaining_transfer_dates": ["2025-11-15", "2025-12-15"],
        "chf_per_transfer": "4,166.67 CHF",
        "remaining_chf": "8,333.34 CHF",
        "last_rate_paid": "1.046 EUR/CHF",
        "lowest_rate_paid_so_far": "1.038 EUR/CHF",
        "highest_rate_paid_so_far": "1.050 EUR/CHF",
        "average_rate_paid_so_far": "1.0432 EUR/CHF",
        "remaining_cost_at_last_rate": "€8,717",
        "possible_swing": "€100",
        "possible_swing_is_small": True,
    }
