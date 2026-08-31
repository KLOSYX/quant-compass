import math
import time
from datetime import date
from typing import Any, Dict, List, Optional

import akshare as ak
import numpy as np
import pandas as pd
import requests
from fastapi import HTTPException

FUND_LIST_CACHE = None
MONEY_FUND_NAV_CACHE = {}
MONEY_FUND_NAV_CACHE_TTL_SECONDS = 6 * 60 * 60
MONEY_FUND_HISTORY_URL = "https://api.fund.eastmoney.com/f10/lsjz"
MONEY_FUND_PAGE_SIZE = 20
MONEY_FUND_REQUEST_TIMEOUT_SECONDS = 30
MONEY_FUND_HISTORY_COLUMNS = [
    "净值日期",
    "每万份收益",
    "7日年化收益率",
    "申购状态",
    "赎回状态",
]


class _FundDataSchemaError(ValueError):
    """Raised when a provider response cannot be interpreted safely."""


def _get_fund_list() -> pd.DataFrame:
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
    return FUND_LIST_CACHE


def get_fund_names(fund_codes: List[str]) -> Dict[str, str]:
    fund_list = _get_fund_list()
    names = {}
    for code in dict.fromkeys(fund_codes):
        try:
            names[code] = str(fund_list.loc[code]["基金简称"])
        except KeyError:
            names[code] = f"{code} (名称未找到)"
    return names


def _fetch_with_retry(fetcher, code: str, max_retries: int = 5) -> pd.DataFrame:
    retry_delay = 1
    for attempt in range(max_retries):
        try:
            return fetcher()
        except _FundDataSchemaError:
            raise
        except Exception as e:
            if attempt == max_retries - 1:
                raise
            print(
                f"Attempt {attempt + 1}/{max_retries} failed for {code}: {e}. "
                f"Retrying in {retry_delay}s..."
            )
            time.sleep(retry_delay)
            retry_delay *= 2


def _normalize_money_fund_history(rows: List[Any]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=MONEY_FUND_HISTORY_COLUMNS)

    if all(isinstance(row, dict) for row in rows):
        frame = pd.DataFrame.from_records(rows)
        required_raw_columns = {"FSRQ", "DWJZ", "LJJZ", "SGZT", "SHZT"}
        missing = sorted(required_raw_columns - set(frame.columns))
        if missing:
            raise _FundDataSchemaError(
                "货币基金接口返回缺少字段: " + ", ".join(missing)
            )
        normalized = frame.rename(
            columns={
                "FSRQ": "净值日期",
                "DWJZ": "每万份收益",
                "LJJZ": "7日年化收益率",
                "SGZT": "申购状态",
                "SHZT": "赎回状态",
            }
        )[MONEY_FUND_HISTORY_COLUMNS].copy()
    else:
        if not all(isinstance(row, (list, tuple)) for row in rows):
            raise _FundDataSchemaError("货币基金接口返回记录格式无法识别")
        frame = pd.DataFrame(rows)
        if frame.shape[1] < 9:
            raise _FundDataSchemaError(f"货币基金接口返回列数不足: {frame.shape[1]}")
        # Older AKShare versions consumed positional rows.  The provider has
        # added fields over time, but these five positions remain stable.
        normalized = frame.iloc[:, [0, 1, 2, 7, 8]].copy()
        normalized.columns = MONEY_FUND_HISTORY_COLUMNS

    normalized.sort_values("净值日期", inplace=True, ignore_index=True)
    normalized["净值日期"] = pd.to_datetime(
        normalized["净值日期"], errors="coerce"
    ).dt.date
    normalized["每万份收益"] = pd.to_numeric(normalized["每万份收益"], errors="coerce")
    normalized["7日年化收益率"] = pd.to_numeric(
        normalized["7日年化收益率"], errors="coerce"
    )
    return normalized


def _fetch_money_fund_history(code: str) -> pd.DataFrame:
    """Fetch money-fund history without AKShare's fixed-width row mapping."""

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/80.0.3987.149 Safari/537.36"
        ),
        "Referer": f"https://fundf10.eastmoney.com/jjjz_{code}.html",
        "Host": "api.fund.eastmoney.com",
    }

    def fetch_page(page: int) -> dict:
        response = requests.get(
            MONEY_FUND_HISTORY_URL,
            params={
                "fundCode": code,
                "pageIndex": str(page),
                "pageSize": str(MONEY_FUND_PAGE_SIZE),
                "startDate": "",
                "endDate": "",
                "_": round(time.time() * 1000),
            },
            headers=headers,
            timeout=MONEY_FUND_REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise _FundDataSchemaError("货币基金接口返回不是 JSON 对象")
        return payload

    def extract_rows(payload: dict) -> List[Any]:
        data = payload.get("Data")
        if data is None:
            return []
        if not isinstance(data, dict):
            raise _FundDataSchemaError("货币基金接口 Data 字段格式无法识别")
        rows = data.get("LSJZList")
        if rows is None:
            return []
        if not isinstance(rows, list):
            raise _FundDataSchemaError("货币基金接口 LSJZList 字段格式无法识别")
        return rows

    first_payload = fetch_page(1)
    first_rows = extract_rows(first_payload)
    try:
        total_count = int(first_payload.get("TotalCount") or 0)
    except (TypeError, ValueError) as exc:
        raise _FundDataSchemaError("货币基金接口 TotalCount 字段格式无法识别") from exc

    if not first_rows:
        if total_count > 0:
            raise _FundDataSchemaError("货币基金接口返回总数但第一页为空")
        return _normalize_money_fund_history([])

    rows = list(first_rows)
    total_pages = max(1, math.ceil(total_count / MONEY_FUND_PAGE_SIZE))
    for page in range(2, total_pages + 1):
        rows.extend(extract_rows(fetch_page(page)))

    return _normalize_money_fund_history(rows)


def _build_money_fund_nav(fund_history: pd.DataFrame) -> pd.Series:
    required_columns = {"净值日期", "每万份收益"}
    if fund_history.empty or not required_columns.issubset(fund_history.columns):
        raise ValueError("货币基金历史收益数据为空或缺少每万份收益字段")

    dates = pd.to_datetime(fund_history["净值日期"], errors="coerce")
    income_per_10k = pd.to_numeric(fund_history["每万份收益"], errors="coerce")
    daily_returns = income_per_10k / 10000.0
    valid = dates.notna() & daily_returns.notna()
    if not valid.any():
        raise ValueError("货币基金历史收益数据中没有可用记录")

    nav = (1.0 + daily_returns.loc[valid]).cumprod()
    nav.index = dates.loc[valid]
    nav = nav.sort_index()
    nav.name = "单位净值"
    return nav


def _get_fund_nav(code: str, fund_type: str) -> pd.Series:
    if "货币" in fund_type:
        cached = MONEY_FUND_NAV_CACHE.get(code)
        if cached is not None:
            cached_at, cached_nav = cached
            if time.time() - cached_at < MONEY_FUND_NAV_CACHE_TTL_SECONDS:
                return cached_nav.copy()

        fund_history = _fetch_with_retry(lambda: _fetch_money_fund_history(code), code)
        nav = _build_money_fund_nav(fund_history)
        MONEY_FUND_NAV_CACHE[code] = (time.time(), nav.copy())
        return nav

    fund_history = _fetch_with_retry(
        lambda: ak.fund_open_fund_info_em(symbol=code, indicator="单位净值走势"),
        code,
    )
    required_columns = {"净值日期", "单位净值"}
    if fund_history.empty or not required_columns.issubset(fund_history.columns):
        raise ValueError("开放式基金净值数据为空或字段不完整")

    fund_history["净值日期"] = pd.to_datetime(fund_history["净值日期"], errors="coerce")
    nav = pd.to_numeric(fund_history["单位净值"], errors="coerce")
    nav.index = fund_history["净值日期"]
    return nav.dropna().sort_index()


def get_fund_data(
    fund_codes: List[str],
    start_date: Optional[date],
    end_date: Optional[date],
    risk_free_rate: Optional[float],
) -> (pd.DataFrame, Dict[str, str], List[str]):
    fund_list = _get_fund_list()

    fund_data = {}
    daily_fund_data = {}
    fund_names = {}
    warnings = []
    if fund_codes:
        for code in fund_codes:
            try:
                try:
                    fund_record = fund_list.loc[code]
                    fund_names[code] = fund_record["基金简称"]
                    fund_type = str(fund_record.get("基金类型", ""))
                except KeyError:
                    fund_names[code] = f"{code} (名称未找到)"
                    fund_type = ""

                fund_nav = _get_fund_nav(code, fund_type)
                daily_fund_data[code] = fund_nav
                fund_data[code] = fund_nav.resample("ME").last()

            except Exception as e:
                raise HTTPException(
                    status_code=400, detail=f"获取基金 {code} 的净值数据时发生错误: {e}"
                )

    df = pd.DataFrame(fund_data)
    df = df.sort_index()
    daily_df = pd.DataFrame(daily_fund_data).sort_index()

    if not df.empty:
        latest_start_date = max(
            df[c].first_valid_index()
            for c in df.columns
            if pd.notna(df[c].first_valid_index())
        )
        user_start = pd.to_datetime(start_date) if start_date else latest_start_date
        user_end = pd.to_datetime(end_date) if end_date else daily_df.index.max()
        actual_start = max(latest_start_date, user_start)
        actual_end = min(user_end, daily_df.index.max())
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
        daily_df = pd.DataFrame(
            index=pd.bdate_range(start=actual_start, end=actual_end)
        )

    if risk_free_rate is not None:
        if risk_free_rate <= -1:
            raise HTTPException(
                status_code=400, detail="risk_free_rate must be greater than -1"
            )
        monthly_rf_return = (1 + risk_free_rate) ** (1 / 12) - 1
        rf_index = pd.date_range(start=actual_start, end=actual_end, freq="ME")
        rf_returns = pd.Series(monthly_rf_return, index=rf_index)
        rf_nav = (1 + rf_returns).cumprod()
        df["RiskFree"] = rf_nav
        daily_rf_return = (1 + risk_free_rate) ** (1 / 252) - 1
        daily_rf_index = pd.bdate_range(start=actual_start, end=actual_end)
        daily_df["RiskFree"] = pd.Series(
            (1 + daily_rf_return) ** np.arange(1, len(daily_rf_index) + 1),
            index=daily_rf_index,
        )
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
    daily_processed = daily_df.loc[actual_start:actual_end].ffill().dropna()

    if df_processed.empty:
        raise HTTPException(status_code=400, detail="数据处理后为空，无法进行分析。")

    df_processed.attrs["daily_nav"] = daily_processed
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
