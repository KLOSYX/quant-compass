from datetime import date
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from core.constants import (
    DEFAULT_APPLY_FUND_FEES_TO_HISTORY,
    DEFAULT_CVAR_CONFIDENCE,
    DEFAULT_CVAR_LIMIT,
    DEFAULT_ESTIMATION_WINDOW,
    DEFAULT_MAX_DRAWDOWN_LIMIT,
    DEFAULT_RISK_HORIZON_DAYS,
    DEFAULT_STRATEGY_MODE,
)


class FiniteBaseModel(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)


class FundInvestmentLimit(FiniteBaseModel):
    daily_limit: Optional[float] = None
    monthly_limit: Optional[float] = None
    daily_used: float = Field(default=0.0, ge=0)
    monthly_used: float = Field(default=0.0, ge=0)
    limit_basis: Literal["gross_cash_out", "net_asset_add"] = "gross_cash_out"


class FundNamesRequest(FiniteBaseModel):
    fund_codes: List[str]


class ActualFillRequest(FiniteBaseModel):
    decision_hash: str
    filled_at: date
    fills: Dict[str, float]
    fees: Dict[str, float] = {}


class PortfolioSettings(FiniteBaseModel):
    """Shared account, strategy and execution settings across all entry points."""

    model_config = ConfigDict(allow_inf_nan=False, extra="forbid")

    fund_codes: List[str]
    asset_categories: Dict[str, str] = {}
    substitute_for: Dict[str, str] = {}
    planned_purchase_days: Optional[int] = Field(default=None, ge=1)
    min_purchase_amount: float = Field(default=0.0, ge=0)
    amount_step: float = Field(default=0.01, gt=0)
    apply_fund_fees_to_history: bool = DEFAULT_APPLY_FUND_FEES_TO_HISTORY
    risk_free_rate: Optional[float] = None
    benchmark_rate: Optional[float] = None
    strategy_mode: Literal["fixed_weight"] = DEFAULT_STRATEGY_MODE
    estimation_window: int = DEFAULT_ESTIMATION_WINDOW
    minimum_cash_reserve: float = Field(default=0.0, ge=0)
    rebalance_enabled: bool = True
    rebalance_band: float = Field(default=0.02, ge=0, le=1)
    available_existing_cash: float = Field(default=0.0, ge=0)
    pending_sale_proceeds: float = Field(default=0.0, ge=0)
    pending_sell_amounts: Dict[str, float] = {}
    redemption_limits: Dict[str, float] = {}
    planned_purchase_dates: List[date] = []
    enable_cvar_constraint: bool = True
    cvar_confidence: float = DEFAULT_CVAR_CONFIDENCE
    cvar_limit: float = DEFAULT_CVAR_LIMIT
    enable_drawdown_constraint: bool = True
    max_drawdown_limit: float = DEFAULT_MAX_DRAWDOWN_LIMIT
    risk_horizon_days: int = DEFAULT_RISK_HORIZON_DAYS
    buy_fee: Dict[str, float] = {}
    sell_fee: Dict[str, float] = {}
    fund_investment_limits: Dict[str, FundInvestmentLimit] = {}
    allow_partial_return_data_for_research: bool = False


class AnalysisRequest(PortfolioSettings):
    target_weights: Optional[Dict[str, float]] = None
    fund_fees: Dict[str, float]
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    # Add Strategy Params to align chart with actual backtest
    include_strategy_frontier: bool = False
    initial_lump_sum: Optional[float] = 10000.0
    monthly_investment: Optional[float] = 1000.0


class StrategyBacktestRequest(PortfolioSettings):
    weights: Dict[str, float]
    fund_fees: Dict[str, float]
    start_date: date
    end_date: date
    monthly_investment: float
    initial_holdings: Dict[str, float] = {}
    initial_cash: float = 0.0
    exit_fund_codes: List[str] = []
    reuse_settled_sale_proceeds: bool = False
    include_walk_forward: bool = False
    account_distribution_policy: Literal["cash", "reinvest"] = "cash"


class CurrentRecommendationRequest(PortfolioSettings):
    fund_fees: Dict[str, float] = {}
    weights: Dict[str, float]
    current_holdings: Dict[str, float] = {}
    current_cash: float = 0.0
    monthly_budget: float
    exit_fund_codes: List[str] = []
    reuse_settled_sale_proceeds: bool = False
