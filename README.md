# Quant Compass 🧭

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
| Strategy        | Logic                                             | Target Audience                          |
| :-------------- | :------------------------------------------------ | :--------------------------------------- |
| **Lump Sum**    | Buy & Hold from Day 1.                            | One-time investors.                      |
| **Monthly DCA** | Fixed amount every month regardless of price.     | Disciplined savers.                      |
| **VA / Kelly**  | Dynamic buying/selling based on market valuation. | Value investors seeking lower drawdowns. |

#### 2. Market Signals (Kelly/VA)
Market valuation is judged by the deviation from the **250-day Moving Average (MA250)**.

- **🟢 Undervalued**: Price < MA250 * 0.9 (Aggressive buying).
- **🟡 Neutral**: Price within ±10% of MA250.
- **🔴 Overvalued**: Price > MA250 * 1.1 (Reduce or sell).

#### 3. Key Metrics
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
| 策略名称                | 核心逻辑                                       | 适用场景                   |
| :---------------------- | :--------------------------------------------- | :------------------------- |
| **一次全仓 (Lump Sum)** | 第一天全额买入并长期持有 (Buy & Hold)。        | 验证组合被动持有的表现。   |
| **月月投 (DCA)**        | 每月固定日期投入固定金额，不做择时。           | 模拟工薪族强制储蓄。       |
| **VA / Kelly 定投**     | 根据市场估值（价格 vs 均线）动态调整买卖金额。 | 追求"低买高卖"，平滑波动。 |

#### 2. 市场信号判断
使用 **价格与 250日均线 (MA250)** 的偏离程度判断水位。

- **🟢 低估**: 价格 < MA250 * 0.9。建议加大投入。
- **🟡 中性**: 价格在均线上下 10% 波动。正常定投。
- **🔴 高估**: 价格 > MA250 * 1.1。建议减少买入甚至锁定利润。

#### 3. 核心指标说明
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
