# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Quant Compass is a quantitative investment analysis tool for Chinese fund markets. Users input fund codes and fees, the system backtests historical returns, generates an efficient frontier, and provides monthly investment/rebalancing recommendations using Value Averaging (VA) / fractional Kelly strategies.

## Commands

### Install
```bash
cd backend && uv sync          # Python deps
cd frontend && npm install     # Node deps
```

### Run (unified server on port 8666)
```bash
bash start.sh                  # Builds frontend → backend/static/, starts FastAPI
```

### Dev (separate processes)
```bash
cd backend && uv run uvicorn main:app --host 0.0.0.0 --port 8666
cd frontend && npm start        # Port 3000, proxies API to 8666
```

### Test
```bash
bash test.sh                   # All backend tests (pytest)
cd backend && uv run pytest tests/test_analyze.py  # Single file
cd frontend && npm test        # Frontend tests (Jest, watch mode)
```

### Lint
```bash
pre-commit run --all-files     # Ruff lint + format (run before PRs)
```

### Build frontend only
```bash
cd frontend && npm run build
```

## Architecture

### Backend (`backend/main.py` — ~2200 lines, monolithic)

Single FastAPI app serving three core endpoints:

| Endpoint | Purpose |
|---|---|
| `POST /api/analyze` | Fetch AkShare data, compute efficient frontier via SciPy optimization |
| `POST /api/backtest_strategies` | Simulate 3 strategies over historical period |
| `POST /api/current_recommendation` | Real-time allocation advice given current holdings |

**Data flow:**
1. AkShare fetches monthly NAV for each fund code
2. `efficient_frontier()` uses SciPy `minimize` to trace the frontier (Sharpe/CVaR-constrained)
3. User selects a frontier point → `backtest_strategies()` simulates Lump Sum, DCA, and Kelly DCA month-by-month
4. `current_recommendation()` uses current holdings + rolling 36-month window to advise next month's action

**Strategy modes:**
- `optimized_kelly` (default): Fractional Kelly with CVaR (95%, 8% cap) and Max Drawdown (20% cap) hard constraints
- `legacy_linear`: Linear Price/MA250 mapping, no hard constraints (backward-compat only)

**Key constants (top of `main.py`):** `DEFAULT_KELLY_FRACTION = 0.5`, `DEFAULT_ESTIMATION_WINDOW = 36`, `DEFAULT_CVAR_LIMIT = 0.08`, `DEFAULT_MAX_DRAWDOWN_LIMIT = 0.20`

### Frontend (`frontend/src/`)

React 19 SPA. Primary component is `PortfolioOptimizer.js` (~70KB), which manages the full multi-step workflow:
1. Fund input + fee configuration (including RiskFree fund separation)
2. Efficient frontier chart (ECharts scatter) with user-selectable target point
3. Backtest comparison chart (Lump Sum vs DCA vs Kelly DCA)
4. Monthly recommendation panel

State is persisted to `localStorage` (dates, investment amounts, holdings). Language toggle (EN/ZH) via `LanguageContext.js`; all UI strings in `frontend/src/i18n/translations.js`.

**Other components:** `DualMovingAverage.js` (technical analysis), `ValueInvesting.js` (fundamental screening) — these are independent tabs.

### Key Risk Metrics

- **Max Drawdown (NAV-based)**: Primary risk metric — fund-accounting style, unaffected by cash inflows. Prefer this over market-value drawdown.
- **CVaR**: Expected loss in worst 5% of months, used as optimization constraint.

## Portfolio Domain Semantics

This project has three distinct asset buckets. Code changes must never collapse them together:

- **Risk assets**: user-selected funds/ETFs that participate in efficient-frontier optimization and tactical allocation.
- **RiskFree**: a money-market / low-volatility fund sleeve. It has yield, is modeled as approximately zero risk, and is a real portfolio asset — not idle cash.
- **Cash**: idle liquidity available for rebalancing. No return, no risk, not part of the efficient frontier. Must never be silently merged into `RiskFree`.

Invariants to maintain when implementing or changing portfolio logic:
- Efficient-frontier target weights describe the theoretical allocation for the analysis universe only.
- Actual backtests start from the user's real holdings + separate cash, then simulate trading, fees, and constraints against that target.
- `RiskFree` and `Cash` must remain separate rows/flows in recommendations: `RiskFree` is bought/sold as an asset; `Cash` is reserve management only.
- `risk_free_rate` applies to the `RiskFree` sleeve only — never give `Cash` an implied yield.
- If incoming weights reference assets outside the current analysis universe, fail explicitly rather than silently dropping or renormalizing them.

## Coding Conventions

- Python: `snake_case`, `ALL_CAPS` constants, 4-space indent, 3.11+ features OK
- Frontend: `PascalCase` components, `camelCase` hooks/state, colocated CSS; formatting enforced via `ruff` / `ruff-format` pre-commit hooks
- Commits: Conventional Commits (`feat:`, `fix(scope):`, `chore:`), imperative mood, narrowly scoped
- PRs must include test evidence (`uv run pytest`, `npm test`, or both) and screenshots for frontend changes; call out API contract or strategy logic changes explicitly

## Testing Notes

Backend tests mock AkShare responses for reproducibility. When changing strategy math (Kelly, CVaR, drawdown), add a regression test in `backend/tests/`. Focused tests covering fee handling, drawdown logic, and i18n-visible changes are required. No explicit coverage gate — keep tests targeted and meaningful.
