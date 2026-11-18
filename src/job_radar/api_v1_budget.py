"""Pure conservative accounting contract; step one has no provider or enable switch."""
from decimal import Decimal, InvalidOperation
from pydantic import Field, model_validator
from .api_v1_models import WireModel


class BudgetRejected(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def money(value):
    if not isinstance(value, str):
        raise ValueError('decimal_string_required')
    try:
        amount = Decimal(value)
    except InvalidOperation:
        raise ValueError('invalid_money') from None
    if not amount.is_finite() or amount < 0:
        raise ValueError('invalid_money')
    return amount


class BudgetState(WireModel):
    """Simulation value only. Reservations are retained even after known usage."""
    actual_cny: str = '0.05320458'
    reserved_cny: str = '0.6829056'
    new_attempts: int = Field(default=0, ge=0, le=9)
    halted: bool = False

    @model_validator(mode='after')
    def valid_money(self):
        money(self.actual_cny);money(self.reserved_cny)
        return self


def reserve(state: BudgetState, amount_cny: str) -> BudgetState:
    """Pure projection for tests/future adapter; never authorizes an HTTP request."""
    state = BudgetState.model_validate(state.model_dump())
    actual, reserved, amount = map(money, (state.actual_cny,state.reserved_cny,amount_cny))
    if state.halted:
        raise BudgetRejected('budget_halted')
    if state.new_attempts >= 9:
        raise BudgetRejected('attempt_limit')
    if amount == 0:
        raise ValueError('positive_reservation_required')
    if actual + reserved + amount > Decimal('1.00'):
        raise BudgetRejected('budget_exhausted')
    return BudgetState(actual_cny=str(actual),reserved_cny=str(reserved+amount),
                       new_attempts=state.new_attempts+1)


def record_usage(state: BudgetState, actual_cny: str | None) -> BudgetState:
    state = BudgetState.model_validate(state.model_dump())
    if state.new_attempts == 0:
        raise ValueError('usage_requires_reservation')
    actual, reserved = money(state.actual_cny), money(state.reserved_cny)
    if actual_cny is None:
        return state.model_copy(update={'halted':True})
    actual += money(actual_cny)
    return BudgetState(actual_cny=str(actual), reserved_cny=str(reserved),
                       new_attempts=state.new_attempts,
                       halted=state.halted or actual+reserved > Decimal('1.00'))


def require_live_authorization():
    # No environment variable or HTTP body can enable a provider in step one.
    raise BudgetRejected('llm_disabled')


FROZEN_POLICY = {
    'schema_version':'api-budget-policy-v1', 'enabled':False,
    'cap_cny':'1.00', 'historical_actual_cny':'0.05320458',
    'historical_reserved_cny':'0.6829056', 'conservative_exposure_cny':'0.73611018',
    'max_new_generations':9, 'concurrency':1, 'retries':0,
    'model':'gpt-6.1-sol', 'reasoning':'low', 'prompt':'match-analysis-prompt-v3',
    'reservation_refund_on_failure':False,
}
