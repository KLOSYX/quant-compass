# All module-level configuration constants for quant-compass backend.
# FUND_LIST_CACHE lives in core/data.py (it is runtime state, not config).

MAX_SINGLE_WEIGHT = 0.5  # prevent over-concentration in a single fund
MIN_WEIGHT_THRESHOLD = 0.01  # drop tiny weights that are hard to execute
DEFAULT_STRATEGY_MODE = "optimized_kelly"
VALID_STRATEGY_MODES = {DEFAULT_STRATEGY_MODE, "legacy_linear"}
DEFAULT_KELLY_FRACTION = 0.5
DEFAULT_ESTIMATION_WINDOW = 36
OPTIMIZED_SIGNAL_EPSILON = 0.02
DEFAULT_CVAR_CONFIDENCE = 0.95
DEFAULT_CVAR_LIMIT = 0.08
DEFAULT_MAX_DRAWDOWN_LIMIT = 0.20
RISK_RATIO_GRID_STEP = 0.005
RETURN_SHRINKAGE = 0.35
COVARIANCE_SHRINKAGE = 0.20
DEFAULT_APPLY_FUND_FEES_TO_HISTORY = False
MIN_WALK_FORWARD_TRAIN_MONTHS = 24
