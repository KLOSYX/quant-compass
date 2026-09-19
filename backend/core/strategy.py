from fastapi import HTTPException

from core.constants import DEFAULT_RISK_HORIZON_DAYS, VALID_STRATEGY_MODES


def validate_strategy_params(
    *,
    strategy_mode: str,
    estimation_window: int,
    minimum_cash_reserve: float,
    enable_cvar_constraint: bool,
    cvar_confidence: float,
    cvar_limit: float,
    enable_drawdown_constraint: bool,
    max_drawdown_limit: float,
    risk_horizon_days: int = DEFAULT_RISK_HORIZON_DAYS,
):
    if strategy_mode not in VALID_STRATEGY_MODES:
        raise HTTPException(
            status_code=400,
            detail=f"strategy_mode must be one of {sorted(VALID_STRATEGY_MODES)}",
        )

    if estimation_window < 6:
        raise HTTPException(status_code=400, detail="estimation_window must be >= 6")
    if minimum_cash_reserve < 0:
        raise HTTPException(status_code=400, detail="minimum_cash_reserve must be >= 0")
    if not isinstance(enable_cvar_constraint, bool):
        raise HTTPException(
            status_code=400, detail="enable_cvar_constraint must be a boolean"
        )
    if not isinstance(enable_drawdown_constraint, bool):
        raise HTTPException(
            status_code=400, detail="enable_drawdown_constraint must be a boolean"
        )
    if not (0.5 < cvar_confidence < 0.999):
        raise HTTPException(
            status_code=400, detail="cvar_confidence must be in (0.5, 0.999)"
        )
    if not (0 < cvar_limit < 1):
        raise HTTPException(status_code=400, detail="cvar_limit must be in (0, 1)")
    if not (0 < max_drawdown_limit < 1):
        raise HTTPException(
            status_code=400, detail="max_drawdown_limit must be in (0, 1)"
        )
    if not (5 <= risk_horizon_days <= 63):
        raise HTTPException(
            status_code=400, detail="risk_horizon_days must be within [5, 63]"
        )
