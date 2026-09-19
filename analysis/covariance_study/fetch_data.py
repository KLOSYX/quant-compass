"""Download frozen study sources; keep raw source bytes and quality decisions."""

import gzip
import hashlib
import io
import json
import re
import sys
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
OUT = Path(__file__).parent / "data"
SOURCES = {
    "us_industry49": "49_Industry_Portfolios",
    "us_size_value25": "25_Portfolios_5x5",
    "europe6": "Europe_6_Portfolios_ME_BE-ME",
    "japan6": "Japan_6_Portfolios_ME_BE-ME",
    "asia_ex_japan6": "Asia_Pacific_ex_Japan_6_Portfolios_ME_BE-ME",
    "north_america6": "North_America_6_Portfolios_ME_BE-ME",
    "emerging6": "Emerging_Markets_6_Portfolios_ME_BE-ME",
}
CODES = [
    "000051",
    "110003",
    "110020",
    "161725",
    "160119",
    "000968",
    "000198",
    "110017",
    "110018",
    "000171",
    "000216",
    "000217",
    "000311",
    "000478",
    "000071",
    "000614",
]


def french(item):
    name, stem = item
    url = f"https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/{stem}_CSV.zip"
    path = OUT / f"{name}.zip"
    if not path.exists():
        r = requests.get(url, timeout=60)
        r.raise_for_status()
        path.write_bytes(r.content)
    raw = path.read_bytes()
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        content = archive.read(archive.namelist()[0]).decode("utf-8-sig")
    lines = content.splitlines()
    start = next(i for i, line in enumerate(lines) if re.match(r"^\s*\d{6}\s*,", line))
    end = start
    while end < len(lines) and re.match(r"^\s*\d{6}\s*,", lines[end]):
        end += 1
    data = pd.read_csv(
        io.StringIO("\n".join([lines[start - 1], *lines[start:end]])), index_col=0
    )
    data.columns = data.columns.str.strip()
    data.index = pd.to_datetime(
        data.index.astype(str).str.strip(), format="%Y%m"
    ) + pd.offsets.MonthEnd(0)
    data = data.astype(float).replace([-99.99, -999.0], np.nan) / 100
    data = data.loc["1990-01-01":"2025-12-31"]
    assert not data.index.duplicated().any() and (data.dropna() > -1).all().all()
    data.to_csv(OUT / f"{name}.csv", index_label="date")
    return name, {
        "url": url,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "rows": len(data),
        "columns": list(data),
        "missing": int(data.isna().sum().sum()),
        "first": str(data.index.min().date()),
        "last": str(data.index.max().date()),
        "section_header": lines[start - 2 : start],
        "currency": "USD",
        "return_basis": "first monthly value-weighted total-return section",
    }


def main():
    OUT.mkdir(exist_ok=True)
    meta = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "french": {},
        "china": {},
    }
    with ThreadPoolExecutor(max_workers=4) as pool:
        for name, record in pool.map(french, SOURCES.items()):
            meta["french"][name] = record
            print(name, record["rows"], record["missing"], flush=True)
    (OUT / "provenance.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False))
    from core.data import _get_fund_list, _get_fund_nav
    from core.market_data import reconstruct_total_return_index

    fundlist = _get_fund_list()

    def fund(code):
        record = {"code": code}
        try:
            item = fundlist.loc[code]
            kind = str(item["基金类型"])
            record.update(name=str(item["基金简称"]), type=kind)
            if "货币" in kind:
                return code, {
                    **record,
                    "excluded": "money-market asset is outside variable-risk covariance comparison",
                }
            nav = _get_fund_nav(code, kind)
            record["quality"] = asdict(nav.attrs["return_quality"])
            raw = {
                "prices": {str(d): float(v) for d, v in nav.items()},
                "corporate_actions": [
                    asdict(a) for a in nav.attrs["corporate_actions"]
                ],
                "provider_responses": nav.attrs["corporate_action_source_responses"],
            }
            serialized = json.dumps(raw, ensure_ascii=False, default=str).encode()
            (OUT / f"cn_{code}_raw.json.gz").write_bytes(
                gzip.compress(serialized, mtime=0)
            )
            record.update(
                sha256=hashlib.sha256(serialized).hexdigest(),
                source="AkShare / Eastmoney; unit NAV plus distribution/split history and cumulative NAV reconciliation",
            )
            total = reconstruct_total_return_index(nav, nav.attrs["corporate_actions"])
            # Completed calendar months only; no forward fill of missing months.
            monthly = total.loc[:"2025-12-31"].resample("ME").last()
            monthly.pct_change(fill_method=None).dropna().rename(code).to_csv(
                OUT / f"cn_{code}.csv", index_label="date"
            )
            record.update(
                rows=len(monthly) - 1,
                first=str(monthly.index.min().date()),
                last=str(monthly.index.max().date()),
            )
            if record["quality"]["status"] not in ["verified", "reconciled"]:
                record["excluded"] = "total-return quality not verified"
            elif len(monthly) - 1 < 96:
                record["excluded"] = "fewer than 96 completed monthly returns"
        except Exception as exc:  # noqa: BLE001 - record source failures as explicit exclusions
            record["excluded"] = repr(exc)
        print(
            code,
            record.get("name"),
            record.get("rows"),
            record.get("excluded", "included"),
            flush=True,
        )
        return code, record

    with ThreadPoolExecutor(max_workers=3) as pool:
        for code, record in pool.map(fund, CODES):
            meta["china"][code] = record
            (OUT / "provenance.json").write_text(
                json.dumps(meta, indent=2, ensure_ascii=False, default=str)
            )


if __name__ == "__main__":
    main()
