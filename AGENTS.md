# Repository Guidelines

## Project Structure & Module Organization
`backend/` contains the FastAPI service. Main application logic lives in `backend/main.py`, and backend tests live in `backend/tests/`. `frontend/` is a React app; UI code lives in `frontend/src/`, and static public assets live in `frontend/public/`. Production frontend output is copied into `backend/static/` by `start.sh`, so treat that directory as generated build output.

## Build, Test, and Development Commands
Use `cd backend && uv sync` to install Python dependencies and `cd frontend && npm install` for the React app. Run `bash start.sh` from the repo root to build the frontend, move the build into `backend/static/`, upgrade `akshare`, and start the unified server on `http://localhost:8666`. Run backend tests with `bash test.sh` or `cd backend && uv run pytest`. Run frontend tests with `cd frontend && npm test`. Create a production frontend build with `cd frontend && npm run build`.

## Coding Style & Naming Conventions
Python targets 3.11+ and uses 4-space indentation, snake_case for functions and variables, and descriptive constant names in ALL_CAPS. Frontend code uses 4-space indentation, PascalCase for React components (`PortfolioOptimizer.js`), camelCase for hooks/state, and colocated CSS such as `App.css`. Formatting and lint cleanup are enforced through `pre-commit` with `ruff` and `ruff-format`; run `pre-commit run --all-files` before opening a PR when touching Python or repo-wide files.

## Testing Guidelines
Backend tests use `pytest` and live under `backend/tests/` with `test_*.py` names. Frontend tests use React Testing Library and Jest conventions, for example `src/App.test.js`. Add a focused regression test for every behavior change, especially around strategy calculations, drawdown logic, fee handling, and i18n-visible UI changes. No explicit coverage gate is configured, so keep tests targeted and meaningful.

## Portfolio Domain Semantics
This project has three distinct asset buckets, and code changes must not collapse them together:

- `Risk assets`: the user-selected funds/ETFs that participate in efficient-frontier optimization and tactical allocation.
- `RiskFree`: a money-market / low-volatility fund sleeve. It has yield, is modeled as approximately zero risk, and is a real portfolio asset rather than idle cash.
- `Cash`: idle cash available for rebalancing. It has no return and no risk, is not part of the efficient frontier, and must never be silently merged into `RiskFree`.

When implementing or changing portfolio logic, keep these invariants:

- Efficient-frontier analysis and selected target weights describe the theoretical target portfolio weights for the current analysis universe.
- Actual backtests start from the user’s real holdings plus separate cash, then simulate trading, fees, and constraints against that selected target.
- Investment recommendations must treat `RiskFree` and `Cash` as separate rows/flows: `RiskFree` can be bought or sold as an asset; `Cash` is only liquidity and reserve management.
- `risk_free_rate` applies to the `RiskFree` sleeve only. It must not be used to give cash an implied yield.
- If incoming weights reference assets outside the current analysis universe, fail explicitly instead of silently dropping or renormalizing them.

## Commit & Pull Request Guidelines
Recent history follows Conventional Commit style: `feat: ...`, `fix(scope): ...`, and `chore: ...`. Keep commits narrowly scoped and written in the imperative mood. PRs should include a short problem statement, a summary of the solution, test evidence (`uv run pytest`, `npm test`, or both), and screenshots when frontend behavior changes. Call out API contract changes or strategy logic changes explicitly.
