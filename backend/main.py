import time
import traceback
from datetime import date
from typing import Dict, List, Optional

import akshare as ak
import numpy as np
import pandas as pd
import requests
from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from scipy.optimize import minimize
from starlette.staticfiles import StaticFiles

# Set a default timeout for all requests globally to prevent hanging
# This specifically addresses the user's request to "reduce timeout"
_original_request = requests.Session.request


def _patched_request(self, method, url, *args, **kwargs):
    if "timeout" not in kwargs:
        kwargs["timeout"] = 10  # 10 seconds timeout
    return _original_request(self, method, url, *args, **kwargs)


requests.Session.request = _patched_request

app = FastAPI()
router = APIRouter()

FUND_LIST_CACHE = None
MAX_SINGLE_WEIGHT = 0.5  # prevent over-concentration in a single fund
MIN_WEIGHT_THRESHOLD = 0.01  # drop tiny weights that are hard to execute

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class AnalysisRequest(BaseModel):
    fund_codes: List[str]
    fund_fees: Dict[str, float]
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    risk_free_rate: Optional[float] = None
    # Add Strategy Params to align chart with actual backtest
    max_buy_multiplier: Optional[float] = 3.0
    sell_threshold: Optional[float] = 0.05
    min_weight: Optional[float] = None  # If None, use auto-tune. If set, use fixed.
    max_weight: Optional[float] = None
    buy_fee: Dict[str, float] = {}
    sell_fee: Dict[str, float] = {}
    ma_window: int = 12
    initial_lump_sum: Optional[float] = 10000.0
    monthly_investment: Optional[float] = 1000.0


class StrategyBacktestRequest(BaseModel):
    fund_codes: List[str]
    weights: Dict[str, float]
    fund_fees: Dict[str, float]
    start_date: date
    end_date: date
    monthly_investment: float
    risk_free_rate: Optional[float] = None
    initial_holdings: Dict[str, float] = {}
    max_buy_multiplier: float = 3.0
    sell_threshold: float = 0.05
    min_weight: float = 0.3
    max_weight: float = 0.8
    buy_fee: Dict[str, float] = {}
    sell_fee: Dict[str, float] = {}
    ma_window: int = 12


class CurrentRecommendationRequest(BaseModel):
    fund_codes: List[str]
    weights: Dict[str, float]
    current_holdings: Dict[str, float] = {}
    current_cash: float = 0.0
    monthly_budget: float
    max_buy_multiplier: float = 3.0
    sell_threshold: float = 0.05
    min_weight: float = 0.3
    max_weight: float = 0.8
    buy_fee: Dict[str, float] = {}
    sell_fee: Dict[str, float] = {}
    ma_window: int = 12


def get_fund_data(
    fund_codes: List[str],
    start_date: Optional[date],
    end_date: Optional[date],
    risk_free_rate: Optional[float],
) -> (pd.DataFrame, Dict[str, str], List[str]):
    global FUND_LIST_CACHE
    if FUND_LIST_CACHE is None:
        try:
            print("Initializing fund list cache...")
            FUND_LIST_CACHE = ak.fund_name_em()
            FUND_LIST_CACHE.set_index("基金代码", inplace=True)
            print("Fund list cache initialized.")
        except Exception as e:
            raise HTTPException(
                status_code=500, detail=f"Failed to initialize fund list cache: {e}"
            )

    fund_data = {}
    fund_names = {}
    warnings = []
    if fund_codes:
        for code in fund_codes:
            try:
                try:
                    fund_names[code] = FUND_LIST_CACHE.loc[code, "基金简称"]
                except KeyError:
                    fund_names[code] = f"{code} (名称未找到)"

                # Implement simple retry logic with exponential backoff
                # Increased retries and reduced timeout as requested by user
                max_retries = 5
                retry_delay = 1
                fund_nav = None

                for attempt in range(max_retries):
                    try:
                        fund_nav = ak.fund_open_fund_info_em(
                            symbol=code, indicator="单位净值走势"
                        )
                        break
                    except Exception as e:
                        if attempt == max_retries - 1:
                            raise e
                        # Log to console for debugging
                        print(
                            f"Attempt {attempt + 1}/{max_retries} failed for {code}: {e}. Retrying in {retry_delay}s..."
                        )
                        time.sleep(retry_delay)
                        retry_delay *= 2

                if fund_nav is None:
                    raise ValueError(
                        f"Failed to fetch data for {code} after {max_retries} attempts"
                    )

                fund_nav["净值日期"] = pd.to_datetime(fund_nav["净值日期"])
                fund_nav = fund_nav.set_index("净值日期")["单位净值"].astype(float)
                fund_data[code] = fund_nav.resample("ME").last()

            except Exception as e:
                raise HTTPException(
                    status_code=400, detail=f"获取基金 {code} 的净值数据时发生错误: {e}"
                )

    df = pd.DataFrame(fund_data)
    df = df.sort_index()

    if not df.empty:
        latest_start_date = max(
            df[c].first_valid_index()
            for c in df.columns
            if pd.notna(df[c].first_valid_index())
        )
        user_start = pd.to_datetime(start_date) if start_date else latest_start_date
        user_end = pd.to_datetime(end_date) if end_date else df.index.max()
        actual_start = max(latest_start_date, user_start)
        actual_end = min(user_end, df.index.max())
    else:
        if not start_date or not end_date:
            raise HTTPException(
                status_code=400, detail="当没有选择基金时，必须提供开始和结束日期。"
            )
        user_start = pd.to_datetime(start_date)
        user_end = pd.to_datetime(end_date)
        actual_start, actual_end = user_start, user_end
        df = pd.DataFrame(
            index=pd.date_range(start=actual_start, end=actual_end, freq="ME")
        )

    if risk_free_rate is not None:
        monthly_rf_return = (1 + risk_free_rate) ** (1 / 12) - 1
        rf_index = pd.date_range(start=actual_start, end=actual_end, freq="ME")
        rf_returns = pd.Series(monthly_rf_return, index=rf_index)
        rf_nav = (1 + rf_returns).cumprod()
        df["RiskFree"] = rf_nav
        fund_names["RiskFree"] = "无风险资产"

    if actual_start > user_start and fund_codes:
        warnings.append(
            f"注意：部分基金在您选择的开始日期 {user_start.strftime('%Y-%m-%d')} 尚未成立，实际回测已从 {actual_start.strftime('%Y-%m-%d')} 开始。"
        )

    if actual_start >= actual_end:
        raise HTTPException(
            status_code=400, detail="在指定的时间范围内，所选基金没有重叠的交易日。"
        )

    df_filtered = df.loc[actual_start:actual_end]
    df_processed = df_filtered.ffill().dropna()

    if df_processed.empty:
        raise HTTPException(status_code=400, detail="数据处理后为空，无法进行分析。")

    return df_processed, fund_names, warnings


def calculate_efficient_frontier(df, fund_fees):
    if list(df.columns) == ["RiskFree"]:
        monthly_returns = df.pct_change().fillna(0)
        expected_return = monthly_returns["RiskFree"].mean()
        return [
            {"risk": 0, "return": expected_return * 12, "weights": {"RiskFree": 1.0}}
        ]

    monthly_returns = df.pct_change().fillna(0)
    for code in monthly_returns.columns:
        if code == "RiskFree":
            continue
        monthly_fee = fund_fees.get(code, 0) / 12
        monthly_returns[code] -= monthly_fee

    expected_returns = monthly_returns.mean()
    cov_matrix = monthly_returns.cov()
    if "RiskFree" in cov_matrix.columns:
        cov_matrix["RiskFree"] = 0
        cov_matrix.loc["RiskFree"] = 0

    num_assets = len(df.columns)
    bounds = tuple((0, MAX_SINGLE_WEIGHT) for _ in range(num_assets))

    def clean_weights(raw_weights):
        """Zero out tiny weights and renormalize to keep allocations executable."""
        weights = pd.Series(raw_weights, index=df.columns)
        weights[weights < MIN_WEIGHT_THRESHOLD] = 0
        total = weights.sum()
        if total > 0:
            weights /= total
        else:
            weights = pd.Series(raw_weights, index=df.columns)
        return weights

    def portfolio_variance(w):
        return np.dot(w.T, np.dot(cov_matrix, w))

    mvp_constraints = {"type": "eq", "fun": lambda w: np.sum(w) - 1}
    mvp_result = minimize(
        portfolio_variance,
        [1.0 / num_assets] * num_assets,
        method="SLSQP",
        bounds=bounds,
        constraints=mvp_constraints,
    )
    mvp_return = np.sum(expected_returns * mvp_result.x)

    frontier_returns = np.linspace(mvp_return, expected_returns.max(), 20)
    frontier_points = []

    for target in frontier_returns:
        constraints = (
            {"type": "eq", "fun": lambda w: np.sum(expected_returns * w) - target},
            {"type": "eq", "fun": lambda w: np.sum(w) - 1},
        )
        result = minimize(
            portfolio_variance,
            [1.0 / num_assets] * num_assets,
            method="SLSQP",
            bounds=bounds,
            constraints=constraints,
        )

        if result.success:
            cleaned_weights = clean_weights(result.x)
            # re-evaluate variance with cleaned weights to reflect practical allocation
            risk = np.sqrt(
                np.dot(cleaned_weights.T, np.dot(cov_matrix, cleaned_weights))
            ) * np.sqrt(12)
            ret = target * 12
            frontier_points.append(
                {"risk": risk, "return": ret, "weights": cleaned_weights.to_dict()}
            )

    return frontier_points


def calculate_max_drawdown(nav_series: pd.Series) -> float:
    """Return max drawdown as a decimal (e.g., 0.2 for -20%)."""
    if nav_series.empty:
        return 0.0
    rolling_max = nav_series.cummax()
    drawdowns = (nav_series - rolling_max) / rolling_max
    return drawdowns.min()


def backtest_lump_sum(df_nav, weights_dict, total_investment, initial_holdings=None):
    if initial_holdings is None:
        initial_holdings = {}
    weights = pd.Series(weights_dict).reindex(df_nav.columns).fillna(0)
    initial_nav = df_nav.iloc[0]

    # Calculate initial shares from both new investment and existing holdings
    initial_shares = pd.Series(0.0, index=df_nav.columns)
    initial_cash = initial_holdings.get("RiskFree", 0.0)

    for code in df_nav.columns:
        # Shares from initial holdings
        if code in initial_holdings:
            initial_shares[code] = initial_holdings[code] / initial_nav[code]
        # plus shares from new lump sum cash
        initial_shares[code] += (total_investment * weights[code]) / initial_nav[code]

    # Total capital committed at start
    initial_holdings_value = sum(
        v for k, v in initial_holdings.items() if k != "RiskFree"
    )
    total_committed = total_investment + initial_holdings_value + initial_cash

    portfolio_history_values = df_nav.dot(initial_shares.T) + initial_cash
    attribution_history = df_nav.multiply(initial_shares, axis="columns")

    portfolio_series = pd.Series(portfolio_history_values)
    max_drawdown_value = calculate_max_drawdown(portfolio_series)
    max_drawdown_nav = calculate_max_drawdown(portfolio_series / total_committed)

    # Calculate Annualized Return (CAGR)
    days = (df_nav.index[-1] - df_nav.index[0]).days
    years = days / 365.25 if days > 0 else 0
    annualized_return = 0.0
    if years > 0 and total_committed > 0:
        final_value = portfolio_history_values.iloc[-1]
        if final_value > 0:
            annualized_return = (final_value / total_committed) ** (1 / years) - 1
        else:
            annualized_return = -1.0  # Lost everything

    return {
        "total_invested": total_committed,
        "final_value": portfolio_history_values.iloc[-1],
        "annualized_return": annualized_return,
        "max_drawdown": float(max_drawdown_nav),
        "max_drawdown_value": float(max_drawdown_value),
        "max_drawdown_nav": float(max_drawdown_nav),
        "history": {
            date.strftime("%Y-%m"): value
            for date, value in portfolio_history_values.to_dict().items()
        },
        "attribution": {
            date.strftime("%Y-%m"): {**row.to_dict(), "RiskFree": initial_cash}
            for date, row in attribution_history.iterrows()
        },
    }


def backtest_dca(df_nav, weights_dict, monthly_investment, initial_holdings=None):
    if initial_holdings is None:
        initial_holdings = {}
    weights = pd.Series(weights_dict).reindex(df_nav.columns).fillna(0)
    initial_nav = df_nav.iloc[0]

    total_shares = pd.Series(0.0, index=df_nav.columns)
    # Initialize from existing holdings
    for code in df_nav.columns:
        if code in initial_holdings and initial_holdings[code] > 0:
            total_shares[code] = initial_holdings[code] / initial_nav[code]

    cash_balance = initial_holdings.get("RiskFree", 0.0)
    initial_equity_value = sum(
        v for k, v in initial_holdings.items() if k != "RiskFree"
    )
    total_invested = initial_equity_value + cash_balance

    portfolio_history = {}
    attribution_history = {}
    invested_history = {}

    # Initialize "Strategy Unit" accounting
    # We treat the strategy as a fund where new investments buy "units" of the strategy
    # This allows correctly calculating Drawdown based on Unit Value, not (Value/Invested)
    total_units = 0.0
    if total_invested > 0:
        total_units = total_invested  # Initial units at price 1.0

    unit_nav_history = {}

    for timestamp, nav_row in df_nav.iterrows():
        # 1. Calculate Portfolio Value BEFORE new investment (market impact on existing assets)
        current_val_pre = (total_shares * nav_row).sum() + cash_balance

        # 2. Derive Unit NAV
        if total_units > 0:
            unit_nav = current_val_pre / total_units
        else:
            unit_nav = 1.0

        unit_nav_history[timestamp] = unit_nav

        # 3. Add New Investment (Buying Strategy Units)
        total_invested += monthly_investment
        if unit_nav > 0:
            new_units = monthly_investment / unit_nav
            total_units += new_units

        # 4. Execute Investment Logic (Buying Underlying Assets)
        shares_bought = (monthly_investment * weights) / nav_row
        total_shares += shares_bought

        # 5. Record State
        current_asset_values = total_shares * nav_row
        attr = current_asset_values.to_dict()
        attr["RiskFree"] = cash_balance
        attribution_history[timestamp] = attr
        portfolio_history[timestamp] = current_asset_values.sum() + cash_balance
        invested_history[timestamp] = total_invested

    portfolio_series = pd.Series(portfolio_history)
    unit_nav_series = pd.Series(unit_nav_history)
    # invested_series = pd.Series(invested_history) # No longer used for DD

    # Calculate Max Drawdown based on Unit NAV Series (True performance)
    max_drawdown_nav = calculate_max_drawdown(unit_nav_series)
    max_drawdown_value = calculate_max_drawdown(portfolio_series)

    # Calculate Annualized Return (CAGR based on Strategy Unit NAV)
    days = (df_nav.index[-1] - df_nav.index[0]).days
    years = days / 365.25 if days > 0 else 0
    annualized_return = 0.0

    final_unit_nav = (
        float(unit_nav_series.iloc[-1]) if not unit_nav_series.empty else 1.0
    )

    if years > 0:
        if final_unit_nav > 0:
            annualized_return = (final_unit_nav) ** (1 / years) - 1
        else:
            annualized_return = -1.0

    return {
        "total_invested": total_invested,
        "final_value": list(portfolio_history.values())[-1],
        "final_unit_nav": final_unit_nav,
        "annualized_return": annualized_return,
        "max_drawdown": float(max_drawdown_nav),
        "max_drawdown_value": float(max_drawdown_value),
        "max_drawdown_nav": float(max_drawdown_nav),
        "history": {
            date.strftime("%Y-%m"): value for date, value in portfolio_history.items()
        },
        "attribution": {
            date.strftime("%Y-%m"): value for date, value in attribution_history.items()
        },
    }


def simulate_strategy_frontier(
    frontier_points,
    nav_adjusted,
    fund_fees,
    start_date,
    end_date,
    risk_free_rate,
    ma_window=12,
    buy_fee=None,
    sell_fee=None,
    max_buy_multiplier=3.0,
    sell_threshold=0.05,
    user_min_weight=None,
    user_max_weight=None,
    initial_lump_sum=0.0,
    monthly_investment=1000.0,
):
    """
    Simulate VA/Kelly strategy for each point on the frontier to get separate 'Strategy Frontier'.
    If user_min_weight / user_max_weight are provided, use them (Manual Mode).
    Otherwise, use auto-tuning logic (Auto Mode).
    """
    strategy_points = []
    risks = [p["risk"] for p in frontier_points]
    if not risks:
        return []
    min_risk = min(risks)
    max_risk = max(risks)

    # Prepare backtest common params
    # We use user provided params to match scale

    for pt in frontier_points:
        risk = pt["risk"]
        weights = pt["weights"]

        if user_min_weight is not None and user_max_weight is not None:
            # Use user provided fixed params (Align with detailed backtest)
            min_weight = user_min_weight
            max_weight = user_max_weight
        else:
            # Auto-tune Params Logic (Aggressive)
            if max_risk > min_risk:
                risk_level = (risk - min_risk) / (max_risk - min_risk)
            else:
                risk_level = 0.5  # Default middle

            # Interpolate: Min 40->90, Max 80->100
            min_weight = 0.4 + (risk_level * 0.5)
            max_weight = 0.8 + (risk_level * 0.2)

        # Run Backtest
        # Note: nav_adjusted ALREADY has fees subtracted in analyze_portfolio
        # so we pass buy_fee/sell_fee as 0 to keep it pure to the strategy logic impact
        result = backtest_kelly_dca(
            nav_adjusted,
            weights,
            monthly_investment,
            initial_holdings={
                code: initial_lump_sum * w for code, w in weights.items() if w > 0
            },
            max_buy_multiplier=max_buy_multiplier,
            sell_threshold=sell_threshold,
            min_weight=min_weight,
            max_weight=max_weight,
            buy_fee=buy_fee or {},
            sell_fee=sell_fee or {},
            ma_window=ma_window,
            risk_free_rate=risk_free_rate or 0.0,
        )

        # Strategy Return: Standard CAGR based on Strategy Unit NAV
        # Formula: FinalUnitNav ^ (1 / Years) - 1
        # Duration in years
        days = (pd.to_datetime(end_date) - pd.to_datetime(start_date)).days
        years = days / 365.25
        if years <= 0:
            years = 1

        final_nav = result.get("final_unit_nav", 1.0)
        strategy_return_annualized = (final_nav) ** (1 / years) - 1

        # 2. Strategy Risk (Drawdown? or Volatility?)
        # MV Frontier X-axis is Volatility.
        # But Strategy users care about Drawdown.
        # However, to plot on the SAME chart, X-axis must remain "Annualized Volatility"
        # OR we plot a different chart.
        # If we plot on same chart, we should calculate the Strategy's Volatility.
        # Strategy Volatility = StdDev of the strategy's monthly returns (Total Value change)

        # Calculate monthly returns of the strategy portfolio
        hist_vals = pd.Series(result["history"])
        # Needs to be sorted by date? dictionary order is insertion order (Python 3.7+), valid.
        # Convert index to datetime
        hist_vals.index = pd.to_datetime(hist_vals.index)
        strat_monthly_rets = hist_vals.pct_change().fillna(0)
        strategy_volatility = strat_monthly_rets.std() * np.sqrt(12)

        max_dd = result["max_drawdown_nav"]  # Negative float

        strategy_points.append(
            {
                "risk": strategy_volatility,
                "return": strategy_return_annualized,
                "max_drawdown": max_dd,
                "original_risk": risk,  # Link back to original point
                "weights": weights,
                "auto_params": {"min_weight": min_weight, "max_weight": max_weight},
            }
        )

    return strategy_points


@router.post("/analyze")
async def analyze_portfolio(request: AnalysisRequest):
    if not request.fund_codes and request.risk_free_rate is None:
        raise HTTPException(
            status_code=400, detail="请至少选择一只基金或添加无风险资产。"
        )
    try:
        fund_df, fund_names, warnings = get_fund_data(
            request.fund_codes,
            request.start_date,
            request.end_date,
            request.risk_free_rate,
        )
        efficient_frontier_points = calculate_efficient_frontier(
            fund_df, request.fund_fees
        )

        # --- Simulate Strategy Frontier ---
        # 1. Prepare NAV Adjusted (deduct fees) same as in backtest endpoint
        monthly_returns = fund_df.pct_change().fillna(0)
        for code in monthly_returns.columns:
            if code == "RiskFree":
                continue
            monthly_fee = request.fund_fees.get(code, 0) / 12
            monthly_returns[code] -= monthly_fee
        nav_adjusted = (1 + monthly_returns).cumprod()

        start_date_str = fund_df.index.min().strftime("%Y-%m-%d")
        end_date_str = fund_df.index.max().strftime("%Y-%m-%d")

        strategy_frontier_points = simulate_strategy_frontier(
            efficient_frontier_points,
            nav_adjusted,
            request.fund_fees,
            start_date_str,
            end_date_str,
            request.risk_free_rate,
            ma_window=request.ma_window,
            buy_fee=request.buy_fee,
            sell_fee=request.sell_fee,
            max_buy_multiplier=request.max_buy_multiplier,
            sell_threshold=request.sell_threshold,
            user_min_weight=request.min_weight,
            user_max_weight=request.max_weight,
            initial_lump_sum=request.initial_lump_sum or 0.0,
            monthly_investment=request.monthly_investment or 1000.0,
        )

        return {
            "efficient_frontier": efficient_frontier_points,
            "strategy_frontier": strategy_frontier_points,
            "fund_names": fund_names,
            "backtest_period": {
                "start_date": start_date_str,
                "end_date": end_date_str,
            },
            "warnings": warnings,
        }
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


def calculate_target_ratio(current_price, ma_value, min_weight, max_weight):
    if ma_value == 0:
        return min_weight, "neutral"

    # Simple linear interpolation between low bias (0.8) and high bias (1.2)
    low_bias = 0.8
    high_bias = 1.2
    bias = current_price / ma_value

    if bias <= low_bias:
        return max_weight, "undervalued"
    elif bias >= high_bias:
        return min_weight, "overvalued"
    else:
        # Interpolate
        # Ratio = Slope * (Bias - LowBias) + MaxWeight
        slope = (min_weight - max_weight) / (high_bias - low_bias)
        ratio = slope * (bias - low_bias) + max_weight

        # Signal name for return value clarification
        if bias < 0.95:
            signal = "undervalued"
        elif bias > 1.05:
            signal = "overvalued"
        else:
            signal = "neutral"

        return ratio, signal


@router.post("/current_recommendation")
async def get_current_recommendation(request: CurrentRecommendationRequest):
    """Calculate current investment recommendation based on latest market data."""
    try:
        # Fetch latest fund data (last 12 months for MA calculation)
        end_date_obj = date.today()
        start_date_obj = date(
            end_date_obj.year - 2, end_date_obj.month, end_date_obj.day
        )

        fund_df, fund_names, _ = get_fund_data(
            request.fund_codes, start_date_obj, end_date_obj, None
        )

        # Calculate portfolio reference NAV
        weights = pd.Series(request.weights).reindex(fund_df.columns).fillna(0)
        reference_portfolio_nav = fund_df.dot(weights)

        # Calculate moving average based on ma_window
        ma_window = request.ma_window
        ma_series = reference_portfolio_nav.rolling(
            window=ma_window, min_periods=1
        ).mean()

        if reference_portfolio_nav.empty:
            raise HTTPException(
                status_code=400, detail="Insufficient data for calculation"
            )

        # Get current (latest) values
        current_price = reference_portfolio_nav.iloc[-1]
        current_ma = ma_series.iloc[-1]
        latest_nav = float(reference_portfolio_nav.iloc[-1])
        current_price = latest_nav
        current_ma = float(ma_series.iloc[-1])
        target_ratio, market_signal = calculate_target_ratio(
            current_price, current_ma, request.min_weight, request.max_weight
        )

        # Calculate current equity value
        # Handle 'RiskFree' if passed (filter it out for equity calc)
        equity_holdings = {
            k: v for k, v in request.current_holdings.items() if k != "RiskFree"
        }
        current_equity_value = sum(equity_holdings.values())
        risk_free_balance = request.current_holdings.get("RiskFree", 0.0)
        current_cash = request.current_cash

        # Calculate total wealth projected (Equity + RiskFree + Current Cash + New Budget)
        total_wealth_projected = (
            current_equity_value
            + risk_free_balance
            + current_cash
            + request.monthly_budget
        )

        # Calculate target equity value
        target_equity_value = total_wealth_projected * target_ratio

        # Calculate gap
        gap = target_equity_value - current_equity_value

        # Calculate recommended investment with Limits
        recommended_monthly_investment = 0.0

        if gap > 0:
            # Calculate available cash including new budget
            available_cash = risk_free_balance + current_cash + request.monthly_budget

            # Pre-calculate average buy fee rate based on weights
            total_weight = weights.sum()
            avg_fee = 0.0
            if total_weight > 0:
                avg_fee = (
                    sum(
                        request.buy_fee.get(code, 0.0) * w
                        for code, w in weights.items()
                    )
                    / total_weight
                )

            # Apply triple constraint: Gap (converted to gross), Budget Limit, Available Cash
            # gap is the NAV gap. To fill it, we need gap * (1 + avg_fee) cash.
            gross_gap = gap * (1 + avg_fee)
            limit = request.monthly_budget * request.max_buy_multiplier

            # recommended_monthly_investment is now the TOTAL CASH to be spent (Gross)
            recommended_monthly_investment = min(gross_gap, limit, available_cash)

        recommended_monthly_investment = max(0, recommended_monthly_investment)

        # Calculate Detailed Fund Advice
        fund_advice = []
        total_weight = weights.sum()
        positive_gap_sum = 0
        fund_gaps = {}

        # 1. Distribute Gap Calculation
        # Use a list of dict keys to ensure uniqueness if codes were somehow repeated
        unique_fund_codes = list(dict.fromkeys(request.fund_codes))
        for code in unique_fund_codes:
            w = weights.get(code, 0) / total_weight if total_weight > 0 else 0
            current_val = equity_holdings.get(code, 0)
            target_val = target_equity_value * w
            gap_val = target_val - current_val
            fund_gaps[code] = gap_val
            if gap_val > 0:
                positive_gap_sum += gap_val

        # 2. Determine Actions
        for code in unique_fund_codes:
            current_val = equity_holdings.get(code, 0)
            gap_val = fund_gaps.get(code, 0)
            target_val = current_val + gap_val

            # Reset action and amount for each fund to prevent leakage
            # Using a more robust if-elif-else structure
            action = "Hold"
            amount = 0.0
            reason = "持有"

            if recommended_monthly_investment > 0 and positive_gap_sum > 0:
                # We have money to spend
                if gap_val > 0:
                    action = "Buy"
                    # amount is the Gross Cash to spend on this fund
                    amount = recommended_monthly_investment * (
                        gap_val / positive_gap_sum
                    )
                    reason = f"价值平均补足缺口 (Gap: {gap_val:.1f})"
                else:
                    action = "Hold"
                    reason = "仓位已达标"
            elif recommended_monthly_investment <= 0:
                # Net recommendation is 0 or negative (Hold or Sell)
                if gap_val < 0:
                    # Check Sell Threshold
                    if abs(gap) > total_wealth_projected * request.sell_threshold:
                        action = "Sell"
                        amount = abs(gap_val)
                        reason = f"严重高估触发减持 (Gap: {gap_val:.1f})"
                    else:
                        action = "Hold"
                        reason = "未达卖出阈值"
                elif gap_val > 0:
                    action = "Hold"
                    reason = "低位观察不额外定投"
            else:
                # Handle cases where positive_gap_sum is 0 but recommended_monthly_investment > 0
                # which shouldn't happen but for safety we mark it as Hold
                action = "Hold"
                amount = 0.0
                reason = "仓位已平衡"

            # target_val is already calculated as the ideal target (current + gap)

            fund_advice.append(
                {
                    "code": code,
                    "name": fund_names.get(code, code),
                    "current_holding": current_val,
                    "target_holding": target_val,  # Ideal Target (Current + Gap)
                    "ideal_holding": target_val,  # This is Ideal Target (Current + Gap)
                    "gap": gap_val,
                    "action": action,
                    "amount": amount,
                    "reason": reason,
                }
            )

        # 3. Add RiskFree / Cash Entry
        # Note: In real life, fees are often deducted from the amount.
        # Here we assume recommended_monthly_investment is what's used.
        # But we need to account for specific fund fees if we want precise cash flow.
        total_buy_with_fees = 0.0
        for item in fund_advice:
            if item["action"] == "Buy":
                # item["amount"] is already the gross cash outflow
                total_buy_with_fees += item["amount"]

        # Sells: net proceeds
        total_sell_net_proceeds = 0.0
        for item in fund_advice:
            if item["action"] == "Sell":
                f = request.sell_fee.get(item["code"], 0.0)
                total_sell_net_proceeds += item["amount"] * (1 - f)

        net_cash_flow = (
            request.monthly_budget - total_buy_with_fees + total_sell_net_proceeds
        )

        risk_free_action = "持有"
        risk_free_amount = 0.0

        if net_cash_flow > 0:
            risk_free_action = "存入"
            risk_free_amount = net_cash_flow
        elif net_cash_flow < 0:
            risk_free_action = "取用"
            risk_free_amount = abs(net_cash_flow)

        # RiskFree Target Calculation
        target_risk_free_value = total_wealth_projected - target_equity_value
        risk_free_current = risk_free_balance + current_cash
        risk_free_gap = target_risk_free_value - risk_free_current

        fund_advice.append(
            {
                "code": "RiskFree",
                "name": "无风险资产 (现金/理财)",
                "current_holding": risk_free_current,
                "target_holding": target_risk_free_value,  # Ideal target (current + gap)
                "ideal_holding": target_risk_free_value,
                "gap": risk_free_gap,
                "action": risk_free_action,
                "amount": risk_free_amount,
                "reason": "余额管理" if risk_free_amount > 0 else "持仓平衡",
            }
        )

        return {
            "market_signal": market_signal,
            "target_equity_ratio": target_ratio,
            "current_equity_value": current_equity_value,
            "current_risk_free_value": risk_free_balance,
            "current_cash": current_cash,
            "target_equity_value": target_equity_value,
            "gap": gap,
            "recommended_monthly_investment": recommended_monthly_investment,
            "monthly_budget": request.monthly_budget,
            "latest_nav": latest_nav,
            "ma_value": float(current_ma),
            "current_price": float(current_price),
            "fund_names": fund_names,
            "fund_advice": fund_advice,
        }
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


def backtest_kelly_dca(
    df_nav,
    weights_dict,
    monthly_investment,
    initial_holdings=None,
    max_buy_multiplier=3.0,
    sell_threshold=0.05,
    min_weight=0.3,
    max_weight=0.8,
    buy_fee: Dict[str, float] = None,
    sell_fee: Dict[str, float] = None,
    ma_window: int = 12,
    risk_free_rate: float = 0.0,
):
    """
    Advanced Value Averaging (VA) Strategy.
    Note: Sometimes referred to as "Kelly DCA" in this codebase, but technically
    it implements Value Averaging by dynamically adjusting investment based on
    market valuation (Price vs MA bias).
    """
    weights = pd.Series(weights_dict).reindex(df_nav.columns).fillna(0)

    # Initialize holdings from initial_holdings if provided
    if initial_holdings is None:
        initial_holdings = {}

    initial_nav = df_nav.iloc[0]
    total_shares = pd.Series(0.0, index=df_nav.columns)

    # Convert initial holdings (in currency) to shares
    # IMPORTANT: Exclude 'RiskFree' - it should only be in cash_balance, not shares
    for code in df_nav.columns:
        if code == "RiskFree":
            continue  # Skip RiskFree, handled separately as cash_balance
        if code in initial_holdings and initial_holdings[code] > 0:
            total_shares[code] = initial_holdings[code] / initial_nav[code]

    # Handle Initial Cash (RiskFree only goes here, not in shares)
    cash_balance = initial_holdings.get("RiskFree", 0.0)

    # Calculate initial value excluding RiskFree double-counting
    initial_value = (
        sum(v for k, v in initial_holdings.items() if k != "RiskFree") + cash_balance
    )
    accumulated_investment = initial_value  # Total external money put in (principal)

    portfolio_history = {}
    attribution_history = {}
    invested_history = {}

    reference_portfolio_nav = df_nav.dot(weights)
    ma_series = reference_portfolio_nav.rolling(window=ma_window, min_periods=1).mean()

    # Calculate monthly risk-free return rate
    monthly_rf_rate = (
        (1 + risk_free_rate) ** (1 / 12) - 1 if risk_free_rate > 0 else 0.0
    )

    # Unit NAV Accounting
    total_units = 0.0
    if accumulated_investment > 0:
        total_units = accumulated_investment  # Initial units at 1.0

    unit_nav_history = {}

    for timestamp, nav_row in df_nav.iterrows():
        # 0. Apply Interest to Cash Balance (Start of Month)
        if cash_balance > 0:
            cash_balance *= 1 + monthly_rf_rate

        # --- Unit NAV Calculation Start ---
        # Calculate Wealth BEFORE new external inflow (income)
        current_equity_val_pre = (total_shares * nav_row).sum()
        wealth_pre = current_equity_val_pre + cash_balance

        if total_units > 0:
            unit_nav = wealth_pre / total_units
        else:
            unit_nav = 1.0

        unit_nav_history[timestamp] = unit_nav
        # --- Unit NAV Calculation End ---

        # 1. Income Step (External Inflow)
        cash_balance += monthly_investment
        accumulated_investment += monthly_investment

        # Buy Strategy Units
        if unit_nav > 0:
            total_units += monthly_investment / unit_nav

        # 2. Valuation Step (Post Income)
        current_equity_value = (total_shares * nav_row).sum()
        total_wealth = current_equity_value + cash_balance

        # 3. Target Ratio (Linear)
        current_price = reference_portfolio_nav.loc[timestamp]
        current_ma = ma_series.loc[timestamp]
        target_ratio, market_signal_current = calculate_target_ratio(
            current_price, current_ma, min_weight, max_weight
        )

        # 4. Rebalance Step
        target_equity_value = total_wealth * target_ratio
        diff = target_equity_value - current_equity_value

        if diff > 0:
            # Buy Limit: Min(Gap, Cash Balance considering fees, Budget * Multiplier)
            buy_limit = monthly_investment * max_buy_multiplier

            # Pre-calculate average fee rate to prevent cash overdraft
            total_weight = weights[weights > 0].sum()
            avg_fee = 0.0
            if total_weight > 0:
                avg_fee = (
                    sum(
                        (buy_fee or {}).get(code, 0.0) * w
                        for code, w in weights[weights > 0].items()
                    )
                    / total_weight
                )

            # Calculate max buyable amount considering fees
            # buy_amount * (1 + avg_fee) <= cash_balance
            max_buyable_with_fees = (
                cash_balance / (1 + avg_fee) if avg_fee < 1 else cash_balance
            )

            buy_amount = min(diff, max_buyable_with_fees, buy_limit)

            if buy_amount > 0:
                # Buy shares and deduct fees
                total_cost_with_fees = 0.0
                for code, w in weights.items():
                    if w > 0:
                        f = (buy_fee or {}).get(code, 0.0)
                        amt = buy_amount * w
                        total_cost_with_fees += amt * (1 + f)
                        total_shares[code] += amt / nav_row[code]

                cash_balance -= total_cost_with_fees
        elif diff < 0:
            # Sell Limit: Only if gap > threshold
            if abs(diff) > total_wealth * sell_threshold:
                sell_amount = abs(diff)
                if sell_amount > 0:
                    net_proceeds = 0.0
                    for code, w in weights.items():
                        if w > 0:
                            f = (sell_fee or {}).get(code, 0.0)
                            # CRITICAL FIX: Cannot sell more than what we have (No short selling)
                            # target_amt_to_sell is sell_amount * weight
                            available_val = total_shares[code] * nav_row[code]
                            actual_amt_to_sell = min(sell_amount * w, available_val)

                            if actual_amt_to_sell > 0:
                                net_proceeds += actual_amt_to_sell * (1 - f)
                                total_shares[code] -= actual_amt_to_sell / nav_row[code]
                    cash_balance += net_proceeds

        # 5. Record State
        current_asset_values = total_shares * nav_row
        attribution_dict = current_asset_values.to_dict()
        attribution_dict["RiskFree"] = cash_balance
        attribution_history[timestamp] = attribution_dict

        total_portfolio_value = current_asset_values.sum() + cash_balance
        portfolio_history[timestamp] = total_portfolio_value
        invested_history[timestamp] = accumulated_investment

    portfolio_series = pd.Series(portfolio_history)
    unit_nav_series = pd.Series(unit_nav_history)

    max_drawdown_nav = calculate_max_drawdown(unit_nav_series)
    max_drawdown_value = calculate_max_drawdown(portfolio_series)

    # Calculate Annualized Return (CAGR based on Strategy Unit NAV)
    days = (df_nav.index[-1] - df_nav.index[0]).days
    years = days / 365.25 if days > 0 else 0
    annualized_return = 0.0

    final_unit_nav = (
        float(unit_nav_series.iloc[-1]) if not unit_nav_series.empty else 1.0
    )

    if years > 0:
        if final_unit_nav > 0:
            annualized_return = (final_unit_nav) ** (1 / years) - 1
        else:
            annualized_return = -1.0

    return {
        "total_invested": accumulated_investment,
        "final_value": list(portfolio_history.values())[-1],
        "final_unit_nav": final_unit_nav,
        "annualized_return": annualized_return,
        "max_drawdown": float(max_drawdown_nav),
        "max_drawdown_value": float(max_drawdown_value),
        "max_drawdown_nav": float(max_drawdown_nav),
        "market_signal": market_signal_current,
        "history": {
            date.strftime("%Y-%m"): value for date, value in portfolio_history.items()
        },
        "attribution": {
            date.strftime("%Y-%m"): value for date, value in attribution_history.items()
        },
    }


@router.post("/backtest_strategies")
async def run_strategy_backtests(request: StrategyBacktestRequest):
    try:
        fund_df, _, _ = get_fund_data(
            request.fund_codes,
            request.start_date,
            request.end_date,
            request.risk_free_rate,
        )

        monthly_returns = fund_df.pct_change().fillna(0)
        for code in monthly_returns.columns:
            if code == "RiskFree":
                continue
            monthly_fee = request.fund_fees.get(code, 0) / 12
            monthly_returns[code] -= monthly_fee

        nav_adjusted = (1 + monthly_returns).cumprod()

        num_months = len(fund_df)
        total_lump_sum_investment = request.monthly_investment * num_months
        lump_sum_results = backtest_lump_sum(
            nav_adjusted,
            request.weights,
            total_lump_sum_investment,
            initial_holdings=request.initial_holdings,
        )

        dca_results = backtest_dca(
            nav_adjusted,
            request.weights,
            request.monthly_investment,
            initial_holdings=request.initial_holdings,
        )

        kelly_results = backtest_kelly_dca(
            nav_adjusted,
            request.weights,
            request.monthly_investment,
            request.initial_holdings,
            request.max_buy_multiplier,
            request.sell_threshold,
            request.min_weight,
            request.max_weight,
            request.buy_fee,
            request.sell_fee,
            request.ma_window,
            risk_free_rate=request.risk_free_rate or 0.0,
        )

        return {
            "lump_sum": lump_sum_results,
            "dca": dca_results,
            "kelly_dca": kelly_results,
        }
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


app.include_router(router, prefix="/api")
app.mount("/", StaticFiles(directory="static", html=True), name="static")
