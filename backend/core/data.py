import math
import time
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

import akshare as ak
import numpy as np
import pandas as pd
import requests
from fastapi import HTTPException

from core.domain import CorporateAction, ReturnQuality
from core.market_data import build_market_snapshot, reconstruct_total_return_index

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
        nav.attrs["corporate_actions"] = ()
        nav.attrs["return_quality"] = ReturnQuality(
            status="verified",
            corporate_action_coverage_complete=True,
            reconciliation_tolerance=0.0,
            availability_quality="observed",
        )
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
    nav = nav.dropna().sort_index()

    actions: list[CorporateAction] = []
    raw_action_responses: dict[str, list[dict[str, Any]]] = {}
    coverage_complete = True
    observed_at = datetime.now(timezone.utc)
    for indicator, kind in (
        ("分红送配详情", "cash_distribution"),
        ("拆分详情", "split"),
    ):
        try:
            frame = _fetch_with_retry(
                lambda indicator=indicator: ak.fund_open_fund_info_em(
                    symbol=code, indicator=indicator
                ),
                code,
                max_retries=2,
            )
            raw_action_responses[indicator] = frame.to_dict(orient="records")
            for _, row in frame.iterrows():
                date_values = [
                    row.get(name)
                    for name in (
                        "除息日",
                        "权益登记日",
                        "红利发放日",
                        "拆分折算日",
                        "年份",
                    )
                    if row.get(name) is not None
                ]
                event_date = next(
                    (pd.to_datetime(value, errors="coerce") for value in date_values),
                    pd.NaT,
                )
                if pd.isna(event_date):
                    continue
                if kind == "cash_distribution":
                    amount = pd.to_numeric(
                        row.get("每份分红", row.get("分红", row.get("分红方案"))),
                        errors="coerce",
                    )
                    if pd.isna(amount):
                        continue
                    actions.append(
                        CorporateAction(
                            asset_code=code,
                            kind="cash_distribution",
                            record_date=pd.to_datetime(
                                row.get("权益登记日", event_date), errors="coerce"
                            ),
                            ex_date=pd.to_datetime(
                                row.get("除息日", event_date), errors="coerce"
                            ),
                            payment_date=pd.to_datetime(
                                row.get("红利发放日", event_date), errors="coerce"
                            ),
                            cash_per_share=float(amount),
                            source="akshare:eastmoney",
                            observed_at=observed_at,
                        )
                    )
                else:
                    ratio = pd.to_numeric(
                        row.get("拆分比例", row.get("折算比例")), errors="coerce"
                    )
                    if pd.isna(ratio) or float(ratio) <= 0:
                        continue
                    actions.append(
                        CorporateAction(
                            asset_code=code,
                            kind="split",
                            ex_date=event_date,
                            split_ratio=float(ratio),
                            source="akshare:eastmoney",
                            observed_at=observed_at,
                        )
                    )
        except Exception:
            coverage_complete = False

    reconciliation_error = None
    reconciliation_tolerance = None
    try:
        cumulative_frame = _fetch_with_retry(
            lambda: ak.fund_open_fund_info_em(symbol=code, indicator="累计净值走势"),
            code,
            max_retries=2,
        )
        raw_action_responses["累计净值走势"] = cumulative_frame.to_dict(
            orient="records"
        )
        if {"净值日期", "累计净值"}.issubset(cumulative_frame.columns):
            cumulative = pd.Series(
                pd.to_numeric(cumulative_frame["累计净值"], errors="coerce").values,
                index=pd.to_datetime(cumulative_frame["净值日期"], errors="coerce"),
            ).dropna()
            reconstructed = reconstruct_total_return_index(nav, actions)
            aligned = pd.concat(
                [reconstructed.rename("rebuilt"), cumulative.rename("vendor")],
                axis=1,
                join="inner",
            ).dropna()
            if len(aligned) >= 2 and (aligned["vendor"] > 0).all():
                aligned = aligned / aligned.iloc[0]
                relative_error = (
                    aligned["rebuilt"] - aligned["vendor"]
                ).abs() / aligned["vendor"]
                reconciliation_error = float(relative_error.max())
                numeric_text = cumulative_frame["累计净值"].dropna().astype(str)
                observed_decimal_places = [
                    len(value.partition(".")[2].rstrip("0"))
                    for value in numeric_text
                    if len(value.partition(".")[2].rstrip("0")) > 0
                ]
                # Use the least precise non-integer observation as the
                # provider field-resolution floor. Extra float digits must not
                # fabricate a tighter reconciliation tolerance.
                decimal_places = min(observed_decimal_places, default=0)
                field_precision = 10.0 ** (-decimal_places)
                reconciliation_tolerance = float(
                    field_precision / max(float(cumulative.abs().median()), 1e-12)
                )
        else:
            coverage_complete = False
    except Exception:
        coverage_complete = False

    reconciled = bool(
        coverage_complete
        and reconciliation_error is not None
        and reconciliation_tolerance is not None
        and reconciliation_error <= reconciliation_tolerance
    )
    nav.attrs["corporate_actions"] = tuple(actions)
    nav.attrs["corporate_action_source_responses"] = raw_action_responses
    nav.attrs["return_quality"] = ReturnQuality(
        status="reconciled" if reconciled else "partial",
        corporate_action_coverage_complete=coverage_complete,
        reconciliation_error=reconciliation_error,
        reconciliation_tolerance=reconciliation_tolerance,
        availability_quality="observed",
    )
    return nav


def get_fund_data(
    fund_codes: List[str],
    start_date: Optional[date],
    end_date: Optional[date],
    risk_free_rate: Optional[float],
) -> (pd.DataFrame, Dict[str, str], List[str]):
    fund_list = _get_fund_list()

    fund_data = {}
    daily_fund_data = {}
    daily_total_return_data = {}
    corporate_actions: list[CorporateAction] = []
    return_quality: dict[str, ReturnQuality] = {}
    raw_action_responses: dict[str, dict[str, list[dict[str, Any]]]] = {}
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
                quality = fund_nav.attrs.get("return_quality")
                if not isinstance(quality, ReturnQuality):
                    quality = ReturnQuality(
                        status="unsupported",
                        corporate_action_coverage_complete=False,
                        availability_quality="inferred",
                    )
                actions = tuple(fund_nav.attrs.get("corporate_actions", ()))
                return_quality[code] = quality
                raw_action_responses[code] = dict(
                    fund_nav.attrs.get("corporate_action_source_responses", {})
                )
                corporate_actions.extend(actions)
                daily_total_return_data[code] = reconstruct_total_return_index(
                    fund_nav, actions
                )

            except Exception as e:
                raise HTTPException(
                    status_code=400, detail=f"获取基金 {code} 的净值数据时发生错误: {e}"
                )

    df = pd.DataFrame(fund_data)
    df = df.sort_index()
    daily_df = pd.DataFrame(daily_fund_data).sort_index()
    daily_total_return_df = pd.DataFrame(daily_total_return_data).sort_index()

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
        daily_total_return_df = pd.DataFrame(index=daily_df.index)

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
        daily_total_return_df["RiskFree"] = daily_df["RiskFree"]
        return_quality["RiskFree"] = ReturnQuality(
            status="verified",
            corporate_action_coverage_complete=True,
            reconciliation_tolerance=0.0,
            availability_quality="observed",
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
    daily_total_return_processed = (
        daily_total_return_df.loc[actual_start:actual_end].ffill().dropna()
    )

    if df_processed.empty:
        raise HTTPException(status_code=400, detail="数据处理后为空，无法进行分析。")

    df_processed.attrs["daily_nav"] = daily_processed
    df_processed.attrs["daily_total_return"] = daily_total_return_processed
    df_processed.attrs["monthly_total_return"] = daily_total_return_processed.resample(
        "ME"
    ).last()
    df_processed.attrs["corporate_actions"] = tuple(corporate_actions)
    df_processed.attrs["return_quality"] = return_quality
    df_processed.attrs["corporate_action_source_responses"] = raw_action_responses
    df_processed.attrs["market_snapshot"] = build_market_snapshot(
        price_series=daily_processed,
        corporate_actions=corporate_actions,
        return_quality=return_quality,
        source="akshare:eastmoney",
        fetched_at=datetime.now(timezone.utc),
        available_at={
            code: datetime.now(timezone.utc) for code in daily_processed.columns
        },
    )
    return df_processed, fund_names, warnings


def apply_fund_fee_drag(
    df_nav: pd.DataFrame, fund_fees: Dict[str, float]
) -> pd.DataFrame:
    monthly_returns = df_nav.pct_change().dropna(how="all")
    for code in monthly_returns.columns:
        if code == "RiskFree":
            continue
        monthly_returns[code] -= fund_fees.get(code, 0.0) / 12
    dragged = (1 + monthly_returns).cumprod()
    if not df_nav.empty:
        anchor = pd.DataFrame(
            [np.ones(len(df_nav.columns))],
            index=[df_nav.index[0]],
            columns=df_nav.columns,
        )
        dragged = pd.concat([anchor, dragged])
    return dragged


def prepare_nav_for_analysis(
    df_nav: pd.DataFrame,
    fund_fees: Dict[str, float],
    *,
    apply_fund_fees_to_history: bool,
) -> pd.DataFrame:
    if apply_fund_fees_to_history:
        return apply_fund_fee_drag(df_nav, fund_fees)
    return df_nav.copy()


def estimation_total_return_nav(
    df_nav: pd.DataFrame, *, allow_degraded_research: bool = False
) -> tuple[pd.DataFrame, list[str]]:
    """Return the economic-return series and enforce its quality gate.

    Synthetic frames used by core callers may have no provider metadata. Real
    provider frames always carry ``return_quality`` and must pass this gate.
    """

    quality = df_nav.attrs.get("return_quality")
    monthly_total_return = df_nav.attrs.get("monthly_total_return")
    if quality is None or monthly_total_return is None:
        return df_nav.copy(), []
    degraded = sorted(
        code
        for code, item in quality.items()
        if item.status not in {"verified", "reconciled"}
    )
    if degraded and not allow_degraded_research:
        raise ValueError(
            "total-return data quality blocks frontier and Kelly: "
            + ", ".join(degraded)
        )
    warnings = []
    if degraded:
        warnings.append(
            "降级研究结果：以下基金的公司行为数据不完整：" + ", ".join(degraded)
        )
    result = monthly_total_return.reindex(columns=df_nav.columns).dropna()
    if result.empty:
        raise ValueError("total-return series is empty after alignment")
    return result, warnings


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
