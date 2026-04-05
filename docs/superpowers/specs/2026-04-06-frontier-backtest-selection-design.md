# Frontier Backtest Selection Design

## Summary

Fix the mismatch between efficient-frontier point selection and historical backtest behavior.

Current behavior allows different frontier points to collapse into the same backtest result because the backtest path strips out `RiskFree`, renormalizes the remaining risky weights, and then runs the dynamic Kelly/VA logic on that normalized risky basket only.

Target behavior:

- A selected frontier point represents the full target allocation.
- The selected point must affect both:
  - risky asset internal composition
  - `RiskFree` sleeve size
- Backtest results for materially different frontier points must no longer be identical under the same historical window and strategy parameters.

## Problem Statement

The UI sends `selectedPoint.weights` from the theoretical frontier into `/api/backtest_strategies`.

In the current backend:

- `backtest_kelly_dca()` calls `normalize_risky_weights(weights_dict, list(df_nav.columns))`
- this drops `RiskFree`
- the remaining risky weights are renormalized to sum to `1.0`
- the strategy then computes a dynamic target equity ratio independently of the original frontier point's `RiskFree` allocation

As a result, multiple frontier points can produce the same effective risky basket and therefore the same backtest trajectory, especially when the main difference between points is the cash sleeve.

## Goals

- Preserve the meaning of frontier selection as a full portfolio choice.
- Keep the current UI interaction unchanged.
- Keep the current API request shape unchanged.
- Preserve Kelly/VA dynamic adjustment behavior.
- Ensure low-risk and high-risk frontier points produce meaningfully different backtest outputs.

## Non-Goals

- Redesign the portfolio optimizer UI.
- Replace Kelly/VA with a purely static rebalance strategy.
- Split the product into separate static and dynamic backtest modules in this change.
- Change request or response field names unless strictly required.

## Design

### Selected Point Semantics

A frontier point will be interpreted as:

- `base_weights`: the full selected portfolio, including `RiskFree`
- `base_risky_weights`: the risky subset of `base_weights`, normalized within the risky sleeve only
- `base_risky_ratio`: total risky allocation implied by the selected point
- `base_risk_free_ratio`: total `RiskFree` allocation implied by the selected point

This means the selected point is no longer treated as a hint for risky composition only. It becomes the baseline allocation that dynamic strategy logic operates around.

### Backtest Strategy Semantics

The dynamic Kelly/VA layer will continue to compute a tactical adjustment, but that adjustment must scale the selected point's baseline risky sleeve instead of replacing it.

Operationally:

- `base_risky_weights` determines how risky capital is split among funds
- dynamic strategy computes a tactical risky exposure scalar for the current month
- final target risky exposure is derived from:
  - selected point baseline risky ratio
  - dynamic tactical adjustment

The exact implementation should use a bounded multiplier against the baseline risky ratio, not a fresh whole-portfolio target that ignores the selected point.

### Target Equity Calculation

Current behavior:

- `target_ratio` is treated like a whole-portfolio risky allocation ratio

New behavior:

- `target_ratio` becomes a tactical scale applied to the selected point's baseline risky sleeve
- final risky target ratio is computed from:
  - `base_risky_ratio`
  - tactical adjustment from Kelly/VA
  - cash reserve and risk constraints

Reference semantics:

- conservative point + aggressive tactical signal still stays more conservative than an aggressive point under the same signal
- aggressive point + defensive tactical signal still remains distinguishable from a conservative baseline point

### Recommendation Endpoint Semantics

`/api/current_recommendation` must follow the same interpretation as backtests.

That endpoint currently also drops `RiskFree` and works only from renormalized risky weights. It must be updated so:

- latest recommendation reflects the full selected point
- target equity value is derived from baseline risky ratio plus tactical overlay
- fund-level advice still uses normalized risky subweights for distribution across risky assets
- `RiskFree` advice reflects the selected point's retained cash sleeve

### Frontend Compatibility

Frontend request behavior remains unchanged:

- click frontier point
- send `selectedPoint.weights`
- render returned backtest results

No frontend API shape changes are required for this fix.

Optional future improvement, out of scope for this change:

- show both full weights and risky-sleeve weights more explicitly in the UI

## Implementation Outline

1. Introduce helpers that decompose full selected weights into:
   - full normalized weights
   - baseline risky ratio
   - normalized risky-only subweights
2. Update `backtest_kelly_dca()` to preserve selected-point `RiskFree` allocation.
3. Update `/current_recommendation` to use the same semantics.
4. Keep `effective_risky_weights` as the risky sub-basket descriptor, but do not treat it as the full selected portfolio.
5. Add regression tests covering distinct frontier points with identical risky composition but different `RiskFree` sleeves.

## Error Handling

- If the selected point contains only `RiskFree`, the strategy should behave as cash-only:
  - no risky purchases
  - stable cash-driven path
  - no divide-by-zero or empty-basket failures
- If the selected point contains risky assets but baseline risky ratio is very small, the system should still produce valid low-exposure behavior.
- If constraints force tactical risky exposure lower than the baseline-implied target, the constrained result should remain valid and should not renormalize away the selected cash sleeve.

## Testing

### Unit Tests

- Decomposition helper returns correct:
  - full weights
  - risky-only weights
  - risky ratio
  - risk-free ratio
- Cash-only selected point remains cash-only in backtest.
- Two selected points with same risky subweights but different `RiskFree` ratios produce different backtest outcomes.

### API Tests

- `/api/backtest_strategies` returns different `kelly_dca` outputs for a conservative and aggressive selected point under the same market history.
- `/api/current_recommendation` returns different target equity values for those same two points.

### Regression Coverage

The main regression to lock:

- selecting different efficient-frontier points must not yield identical backtest results merely because risky weights were renormalized after dropping `RiskFree`

## Acceptance Criteria

The change is complete when:

1. Selecting low-risk and high-risk frontier points yields different backtest metrics under the same date range and strategy parameters.
2. A near-100% `RiskFree` point produces a materially more conservative backtest than a high-risk point.
3. Current recommendation output changes appropriately with selected point.
4. Existing relevant tests still pass.
5. New regression tests fail on the old behavior and pass on the new behavior.

## Risks

- If the tactical scaling formula is chosen poorly, the strategy may become too conservative or too aggressive relative to previous expectations.
- If recommendation and backtest semantics diverge, the product will remain internally inconsistent.
- If baseline risky ratio is applied twice, the selected point effect could be overstated.

## Decision

Implement the fix by preserving selected-point full-allocation semantics and applying Kelly/VA as a tactical overlay on top of the selected baseline risky sleeve.
