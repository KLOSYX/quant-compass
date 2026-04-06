import time
from datetime import date
from typing import Dict, List, Optional

import akshare as ak
import pandas as pd
from fastapi import HTTPException

FUND_LIST_CACHE = None


def get_fund_data(
    fund_codes: List[str],
    start_date: Optional[date],
    end_date: Optional[date],
    risk_free_rate: Optional[float],
) -> (pd.DataFrame, Dict[str, str], List[str]):
    global FUND_LIST_CACHE
    if FUND_LIST_CACHE is None:
        try:
            print("Initializing fund list cache...")
            FUND_LIST_CACHE = ak.fund_name_em()
            FUND_LIST_CACHE.set_index("基金代码", inplace=True)
            print("Fund list cache initialized.")
        except Exception as e:
            raise HTTPException(
                status_code=500, detail=f"Failed to initialize fund list cache: {e}"
            )

    fund_data = {}
    fund_names = {}
    warnings = []
    if fund_codes:
        for code in fund_codes:
            try:
                try:
                    fund_names[code] = FUND_LIST_CACHE.loc[code, "基金简称"]
                except KeyError:
                    fund_names[code] = f"{code} (名称未找到)"

                # Implement simple retry logic with exponential backoff
                # Increased retries and reduced timeout as requested by user
                max_retries = 5
                retry_delay = 1
                fund_nav = None

                for attempt in range(max_retries):
                    try:
                        fund_nav = ak.fund_open_fund_info_em(
                            symbol=code, indicator="单位净值走势"
                        )
                        break
                    except Exception as e:
                        if attempt == max_retries - 1:
                            raise e
                        # Log to console for debugging
                        print(
                            f"Attempt {attempt + 1}/{max_retries} failed for {code}: {e}. Retrying in {retry_delay}s..."
                        )
                        time.sleep(retry_delay)
                        retry_delay *= 2

                if fund_nav is None:
                    raise ValueError(
                        f"Failed to fetch data for {code} after {max_retries} attempts"
                    )

                fund_nav["净值日期"] = pd.to_datetime(fund_nav["净值日期"])
                fund_nav = fund_nav.set_index("净值日期")["单位净值"].astype(float)
                fund_data[code] = fund_nav.resample("ME").last()

            except Exception as e:
                raise HTTPException(
                    status_code=400, detail=f"获取基金 {code} 的净值数据时发生错误: {e}"
                )

    df = pd.DataFrame(fund_data)
    df = df.sort_index()

    if not df.empty:
        latest_start_date = max(
            df[c].first_valid_index()
            for c in df.columns
            if pd.notna(df[c].first_valid_index())
        )
        user_start = pd.to_datetime(start_date) if start_date else latest_start_date
        user_end = pd.to_datetime(end_date) if end_date else df.index.max()
        actual_start = max(latest_start_date, user_start)
        actual_end = min(user_end, df.index.max())
    else:
        if not start_date or not end_date:
            raise HTTPException(
                status_code=400, detail="当没有选择基金时，必须提供开始和结束日期。"
            )
        user_start = pd.to_datetime(start_date)
        user_end = pd.to_datetime(end_date)
        actual_start, actual_end = user_start, user_end
        df = pd.DataFrame(
            index=pd.date_range(start=actual_start, end=actual_end, freq="ME")
        )

    if risk_free_rate is not None:
        monthly_rf_return = (1 + risk_free_rate) ** (1 / 12) - 1
        rf_index = pd.date_range(start=actual_start, end=actual_end, freq="ME")
        rf_returns = pd.Series(monthly_rf_return, index=rf_index)
        rf_nav = (1 + rf_returns).cumprod()
        df["RiskFree"] = rf_nav
        fund_names["RiskFree"] = "无风险资产"

    if actual_start > user_start and fund_codes:
        warnings.append(
            f"注意：部分基金在您选择的开始日期 {user_start.strftime('%Y-%m-%d')} 尚未成立，实际回测已从 {actual_start.strftime('%Y-%m-%d')} 开始。"
        )

    if actual_start >= actual_end:
        raise HTTPException(
            status_code=400, detail="在指定的时间范围内，所选基金没有重叠的交易日。"
        )

    df_filtered = df.loc[actual_start:actual_end]
    df_processed = df_filtered.ffill().dropna()

    if df_processed.empty:
        raise HTTPException(status_code=400, detail="数据处理后为空，无法进行分析。")

    return df_processed, fund_names, warnings


def apply_fund_fee_drag(
    df_nav: pd.DataFrame, fund_fees: Dict[str, float]
) -> pd.DataFrame:
    monthly_returns = df_nav.pct_change().fillna(0)
    for code in monthly_returns.columns:
        if code == "RiskFree":
            continue
        monthly_returns[code] -= fund_fees.get(code, 0.0) / 12
    return (1 + monthly_returns).cumprod()


def prepare_nav_for_analysis(
    df_nav: pd.DataFrame,
    fund_fees: Dict[str, float],
    *,
    apply_fund_fees_to_history: bool,
) -> pd.DataFrame:
    if apply_fund_fees_to_history:
        return apply_fund_fee_drag(df_nav, fund_fees)
    return df_nav.copy()


def ensure_risk_free_column(
    df_nav: pd.DataFrame,
    fund_names: Dict[str, str],
    *,
    weights_dict: Optional[Dict[str, float]] = None,
    holdings_dict: Optional[Dict[str, float]] = None,
) -> tuple[pd.DataFrame, Dict[str, str]]:
    needs_risk_free = False
    for source in (weights_dict or {}, holdings_dict or {}):
        if float(source.get("RiskFree", 0.0)) > 1e-12:
            needs_risk_free = True
            break

    if not needs_risk_free or "RiskFree" in df_nav.columns:
        return df_nav, fund_names

    df_with_risk_free = df_nav.copy()
    df_with_risk_free["RiskFree"] = 1.0
    updated_names = dict(fund_names)
    updated_names.setdefault("RiskFree", "无风险资产")
    return df_with_risk_free, updated_names
