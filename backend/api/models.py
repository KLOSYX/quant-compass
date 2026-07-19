from datetime import date
from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from core.constants import (
    DEFAULT_APPLY_FUND_FEES_TO_HISTORY,
    DEFAULT_CVAR_CONFIDENCE,
    DEFAULT_CVAR_LIMIT,
    DEFAULT_ESTIMATION_WINDOW,
    DEFAULT_KELLY_FRACTION,
    DEFAULT_MAX_DRAWDOWN_LIMIT,
    DEFAULT_RISK_HORIZON_DAYS,
    DEFAULT_STRATEGY_MODE,
)


class FundInvestmentLimit(BaseModel):
    daily_limit: Optional[float] = None
    monthly_limit: Optional[float] = None


class FundNamesRequest(BaseModel):
    fund_codes: List[str]


class AnalysisRequest(BaseModel):
    fund_codes: List[str]
    fund_fees: Dict[str, float]
    asset_categories: Dict[str, str] = {}
    substitute_for: Dict[str, str] = {}
    planned_purchase_days: Optional[int] = Field(default=None, ge=1)
    apply_fund_fees_to_history: bool = DEFAULT_APPLY_FUND_FEES_TO_HISTORY
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    risk_free_rate: Optional[float] = None
    # Add Strategy Params to align chart with actual backtest
    max_buy_multiplier: Optional[float] = Field(default=3.0, deprecated=True)
    sell_threshold: Optional[float] = Field(default=0.05, deprecated=True)
    min_weight: Optional[float] = None  # If None, use auto-tune. If set, use fixed.
    max_weight: Optional[float] = None
    strategy_mode: str = DEFAULT_STRATEGY_MODE
    kelly_fraction: float = DEFAULT_KELLY_FRACTION
    estimation_window: int = DEFAULT_ESTIMATION_WINDOW
    minimum_cash_reserve: float = 0.0
    enable_cvar_constraint: bool = True
    cvar_confidence: float = DEFAULT_CVAR_CONFIDENCE
    cvar_limit: float = DEFAULT_CVAR_LIMIT
    enable_drawdown_constraint: bool = True
    max_drawdown_limit: float = DEFAULT_MAX_DRAWDOWN_LIMIT
    risk_horizon_days: int = DEFAULT_RISK_HORIZON_DAYS
    buy_fee: Dict[str, float] = {}
    sell_fee: Dict[str, float] = {}
    fund_investment_limits: Dict[str, FundInvestmentLimit] = {}
    ma_window: int = 12
    include_strategy_frontier: bool = False
    initial_lump_sum: Optional[float] = 10000.0
    monthly_investment: Optional[float] = 1000.0


class StrategyBacktestRequest(BaseModel):
    fund_codes: List[str]
    weights: Dict[str, float]
    fund_fees: Dict[str, float]
    asset_categories: Dict[str, str] = {}
    substitute_for: Dict[str, str] = {}
    planned_purchase_days: Optional[int] = Field(default=None, ge=1)
    apply_fund_fees_to_history: bool = DEFAULT_APPLY_FUND_FEES_TO_HISTORY
    start_date: date
    end_date: date
    monthly_investment: float
    risk_free_rate: Optional[float] = None
    initial_holdings: Dict[str, float] = {}
    initial_cash: float = 0.0
    max_buy_multiplier: float = Field(default=3.0, deprecated=True)
    sell_threshold: float = Field(default=0.05, deprecated=True)
    min_weight: float = 0.3
    max_weight: float = 0.8
    strategy_mode: str = DEFAULT_STRATEGY_MODE
    kelly_fraction: float = DEFAULT_KELLY_FRACTION
    estimation_window: int = DEFAULT_ESTIMATION_WINDOW
    minimum_cash_reserve: float = 0.0
    enable_cvar_constraint: bool = True
    cvar_confidence: float = DEFAULT_CVAR_CONFIDENCE
    cvar_limit: float = DEFAULT_CVAR_LIMIT
    enable_drawdown_constraint: bool = True
    max_drawdown_limit: float = DEFAULT_MAX_DRAWDOWN_LIMIT
    risk_horizon_days: int = DEFAULT_RISK_HORIZON_DAYS
    buy_fee: Dict[str, float] = {}
    sell_fee: Dict[str, float] = {}
    fund_investment_limits: Dict[str, FundInvestmentLimit] = {}
    exit_fund_codes: List[str] = []
    reuse_settled_sale_proceeds: bool = False
    ma_window: int = 12
    include_walk_forward: bool = False


class CurrentRecommendationRequest(BaseModel):
    fund_codes: List[str]
    fund_fees: Dict[str, float] = {}
    asset_categories: Dict[str, str] = {}
    substitute_for: Dict[str, str] = {}
    planned_purchase_days: Optional[int] = Field(default=None, ge=1)
    apply_fund_fees_to_history: bool = DEFAULT_APPLY_FUND_FEES_TO_HISTORY
    weights: Dict[str, float]
    current_holdings: Dict[str, float] = {}
    current_cash: float = 0.0
    monthly_budget: float
    risk_free_rate: Optional[float] = None
    max_buy_multiplier: float = Field(default=3.0, deprecated=True)
    sell_threshold: float = Field(default=0.05, deprecated=True)
    min_weight: float = 0.3
    max_weight: float = 0.8
    strategy_mode: str = DEFAULT_STRATEGY_MODE
    kelly_fraction: float = DEFAULT_KELLY_FRACTION
    estimation_window: int = DEFAULT_ESTIMATION_WINDOW
    minimum_cash_reserve: float = 0.0
    enable_cvar_constraint: bool = True
    cvar_confidence: float = DEFAULT_CVAR_CONFIDENCE
    cvar_limit: float = DEFAULT_CVAR_LIMIT
    enable_drawdown_constraint: bool = True
    max_drawdown_limit: float = DEFAULT_MAX_DRAWDOWN_LIMIT
    risk_horizon_days: int = DEFAULT_RISK_HORIZON_DAYS
    buy_fee: Dict[str, float] = {}
    sell_fee: Dict[str, float] = {}
    fund_investment_limits: Dict[str, FundInvestmentLimit] = {}
    exit_fund_codes: List[str] = []
    reuse_settled_sale_proceeds: bool = False
    ma_window: int = 12
