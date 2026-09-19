# Repository Guidelines

## 项目目标与产品边界

本项目首先是供项目所有者每月定投使用的家庭资产配置决策工具。每月更新真实持仓、独立现金余额和当月新增投入额度后，系统应明确回答：本月资金如何分配到各资产类别和具体标的、各投入多少、如何执行，以及执行后组合是什么样。分析、图表、有效前沿和回测服务于这一决策闭环。

本节是后续开发和验收的产品要求，不表示现有实现已经满足。发现旧实现、默认参数或说明文档与这些要求冲突时，应明确记录差距并修正；不得将当前算法或历史行为视为不可变的产品目标。

### 每月输入与输出

- 输入包括截至明确日期的真实持仓及估值、现有 Cash、当月新增资金、最低现金保留额、用户认可的投资标的与分类、实际适用的申购/赎回费用和交易限制。新增资金与已有现金必须分别记录，避免重复计入；涉及持有期费率、结算时间或已使用额度时，应补齐对应信息或明确采用的假设。
- 主结果必须提供一个明确的推荐方案，包含资产类别目标、具体标的买入/卖出/持有金额、费用、执行后持仓与权重、Cash 余额、现金底线缺口（如有），以及未投入金额的去向和原因。多个研究候选不能替代默认推荐方案。
- 分别展示长期/理论目标、本月受约束的可执行方案和执行后的实际状态，并量化未达目标的偏差。不能把理想权重直接当成从真实持仓出发就能完成的交易。
- 全部新增资金和已有现金的使用必须可以对账；买入支出应区分费用与净资产增加，卖出应区分资产减少、费用、已到账和待到账资金。分批交易需说明批次金额、可执行日期或条件及尚未执行金额。
- 下月以用户更新的实际持仓和现金为准重新决策，不假设上月建议已全部成交。

### 实际限制与优化目标

- 可用资金、最低 Cash 保留额、申购日限额/月限额、交易资格、赎回限制、结算到账时间及适用费用属于必须真实建模的执行条件。不得为了给出建议而忽略限额、漏计费用、使用未到账资金或挪用现金底线。
- 按用户最新确认，不引入日历计算。高级设置分别配置规划周期自然天数（默认30）和预计可交易天数（默认21）；日限额乘可交易天数估算分批投入总额，不按打开页面时距离月底的天数缩减。真实月限额及已用额度仍约束计划，不自动假定跨月额度重置。可交易日数不得超过自然天数；这是一项允许误差的规划假设，不代表已核验的渠道可购日期。
- 收益、风险、分散度、目标配置偏差、换手和交易成本之间的取舍属于优化问题。每个限制或惩罚项都应说明属于实际限制、用户明确偏好还是模型内部规则；不能把未经确认的内部阈值变成阻断投资的产品要求。
- 当初始状态已违反现金底线或其他约束时，报告缺口及当前允许操作下可执行的修复安排；无法当月修复时给出仍未满足的金额和后续条件，不得伪称所有约束已满足。
- 风险指标是依赖数据、窗口和假设的估计。必须区分历史/模型阈值与未来实际风险，不能把历史回撤约束表述为未来不超过该回撤的保证。

### 受限情况下仍须给出资金安排

- 限购、目标暂时不可达、统计证据不足、没有通过研究筛选的组合或优化器失败，不能仅返回空推荐、零买入或报错并结束流程。应保留诊断，并给出符合已知实际限制的具体备用安排。
- 备用安排可以包含允许范围内的重新分配、经用户认可的替代标的、分批投入、补足现金储备、购买可执行的 RiskFree 标的或明确保留 Cash。具体优先级依用户确认的政策执行；不得擅自扩大投资标的范围或把 Cash 当作有收益的资产。
- “有明确方案”要求每笔资金有金额、去向和理由，不要求在不存在合理可执行交易时强行买入。暂存必须说明暂存位置、金额、阻碍投入的条件及下次评估时点或触发条件；不能仅用泛泛的风险提示替代当月安排。
- 数据质量不够时，明确区分模型推荐与备用资金安排，说明缺失数据和可信度。可以沿用仍适用的已确认目标或采用已约定的备用规则，但不得编造行情、收益、可购状态或最优性结论。
- 非法数值、资产范围冲突、账目不一致等输入错误仍必须明确拒绝相应计算，并指出需修正的字段；不能静默丢弃资产或重归一化权重。可独立核实的资金安排可继续输出；连资金和限制都无法核实时，应明确列出修复步骤，不能捏造交易金额。

### 算法依据、精度与可靠性

- 对目标组合选择、战术仓位、当月交易优化及备用规则，分别记录目标函数、变量、约束、优先级、参数含义、适用假设及可核查的理论/文献来源。启发式规则应明确标注，并解释为什么适合本项目。
- 不预先把 Kelly、均值方差、择时信号或任何现有方法视为最终答案。算法选择应服务于家庭长期定投，在实际交易条件下与简单可解释的定投基线比较；复杂度增加需有验证证据支持。
- 区分计算精度、求解质量与预测不确定性：检查金额舍入、资金守恒、约束残差、求解收敛及备用结果可行性；不能用更多小数位或一次求解成功代表预测可靠，也不能将局部解/启发式结果宣称为已证明的全局最优。
- 回测从真实持仓、独立 Cash 和逐期新增资金出发，使用与建议一致的费用、限额和执行语义。采用无未来信息的样本外/滚动验证，检查不同市场阶段、估计窗口和参数扰动下的稳定性；用于调参或选择策略的数据不得冒充独立验证集。
- 记录数据来源、时间截点、输入、算法版本、推荐理由、约束及备用原因，支持复现与审计。将“计算正确”“历史验证结果”“未来表现不确定”分别表达。
- 验收应覆盖：正常月度投入、单只/全部标的限购、现金储备不足、预算为零、持仓偏离目标、费用及到账限制、数据不足、无候选通过筛选、求解失败，以及 RiskFree 与 Cash 分离。每个场景都验证资金去向、约束状态和用户可执行的下一步。

### 已确认的配置政策与范围

- 2026-09-19 已确认：仅保留原页面及原操作入口，固定已选目标定投，移除 Kelly 运行代码及参数，保留历史验证结论；重新分析不能静默替换保存的目标。
- 候选仍由系统生成，用户明确确认后固定；每月仅优化跟踪该目标的交易。模型统计量不得冒充收益预测；基线数学定义及参数去留见 [MODEL.md](analysis/zero_baseline/MODEL.md)。
- 新增资金优先，明显偏离时以尽快恢复目标为主建议必要卖出；费用真实核算但不设任意费用预算阻断。已有在途订单时不重复卖出，普通再平衡卖出须有可购标的承接，待到账款不能提前使用。

- 当前核心范围是用户认可的资产集合内的月度配置与人工执行指导。全市场选品、自动下单和短线交易不属于本轮目标定义；需要时另行明确范围。
- 优先使用当月新增资金改善配置；必要时可以建议卖出现有持仓再平衡，但必须说明必要性、费用、到账时间及对配置的影响。
- 限购时优先在用户认可的标的内重新分配或分批买入；仍无法合理投入的金额必须给出明确暂存安排。不得仅为维持理想权重而直接放弃生成方案。
- CVaR、回撤等模型指标作为优化偏好与风险提示，不因估计超标取消当月方案，也不默认作为使候选全部被排除的硬门槛。超标时说明风险、配置取舍及缓解措施；真实现金底线和交易限制仍必须遵守。
- 后续算法设计需明确长期投资期限、可接受的风险取舍与优化目标；未经确认不得默认“收益最大”“Sharpe 最大”或“当月必须花完预算”等同于用户目标。

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
