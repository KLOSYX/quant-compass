"""Generate descriptive tables; no parameter selection or production changes."""

from pathlib import Path

import pandas as pd

HERE = Path(__file__).parent
OUT = HERE / "results"


def markdown(frame):
    columns = list(frame.columns)
    return "\n".join(
        [
            "| " + " | ".join(columns) + " |",
            "| " + " | ".join(["---"] * len(columns)) + " |",
        ]
        + [
            "| " + " | ".join(str(x) for x in row) + " |"
            for row in frame.itertuples(index=False, name=None)
        ]
    )


def main():
    h = pd.read_csv(OUT / "historical.csv")
    s = pd.read_csv(OUT / "synthetic.csv")
    a = pd.read_csv(OUT / "accounts.csv")
    primary = h.query(
        "window == 36 and objective == 'gmv' and fee == 0.0015 and period == 'full' and lag == 0 and method in ['fixed_20', 'ledoit_wolf'] and not universe.str.startswith('us_sub')"
    )
    rows = []
    for name, f in primary.groupby("universe", sort=False):
        pair = f.set_index("method")
        x, y = pair.loc["fixed_20"], pair.loc["ledoit_wolf"]
        rows.append(
            [
                name,
                int(x.months),
                f"{x.sharpe:.3f} → {y.sharpe:.3f}",
                f"{x.volatility:.2%} → {y.volatility:.2%}",
                f"{x.max_drawdown:.2%} → {y.max_drawdown:.2%}",
            ]
        )
    text = "# 完整结果摘要表\n\n所有箭头均为固定20% → Ledoit–Wolf；Sharpe 使用零基准，回撤来自月度采样。\n\n## 主比较：36个月训练、最小方差、成交费率15bp\n\n"
    text += markdown(
        pd.DataFrame(
            rows, columns=["样本", "样本外月数", "Sharpe", "年化波动", "最大回撤"]
        )
    )
    for obj in ["gmv", "mid_return_floor"]:
        f = h.query(
            "fee == 0.0015 and lag == 0 and period == 'full' and method in ['fixed_20', 'ledoit_wolf'] and not universe.str.startswith('us_sub') and objective == @obj"
        )
        p = f.pivot(index=["universe", "window"], columns="method", values="sharpe")
        d = (
            (p.ledoit_wolf - p.fixed_20)
            .unstack()
            .round(3)
            .reset_index()
            .fillna("不足24个月，未估计")
        )
        d.columns = [str(c) for c in d.columns]
        text += f"\n\n## 窗口敏感性：{obj}，Sharpe差值（LW−固定）\n\n" + markdown(d)
    text += "\n\n## 固定目标、正常新增：账户队列\n\n每5年一个入场队列，至少60个月。末值差只在同样本、同队列内配对；表内范围为不同入场队列，不是置信区间。\n\n"
    f = a.query("refresh == 'fixed' and scenario == 'normal'")
    p = f.pivot(index=["universe", "entry"], columns="method", values="terminal_wealth")
    d = (p.ledoit_wolf / p.fixed_20 - 1).rename("difference").reset_index()
    rows = [
        [n, len(g), f"{g.difference.min():+.2%} ～ {g.difference.max():+.2%}"]
        for n, g in d.groupby("universe", sort=False)
    ]
    text += markdown(
        pd.DataFrame(rows, columns=["样本", "入场队列数", "LW末值相对固定差异"])
    )
    text += "\n\n## 各执行条件：末值相对差异\n\n以下是所有家族与入场队列的描述性中位数及范围；样本相关、市场和币种不同，不能解释为投资者预期收益或独立获胜概率。\n\n"
    p = a.pivot(
        index=["universe", "entry", "refresh", "scenario"],
        columns="method",
        values="terminal_wealth",
    )
    d = (p.ledoit_wolf / p.fixed_20 - 1).rename("difference").reset_index()
    rows = [
        [
            r,
            s,
            len(g),
            f"{g.difference.median():+.2%}",
            f"{g.difference.min():+.2%} ～ {g.difference.max():+.2%}",
        ]
        for (r, s), g in d.groupby(["refresh", "scenario"])
    ]
    text += markdown(
        pd.DataFrame(rows, columns=["目标更新", "情景", "配对数", "中位差异", "范围"])
    )
    text += "\n\n## 已知协方差机制实验\n\n每类4个训练窗口、每窗口100次；表为等权平均真方差之比，不混入历史统计。\n\n"
    p = s.groupby(["kind", "method"]).true_variance.mean().unstack()
    rows = [[n, f"{row.ledoit_wolf / row.fixed_20:.3f}"] for n, row in p.iterrows()]
    text += markdown(
        pd.DataFrame(rows, columns=["机制", "LW / 固定的真方差（小于1较好）"])
    )
    text += f"\n\n明细：historical.csv {len(h)}行（含重复窗口/费用/分期）；accounts.csv {len(a)}个账户；synthetic.csv {len(s)}行（2400组配对）。这些数量不是独立样本数。\n"
    (HERE / "TABLES.md").write_text(text)


if __name__ == "__main__":
    main()
