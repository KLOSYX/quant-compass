# Quant Compass 🧭

> 为每月定投提供明确的资金分配指导。Monthly allocation guidance for long-term household investing.

[English](#english) | [简体中文](#chinese)

<a name="english"></a>

## English

Quant Compass is a household asset allocation tool for monthly investing. Its purpose is to turn updated holdings, a separate cash balance, and each month's new contribution into a clear plan: which asset categories and funds to allocate to, how much to invest, and how to execute within real constraints. Analysis, charts, and backtests support that decision.

### Monthly workflow and required outcome

The following describes the agreed product requirements. Implementation gaps are listed below; these requirements are not a claim that every behavior is already implemented. [AGENTS.md](AGENTS.md) contains the detailed scope and acceptance criteria.

1. Update actual holdings, idle cash, and the new monthly contribution separately, with a valuation date.
2. Check the approved investment universe, minimum cash reserve, purchase/redemption fees, limits, and settlement conditions.
3. Receive one recommended plan with category targets, per-fund buy/sell/hold amounts, fees, projected holdings and weights, remaining cash, and the destination of any uninvested money.
4. Execute manually and use actual holdings and cash as the starting point next month; a recommendation is not assumed to have been filled.

The theoretical target portfolio, this month's executable trades, and the resulting actual allocation must be shown separately.

### Allocation policy and boundaries

- **Use new money first.** Recommend selling existing holdings when necessary, explaining the reason, costs, and settlement timing.
- **Respect real execution conditions.** Available funds, minimum Cash, purchase limits, redemption restrictions, and fees must be modeled. Daily limits must reflect planned purchase dates and remaining capacity.
- **Treat model risk as an optimization preference.** CVaR and drawdown estimates inform allocation and risk explanations; exceeding an estimate should not cancel the monthly plan. Historical or modeled risk is not a guarantee about future losses.
- **Provide an alternative when constrained.** Prioritize reallocation within approved assets or staged purchases. Any remainder needs a specific temporary destination, amount, reason, and review condition. Limits, insufficient statistical evidence, or solver failure must not end with only an empty recommendation.
- **Keep inputs honest.** Invalid amounts or assets outside the analysis universe must be reported explicitly. If balances or constraints cannot be verified, identify the required corrections instead of inventing trades.

The core scope is monthly allocation within user-approved assets and guidance for manual execution. Whole-market product selection, automatic order placement, and short-term trading are outside this scope.

### Three separate asset buckets

| Bucket | Meaning |
| :-- | :-- |
| Risk assets | Selected funds/ETFs participating in portfolio optimization and target allocation. |
| RiskFree | A real money-market / low-volatility fund sleeve with yield, modeled as approximately zero risk. Purchases and redemptions are asset transactions. |
| Cash | Idle liquidity with no return; excluded from the efficient frontier and used for reserves and settlement. |

`risk_free_rate` applies only to RiskFree. Cash must not receive an implied yield or silently become RiskFree; the minimum cash reserve is held as Cash.

### Existing implementation and remaining work

The existing page retains target selection, holdings, monthly recommendations and backtests. Its only running strategy is `fixed_weight`: retain the selected target, use new contributions first, and suggest necessary sales when the remaining deviation exceeds the execution tolerance. Kelly and legacy tactical implementations and parameters have been removed; [the historical decision and evidence](analysis/KELLY_DECISION.md) remain.

Reanalysis does not overwrite a saved target. The default 2-percentage-point tolerance suppresses small trades; it is a transparent execution heuristic, not a calibrated optimum. Rebalancing minimizes fee-adjusted target deviation, then prefers fewer sales. Sales require receiving purchase capacity, and their proceeds stay pending until settlement. Existing cash can be reinvested only through `available_existing_cash`. CVaR and drawdown provide warnings and do not scale or cancel the monthly allocation.

Recommendations and backtests share the monthly planner. The existing comparison charts show contribution-only DCA, lump sum and fixed-target rebalancing, not multiple user workflows. External weights must be non-negative, sum to one, and refer only to the current universe. Small theoretical weights are retained; no implicit 50% single-fund cap is imposed.

Projected-account risk uses executed asset exposures with separate zero-return Cash and receivables. It is a historical scenario estimate, not the theoretical target's risk or a future guarantee. Actual channel calendars, holding-period fee tiers and settlement dates still need user confirmation. See [algorithm assumptions and validation](ALGORITHM_DESIGN.md).

### Algorithm quality requirements

Document each method's objective, constraints, assumptions, parameters, and verifiable theoretical sources; label heuristics explicitly. Validate monetary rounding, accounting, solver convergence, constraint residuals, and fallback feasibility. Compare complete executable strategies against simple recurring-investment baselines using walk-forward/out-of-sample evidence, including fees, limits, and sensitivity to estimation windows. Data used to tune or select a strategy is not independent validation data.

Record input dates, data sources, algorithm versions, recommendation reasons, and fallback causes for reproducibility. Numerical correctness, historical evidence, and uncertainty about future performance must be reported separately.

### 🛠 Tech Stack

- **Backend**: Python 3.11+, FastAPI, Pandas, NumPy, AkShare (for market data).
- **Frontend**: React 19, Bootstrap 5, ECharts.
- **Dev Tools**: UV (for Python package management), Docker support.

### 🚀 Getting Started

#### Prerequisites

- Python 3.11+
- Node.js & npm
- [uv](https://github.com/astral-sh/uv) (recommended for backend)

#### Installation

1. **Clone the repository**:
   ```bash
   git clone https://github.com/your-username/quant-compass.git
   cd quant-compass
   ```

2. **Backend Setup**:
   ```bash
   cd backend
   # Using uv (recommended)
   uv sync
   # Or using pip
   pip install -r requirements.txt
   ```

3. **Frontend Setup**:
   ```bash
   cd ../frontend
   npm install
   ```

#### Running the Application

You can use the provided script to start both backend and frontend:

```bash
bash start.sh
```

On Windows, double-click `start.cmd`, or run the following command from
PowerShell:

```powershell
.\start.ps1
```

The Windows launcher installs missing frontend and backend dependencies, builds
the frontend, updates AkShare, and starts the application at
`http://localhost:8666`. To skip the AkShare update on a later startup, use
`.\start.ps1 -SkipAkshareUpdate`.

### Current API defaults

These describe the existing implementation, including legacy API field names retained for compatibility. They are not recommended personal settings.

| Parameter | Default | Meaning |
| :-- | :-- | :-- |
| `strategy_mode` | `fixed_weight` | Fixed selected target / 唯一固定目标策略 |
| `rebalance_band` | `0.02` | Execution tolerance, 2 percentage points / 执行容忍偏差 2 个百分点 |
| `available_existing_cash` | `0` | Authorized existing cash / 本月允许追加投入的存量现金 |
| `pending_sale_proceeds` | `0` | Confirmed unsettled net proceeds / 已确认未到账净款 |
| `estimation_window` | `36` | rolling monthly window for return/risk estimation |
| `minimum_cash_reserve` | `0` | minimum idle Cash balance, separate from RiskFree |
| `enable_cvar_constraint` | `true` | enable CVaR risk warning |
| `cvar_confidence` | `0.95` | CVaR confidence level |
| `cvar_limit` | `0.08` | CVaR preference threshold |
| `enable_drawdown_constraint` | `true` | enable drawdown risk warning |
| `max_drawdown_limit` | `0.20` | drawdown preference threshold |

### Reading results

NAV-based drawdown separates portfolio performance from external contributions. Market-value drawdown includes deposits and withdrawals and can obscure losses. Estimated returns and backtest returns cover different assumptions and periods; neither establishes future performance.

A zero purchase amount is not a complete monthly plan. Under the product requirements, it must be accompanied by the amount retained or redirected, the reason, and the next review condition. A RiskFree purchase and retained Cash must appear as separate flows.

---

<a name="chinese"></a>

## 简体中文

Quant Compass 是供每月定投使用的家庭资产配置决策工具。每月更新真实持仓、独立现金余额和新增投入额度后，项目的目标是明确回答：资金应分配到哪些资产类别和具体标的、各投入多少、如何在实际限制下执行。分析、图表和回测服务于这项决策。

### 每月使用流程与交付目标

以下是已确认的产品要求，不代表当前实现已全部满足；已知差距见下文。详细边界和验收要求记录在 [AGENTS.md](AGENTS.md)。

1. 分别更新真实持仓、闲置现金和当月新增资金，并注明估值日期，避免重复计入资金。
2. 核对认可的投资标的、最低现金保留额、申购/赎回费用、限购额度和到账条件。
3. 获得一个明确方案：资产类别目标、各标的买入/卖出/持有金额、费用、预计执行后持仓和权重、剩余现金，以及未投入资金的去向。
4. 人工执行；下月以实际持仓和现金重新决策，不假设上月建议已全部成交。

长期/理论目标、本月可执行交易和执行后的实际配置必须分别展示，并说明尚未达到目标的差距。

### 已确认的配置政策与边界

- **优先使用新增资金。** 必要时可以建议卖出现有持仓再平衡，但需说明理由、费用和到账时间。
- **遵守实际执行条件。** 可用资金、最低 Cash 保留额、申购限额、赎回限制及费用必须真实建模。日限额需对应计划购买日和剩余额度，不能简单乘整月工作日就宣称可以执行。
- **模型风险用于优化和提示。** CVaR、回撤等估计指标用于配置取舍与风险解释，不因估计超标取消当月方案；历史或模型风险不代表未来损失保证。
- **受限时仍需安排资金。** 优先在认可标的内重新分配或分批买入，剩余资金明确暂存位置、金额、原因和再次评估的条件。限购、统计证据不足、求解失败不能仅以空推荐结束。
- **不掩盖输入错误。** 非法金额、范围外资产等问题必须明确指出。资金或限制无法核实时，列出修复要求，不能编造交易金额。

核心范围是用户认可资产集合内的月度配置和人工执行指导。全市场选品、自动下单、短线交易不在当前范围内。

### 三类资产必须分开

| 类型 | 含义 |
| :-- | :-- |
| Risk assets | 参与组合优化与目标配置的用户选定基金/ETF。 |
| RiskFree | 有收益的货币基金/低波动基金资产，在模型中近似零风险；申购、赎回属于资产交易。 |
| Cash | 没有收益的闲置现金，不进入有效前沿，用于现金储备和交易结算。 |

`risk_free_rate` 只作用于 RiskFree。Cash 不能被赋予隐含收益或静默合并到 RiskFree；最低现金储备应保留为 Cash。

### 当前实现与待完善事项

保留原有目标选择、持仓输入、月度建议及回测页面，唯一运行策略为 `fixed_weight`：沿用已选目标，新增资金优先，必要时卖出以恢复配置。Kelly 和旧战术策略的运行代码、参数已移除，[历史验证结论与证据](analysis/KELLY_DECISION.md)保留。

系统生成候选后，首次目标也需确认。候选使用各标的自身历史均值与协方差，不再将债基收益向股票/黄金均值收缩；历史统计不是未来收益预测。带最优性验证的凸二次模型、数值证书与仍保留的经验参数见 [零基线模型](analysis/zero_baseline/MODEL.md)，离线检查见 [核验结果](analysis/zero_baseline/VALIDATION.md)。生产仍保留之前确认的固定20%协方差收缩，不自动切换 Ledoit–Wolf。

重新分析不自动覆盖已保存目标。点击前沿点或恢复推荐点先展示各标的新旧权重与百分点差异，明确确认后才保存并清除旧方案结果；可预览并撤销最近一次目标切换。目标及上一份目标保存在当前浏览器，清除浏览器数据后会丢失。

新增资金投入后仍超过默认 2 个百分点的偏差，才进入再平衡计算；这是一项明确的执行容忍规则，不是已校准的统计最优值。先改善扣费后的目标偏差，再减少不必要卖出。接收资金的标的须有购买容量；卖出款到账前单列，不能提前买入。存量现金只追加使用显式填写的 `available_existing_cash`。CVaR、回撤仅提示风险，不缩放目标或取消月度方案。

建议和回测共用月度执行器。原对比图保留新增资金定投、一次投入及固定目标再平衡对照，没有新增产品入口。外部权重必须非负、和为 1、属于当前资产范围；不再静默修改，也不设置未经确认的统一单基金 50% 上限或清除微小理论权重。

执行后账户风险按实际投影持仓计算，Cash 和待到账款单独处理；属于历史情景估计，不等同于理论目标风险或未来保证。真实渠道交易日历、持有期阶梯费率及到账日期仍需输入或核验。详细口径见 [算法设计说明](ALGORITHM_DESIGN.md)。

### 算法质量要求

每种方法应说明目标函数、约束、假设、参数和可核查的理论来源；启发式规则明确标注。验证金额舍入、资金守恒、求解收敛、约束残差和备用方案可行性。包含费用与限额的完整可执行策略，应与简单定投基线进行滚动/样本外比较，并检查估计窗口等参数变化下的稳定性。用于调参或选择策略的数据不能冒充独立验证集。

记录输入日期、数据来源、算法版本、推荐理由和备用原因，支持复现。分别表达计算正确性、历史验证证据和未来表现的不确定性。

### 🛠 技术架构

- **后端**: Python 3.11+, FastAPI, Pandas, NumPy, AkShare (获取市场数据)。
- **前端**: React 19, Bootstrap 5, ECharts。
- **工程化**: UV (Python 包管理), Docker 支持。

### 🚀 快速开始

#### 环境要求

- Python 3.11+
- Node.js & npm
- [uv](https://github.com/astral-sh/uv) (推荐用于后端管理)

#### 安装步骤

1. **克隆项目**:
   ```bash
   git clone https://github.com/your-username/quant-compass.git
   cd quant-compass
   ```

2. **Backend Setup**:
   ```bash
   cd backend
   # 使用 uv (推荐)
   uv sync
   # 或者使用 pip
   pip install -r requirements.txt
   ```

3. **Frontend Setup**:
   ```bash
   cd ../frontend
   npm install
   ```

#### 运行项目

使用根目录下的启动脚本同时开启前后端服务：

```bash
bash start.sh
```

Windows 系统可直接双击根目录下的 `start.cmd`，或在 PowerShell 中运行：

```powershell
.\start.ps1
```

Windows 启动脚本会安装缺失的前后端依赖、构建前端、更新 AkShare，并在
`http://localhost:8666` 启动应用。后续启动如需跳过 AkShare 更新，可运行
`.\start.ps1 -SkipAkshareUpdate`。

### 当前 API 默认参数

下表描述现有实现，其中风险参数沿用旧 API 字段名以兼容已有配置，不是针对个人的参数推荐。

| 参数 | 默认值 | 含义 |
| :-- | :-- | :-- |
| `strategy_mode` | `fixed_weight` | Fixed selected target / 唯一固定目标策略 |
| `rebalance_band` | `0.02` | Execution tolerance, 2 percentage points / 执行容忍偏差 2 个百分点 |
| `available_existing_cash` | `0` | Authorized existing cash / 本月允许追加投入的存量现金 |
| `pending_sale_proceeds` | `0` | Confirmed unsettled net proceeds / 已确认未到账净款 |
| `estimation_window` | `36` | 风险收益估计窗口（月） |
| `minimum_cash_reserve` | `0` | 独立于 RiskFree 的最低闲置 Cash 余额 |
| `enable_cvar_constraint` | `true` | 是否提示 CVaR 偏好超标 |
| `cvar_confidence` | `0.95` | CVaR 置信度 |
| `cvar_limit` | `0.08` | CVaR 偏好阈值 |
| `enable_drawdown_constraint` | `true` | 是否提示回撤偏好超标 |
| `max_drawdown_limit` | `0.20` | 最大回撤偏好阈值 |

### 常见问题

**为什么会出现“买入 0 元”？**

当前实现可能因目标缺口、限额、资金或模型规则产生零买入。按照产品要求，仅有零买入不足以完成建议：必须说明资金保留或转向哪里、金额、原因，以及何时重新评估。购买 RiskFree 和保留 Cash 必须分别列出。

**每月预算一定要全部买入吗？**

要求是每笔资金有明确安排。优先在认可标的内重新分配或分批投入；无法合理投入的部分给出具体暂存安排，不能突破现金底线、忽略费用或限额来强行买入。

**理论目标为什么不同于本月交易结果？**

理论目标描述期望配置；当月交易从真实持仓和现金出发，受到费用、额度和到账条件影响。系统需要同时说明可执行动作、执行后的配置和剩余偏差。

**如何理解收益和回撤？**

净值化回撤用于排除外部资金进出对组合表现的影响；市值回撤包含投入和提取资金的影响，可能掩盖亏损。预期收益和回测收益采用的假设、样本区间不同，都不能证明未来表现。

### 开源协议

本项目采用 [MIT License](LICENSE) 开源协议。

按用户最新确认，不引入日历计算。高级设置分别配置规划周期自然天数（默认30）和预计可交易天数（默认21）；日限额乘可交易天数估算分批投入总额，不按打开页面时距离月底的天数缩减。真实月限额及已用额度仍约束计划，不自动假定跨月额度重置。可交易日数不得超过自然天数；这是一项允许误差的规划假设，不代表已核验的渠道可购日期。
