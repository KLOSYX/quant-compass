# Quant Compass 🧭

> **Precision tools for the long-term journey.** A modern Quant Analysis suite specializing in Value Averaging, Dynamic DCA, and multi-cycle backtesting. Built for investors who measure success in years, not days.

[English](#english) | [简体中文](#chinese)

<a name="english"></a>

## English

Quant Compass is a comprehensive quantitative investment analysis and backtesting tool. It helps investors make data-driven decisions using advanced strategies like **Value Averaging (VA)** and **Kelly Criterion-inspired DCA**.

### ✨ Features

- **Multi-Strategy Backtesting**: Compare Lump Sum, fixed DCA, and Value Averaging strategies.
- **Dynamic Asset Allocation**: Real-time investment recommendations based on market valuation signals (Price vs. MA250).
- **Advanced Metrics**: Industry-standard risk assessment including NAV-based Max Drawdown and Annualized Returns.
- **Intuitive UI**: Interactive charts and data visualization using ECharts.
- **Modern Tech Stack**: FastAPI backend and React frontend.

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

### 📈 Investment Strategy & Logic

#### 1. Core Logic Comparison
| Strategy                   | Logic                                                                 | Target Audience                          |
| :------------------------- | :-------------------------------------------------------------------- | :--------------------------------------- |
| **Lump Sum**               | Buy & Hold from Day 1.                                                | One-time investors.                      |
| **Monthly DCA**            | Fixed amount every month regardless of price.                         | Disciplined savers.                      |
| **Optimized Kelly (Default)** | Fractional Kelly with hard risk constraints and cash reserve floor. | Value investors with strict risk control.|
| **Legacy Linear**          | Linear target ratio from Price/MA bias for backward compatibility.    | Users reproducing historical behavior.   |

#### 2. Default Strategy Mode
- **Current default**: `optimized_kelly`
- **Compatibility mode**: `legacy_linear`
- `legacy_linear` does **not** apply CVaR / drawdown hard constraints.

#### 3. Risk Controls (Optimized Kelly)
- **Fractional Kelly**: uses `kelly_fraction` to scale the full Kelly ratio.
- **Cash Reserve Floor**: keeps `minimum_cash_reserve` outside risky allocation.
- **CVaR Hard Constraint**: caps tail-risk by requiring CVaR loss to stay below `cvar_limit` (default confidence: 95%).
- **Max Drawdown Hard Constraint**: caps historical drawdown loss by `max_drawdown_limit`.
- **Constraint Priority**: hard constraints (CVaR / drawdown / cash floor) override the minimum equity ratio when conflicts happen.

#### 4. Key Parameters (Default)
| Parameter | Default | Meaning |
| :-- | :-- | :-- |
| `strategy_mode` | `optimized_kelly` | strategy selector (`optimized_kelly` / `legacy_linear`) |
| `kelly_fraction` | `0.5` | fractional Kelly scaling |
| `estimation_window` | `36` | rolling monthly window for return/risk estimation |
| `minimum_cash_reserve` | `0` | required cash kept out of risky assets |
| `enable_cvar_constraint` | `true` | enable CVaR hard cap |
| `cvar_confidence` | `0.95` | CVaR confidence level |
| `cvar_limit` | `0.08` | CVaR loss upper bound |
| `enable_drawdown_constraint` | `true` | enable drawdown hard cap |
| `max_drawdown_limit` | `0.20` | drawdown loss upper bound |

#### 5. Key Metrics
- **Max Drawdown (NAV)**: The gold standard for risk. It treats your strategy like a mutual fund, calculating the drop in unit value regardless of cash inflows. **Focus on this!**
- **Max Drawdown (Market Value)**: Includes your monthly deposits, which often masks actual losses. Use with caution.

---

<a name="chinese"></a>

## 简体中文

Quant Compass 是一个全面的量化投资分析和回测工具。它通过 **价值平均 (Value Averaging)** 和 **借鉴凯利公式的定投策略**，帮助投资者利用数据进行决策。

### ✨ 核心功能

- **多策略回测**: 对比 "攒钱一次投"、"月月定投" 和 "价值平均 (VA)" 策略。
- **动态资产配置**: 根据市场估值信号 (价格与 MA250 的偏离度) 提供实时投资建议。
- **专业指标管理**: 提供基于净值 (NAV) 的最大回撤、年化收益率等标准风险评估指标。
- **直观 UI 交互**: 使用 ECharts 提供交互式图表 and 数据可视化。
- **现代技术栈**: FastAPI 后端 + React 前端。

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

### 📈 投资策略与逻辑详解

#### 1. 策略回测逻辑对比
| 策略名称 | 核心逻辑 | 适用场景 |
| :-- | :-- | :-- |
| **一次全仓 (Lump Sum)** | 第一天全额买入并长期持有 (Buy & Hold)。 | 验证组合被动持有表现。 |
| **月月投 (DCA)** | 每月固定投入，不做择时。 | 模拟工薪族定投。 |
| **优化 Kelly（默认）** | 分数 Kelly + 现金底线 + CVaR/回撤硬约束。 | 关注风险上限与资金安全垫。 |
| **传统线性（兼容）** | 按 Price/MA 线性映射目标仓位。 | 复现历史行为与旧回测口径。 |

#### 2. 默认模式说明
- **默认模式**：`optimized_kelly`
- **兼容模式**：`legacy_linear`
- `legacy_linear` **不启用** CVaR / 最大回撤硬约束。

#### 3. 风控约束（optimized_kelly）
- **分数 Kelly**：通过 `kelly_fraction` 控制仓位激进程度。
- **现金底线**：`minimum_cash_reserve` 指定不参与风险投资的最低现金。
- **CVaR 硬约束**：要求尾部损失（95% 置信下最差 5% 均值）不超过 `cvar_limit`。
- **最大回撤硬约束**：要求历史回撤损失不超过 `max_drawdown_limit`。
- **优先级**：当与最小权益仓位冲突时，硬约束优先。

#### 4. 关键参数默认值
| 参数 | 默认值 | 含义 |
| :-- | :-- | :-- |
| `strategy_mode` | `optimized_kelly` | 策略模式（`optimized_kelly` / `legacy_linear`） |
| `kelly_fraction` | `0.5` | 分数 Kelly 系数 |
| `estimation_window` | `36` | 风险收益估计窗口（月） |
| `minimum_cash_reserve` | `0` | 现金保留底线 |
| `enable_cvar_constraint` | `true` | 是否启用 CVaR 硬约束 |
| `cvar_confidence` | `0.95` | CVaR 置信度 |
| `cvar_limit` | `0.08` | CVaR 损失上限 |
| `enable_drawdown_constraint` | `true` | 是否启用回撤硬约束 |
| `max_drawdown_limit` | `0.20` | 最大回撤损失上限 |

#### 5. 核心指标说明
- **最大回撤 (净值化/NAV)**: **最重要的风险指标**。采用基金会计法，撇开资金进出影响，真实反映组合自身的抗风险能力。
- **最大回撤 (市值)**: 受新资金投入影响较大，容易产生"从未亏损"的错觉。

---

### 📖 常见问题 (FAQ)

**Q: 为什么会出现"买入 0 元"?**
A: 当市场严重高估且持仓过重时，策略会暂停买入以规避风险，建议将当月预算转入无风险资产（现金/存本）。

**Q: 理论预期回报率与回测结果不符？**
A: "预期回报"是基于历史长期的统计均值，而"回测结果"是特定时间段（如过去3年）的实际表现。市场环境差异和 VA 策略的现金占用（Cash Drag）都会导致两者不同。

### 📄 开源协议
本项目采用 [MIT License](LICENSE) 开源协议。
