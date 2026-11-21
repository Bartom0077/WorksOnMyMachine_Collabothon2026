"""Tax agent: the income tax shortfall estimated from the cash profit so far, and its two proposals."""
from datetime import date
from decimal import Decimal

import pytest

import config
from advisors import tax
from advisors.model import ActionType, Agent
from conftest import OBLIGATIONS_CSV
from domain import DashboardQuery, EventDraft, EventType, LedgerEntry
from service import build_workspace


def _workspace(repo, as_of, horizon=60, buffer="3000"):
    return build_workspace(repo, DashboardQuery(as_of, horizon, Decimal(buffer)))


def _entry(day, amount, category="card_revenue_debit"):
    return LedgerEntry(day, Decimal(amount), category, "X", "")


def _reserve_deposit(amount: str) -> EventDraft:
    return EventDraft(event_type=EventType.TERM_DEPOSIT, date_from=date(2025, 10, 1), date_to=date(2025, 11, 1),
                      amount_eur=Decimal(amount), category="tax_reserve", description="Tax reserve")


# --------------------------------------------------------------------------- constants

def test_tax_constants_match_the_documented_obligation(obligations):
    [prepayment] = [item for item in obligations if item.expense_id == "TAX002"]
    assert prepayment.category == "income_tax_advance"
    assert prepayment.amount_per_payment == config.INCOME_TAX_PREPAYMENT_EUR
    assert len(prepayment.payment_months.split(";")) == config.INCOME_TAX_PREPAYMENTS_PER_YEAR
    assert config.ANNUAL_INCOME_TAX_PREPAYMENTS_EUR == Decimal("4600")


def test_prior_year_profit_base_is_the_one_stated_in_the_obligation_note():
    [row] = [line for line in OBLIGATIONS_CSV.read_text(encoding="utf-8-sig").splitlines() if "TAX002" in line]
    assert f"previous year profit of ~EUR {config.PRIOR_YEAR_PROFIT_BASE_EUR}" in row


# --------------------------------------------------------------------------- figures

@pytest.mark.parametrize("as_of, profit, paid, reserve", [
    (date(2025, 10, 15), "86432.04", "3450.00", "9802.91"),
    (date(2025, 12, 31), "126845.55", "4600.00", "14849.65"),
])
def test_checkpoints_on_the_real_statement(ledger, as_of, profit, paid, reserve):
    position = tax.tax_position(ledger, as_of, ())
    assert position.cash_profit_eur == Decimal(profit)
    assert position.prepayments_paid_eur == Decimal(paid)
    assert position.reserve_eur == Decimal(reserve) == position.still_needed_eur
    assert position.is_active and position.wants_reserve_deposit


def test_pro_rata_base_counts_the_days_inclusively(ledger):
    assert tax.tax_position(ledger, date(2025, 12, 31), ()).pro_rata_base_eur == Decimal("30000.00")
    october = tax.tax_position(ledger, date(2025, 10, 15), ())
    assert october.days_elapsed == 288 and october.pro_rata_base_eur == Decimal("23671.23")  # 30,000 * 288 / 365
    assert tax.tax_position(ledger, date(2025, 1, 1), ()).days_elapsed == 1


def test_estimated_tax_uses_the_rate_derived_from_the_prepayments(ledger):
    position = tax.tax_position(ledger, date(2025, 10, 15), ())
    assert position.estimated_tax_eur == Decimal("13252.91")  # 86,432.04 * 4,600 / 30,000


def test_drawings_zurich_transfers_and_tax_prepayments_are_not_costs():
    day = date(2025, 3, 20)
    entries = [_entry(day, "20000"), _entry(day, "-5000", "supplier_invoices"),
               _entry(day, "-3000", "owner_withdrawal"), _entry(day, "-4350", "investment_outflow"),
               _entry(day, "-1150", "income_tax_advance"), _entry(day, "-225", "trade_tax_advance"),
               _entry(date(2024, 12, 30), "99999"), _entry(date(2025, 3, 21), "99999")]
    position = tax.tax_position(entries, day, ())
    assert position.cash_profit_eur == Decimal("15000")
    assert position.prepayments_paid_eur == Decimal("1150")
    assert position.reserve_eur == Decimal("1150.00")  # 15,000 * 4,600 / 30,000 = 2,300, less 1,150 paid


# --------------------------------------------------------------------------- when the agent stays silent

def test_inactive_when_profit_does_not_run_ahead_of_the_prepayment_base():
    day = date(2025, 12, 31)
    position = tax.tax_position([_entry(day, "30000")], day, ())
    assert position.cash_profit_eur == position.pro_rata_base_eur
    assert not position.is_active and not position.wants_reserve_deposit


def test_inactive_when_the_shortfall_is_below_the_threshold():
    day = date(2025, 12, 31)
    position = tax.tax_position([_entry(day, "36500")], day, ())
    assert position.cash_profit_eur > position.pro_rata_base_eur
    assert position.reserve_eur == Decimal("5596.67")
    paid_up = tax.tax_position([_entry(day, "36500"), _entry(day, "-4600", "income_tax_advance")], day, ())
    assert paid_up.reserve_eur == Decimal("996.67") < config.TAX_RESERVE_MIN_EUR
    assert not paid_up.is_active


def test_never_a_negative_reserve():
    day = date(2025, 6, 30)
    position = tax.tax_position([_entry(day, "-8000", "rent"), _entry(day, "-1150", "income_tax_advance")], day, ())
    assert position.reserve_eur == 0 and not position.is_active


def test_inactive_in_the_first_days_of_the_real_year(ledger):
    assert not tax.tax_position(ledger, date(2025, 1, 5), ()).is_active


def test_reserve_deposits_already_planned_reduce_what_is_still_needed(ledger):
    as_of = date(2025, 10, 15)
    investment_deposit = EventDraft(event_type=EventType.TERM_DEPOSIT, date_from=as_of, date_to=date(2025, 11, 15),
                                    amount_eur=Decimal("5000"), category="investment", description="Investment")
    position = tax.tax_position(ledger, as_of, (_reserve_deposit("9000"), investment_deposit))
    assert position.set_aside_eur == Decimal("9000")
    assert position.still_needed_eur == Decimal("802.91")
    assert position.is_active and not position.wants_reserve_deposit
    fully_covered = tax.tax_position(ledger, as_of, (_reserve_deposit("9000"), _reserve_deposit("5000")))
    assert fully_covered.still_needed_eur == 0


# --------------------------------------------------------------------------- proposals and texts

def test_reserve_deposit_is_a_one_month_term_deposit_marked_as_tax_reserve(repo):
    workspace = _workspace(repo, date(2025, 10, 15))
    proposal = tax.reserve_deposit(workspace, tax.assess(workspace), Decimal("9000"))
    [event] = proposal.events_to_add
    assert (proposal.id, proposal.agent, proposal.action_type) == ("T1", Agent.TAX, ActionType.TAX_RESERVE)
    assert (event.event_type, event.category, event.amount_eur) == (EventType.TERM_DEPOSIT, "tax_reserve", 9000)
    assert (event.date_from, event.date_to) == (date(2025, 10, 16), date(2025, 11, 16))
    assert (proposal.amount_eur, proposal.interest_eur) == (Decimal("9000"), Decimal("15.29"))
    assert proposal.new_min_balance_eur == workspace.dashboard.risk.min_balance_eur - Decimal("9000")
    assert proposal.title == "Tax reserve deposit of €9,000"
    assert "covers €9,000 of the €9,803 still needed" in proposal.description
    assert "Commerzbank" in proposal.description
    assert proposal.assumptions[0] == "Interest at 2% p.a. (assumed rate, not a bank quote)."
    assert proposal.simulated_actions == (
        "Tax reserve: 1-month term deposit of €9,000 with Commerzbank opened on 16 October, maturing on "
        "16 November (simulated).",)


def test_reserve_text_promises_nothing_about_the_tax_bill_and_says_when_the_deposit_ends(repo):
    workspace = _workspace(repo, date(2025, 10, 15))
    description = tax.reserve_deposit(workspace, tax.assess(workspace), Decimal("9000")).description
    assert ("Put €9,000 into a 1-month term deposit with Commerzbank now, to keep it apart from your day-to-day "
            "money for the extra tax you may owe. The deposit ends on 16 November; renew it then if the tax is "
            "still outstanding.") in description
    assert "when the tax bill comes" not in description


def test_full_reserve_does_not_talk_about_partial_cover(repo):
    workspace = _workspace(repo, date(2025, 12, 31))
    position = tax.assess(workspace)
    proposal = tax.reserve_deposit(workspace, position, position.still_needed_eur)
    assert proposal.amount_eur == Decimal("14849.65")
    assert "covers" not in proposal.description and "already set aside" not in proposal.description


def test_reserve_text_mentions_what_is_already_set_aside(repo):
    repo.add_event(_reserve_deposit("5000"))
    workspace = _workspace(repo, date(2025, 10, 15))
    position = tax.assess(workspace)
    proposal = tax.reserve_deposit(workspace, position, position.still_needed_eur)
    assert "You have already set aside €5,000, which leaves €4,803." in proposal.description


def test_advice_has_two_wordings_with_different_fingerprints(repo):
    workspace = _workspace(repo, date(2025, 10, 15))
    position = tax.assess(workspace)
    normal = tax.prepayment_advice(workspace, position, liquidity_first=False)
    tight = tax.prepayment_advice(workspace, position, liquidity_first=True)
    assert normal.title == "Ask for higher income tax prepayments"
    assert "raise the quarterly prepayments (now €1,150)" in normal.description
    assert "start setting money aside for it once the balance recovers" in tight.description
    assert "€9,803" in normal.description and "€9,803" in tight.description
    assert normal.fingerprint != tight.fingerprint
    for advice in (normal, tight):
        assert (advice.id, advice.action_type, advice.events_to_add) == ("T2", ActionType.ADVICE, ())
        assert advice.amount_eur is None and advice.date_from is None and advice.date_to is None
        assert advice.scenario.risk == workspace.dashboard.risk
        assert all(action.endswith("(simulated).") for action in advice.simulated_actions)


def test_advice_stops_promising_payment_in_steps_once_the_last_prepayment_is_paid(repo):
    """The schedule holds four prepayments a year; after the one of 10 December none is left for this year."""
    texts = {}
    for as_of in (date(2025, 10, 15), date(2025, 12, 9), date(2025, 12, 10), date(2025, 12, 31)):
        workspace = _workspace(repo, as_of)
        texts[as_of] = tax.prepayment_advice(workspace, tax.assess(workspace), liquidity_first=False).description
    for before_the_last in (date(2025, 10, 15), date(2025, 12, 9)):
        assert "so the difference is paid in steps and not as one large bill." in texts[before_the_last]
        assert "prepayments are paid" not in texts[before_the_last]
    for after_the_last in (date(2025, 12, 10), date(2025, 12, 31)):
        assert "in steps" not in texts[after_the_last]
        assert texts[after_the_last].endswith(
            "All of this year's prepayments are paid. Ask your tax advisor or the tax office to raise the "
            "quarterly prepayments (now €1,150), so they match your higher profit.")


def test_advice_fingerprint_is_stable_within_the_tax_year(repo):
    october, december = _workspace(repo, date(2025, 10, 15)), _workspace(repo, date(2025, 12, 31))
    assert (tax.prepayment_advice(october, tax.assess(october), False).fingerprint
            == tax.prepayment_advice(december, tax.assess(december), False).fingerprint)


def test_tax_texts_name_no_tax_rate_and_no_settlement_date(repo):
    workspace = _workspace(repo, date(2025, 10, 15))
    position = tax.assess(workspace)
    proposals = [tax.reserve_deposit(workspace, position, Decimal("9000")),
                 tax.prepayment_advice(workspace, position, False), tax.prepayment_advice(workspace, position, True)]
    for proposal in proposals:
        own_words = " ".join([proposal.title, proposal.description])
        assert "%" not in own_words and "15.3" not in own_words
        everything = " ".join([own_words, proposal.effect, *proposal.assumptions]).lower()
        for forbidden in ("deadline", "settle", "due on", "due by", "statutory", "tax rate"):
            assert forbidden not in everything


def test_facts_document_holds_formatted_strings_only(repo):
    workspace = _workspace(repo, date(2025, 10, 15))
    facts = tax.facts_document(tax.assess(workspace))
    assert facts["tax_year"] == 2025
    assert facts["cash_profit_so_far"] == "€86,432"
    assert facts["prepayments_paid_so_far"] == "€3,450"
    assert facts["expected_shortfall"] == "€9,803" == facts["still_to_set_aside"]
    assert facts["already_set_aside"] == "€0"
    assert all(isinstance(value, (str, int)) for value in facts.values())
