from __future__ import annotations

import argparse
import math
import time
from io import StringIO
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd
import requests
import yfinance as yf
import warnings
import os as _os
import sys as _sys

warnings.filterwarnings("ignore")
yf.set_tz_cache_location("/tmp/yf_tz_cache")


REPO = Path(__file__).resolve().parents[1]
DATA_FILE = REPO / "data" / "hsi5f.csv"
START_DATE = pd.Timestamp("2018-01-01")
HSTECH_OFFICIAL_START = pd.Timestamp("2020-07-27")

URL_VIX = "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv"


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": "Mozilla/5.0"})
    return s


def _get_text(s: requests.Session, url: str, retries: int = 5) -> str:
    last_err = None
    for i in range(retries):
        try:
            r = s.get(url, timeout=40)
            r.raise_for_status()
            return r.text
        except Exception as exc:
            last_err = exc
            time.sleep(1.5 * (i + 1))
    raise RuntimeError(f"Failed to fetch {url}: {last_err}")


def _to_num(df: pd.DataFrame, cols: Iterable[str]) -> None:
    for c in cols:
        df[c] = pd.to_numeric(df[c], errors="coerce")


def _fit_linear(x: pd.Series, y: pd.Series) -> tuple[float, float] | None:
    tmp = pd.DataFrame({"x": x, "y": y}).dropna()
    if len(tmp) < 30:
        return None
    b, a = np.polyfit(tmp["x"].values, tmp["y"].values, 1)
    if not (math.isfinite(a) and math.isfinite(b)):
        return None
    return float(a), float(b)


def _load_existing() -> pd.DataFrame:
    if not DATA_FILE.exists():
        raise FileNotFoundError(f"Data file not found: {DATA_FILE}")
    df = pd.read_csv(DATA_FILE)
    if "Date" not in df.columns:
        raise ValueError("data/hsi5f.csv missing Date column")
    keep = ["Date", "HSI", "HSTECH", "USDCNH", "VHSI", "BTC"]
    df = df[keep].copy()
    df["Date"] = pd.to_datetime(df["Date"])
    _to_num(df, ["HSI", "HSTECH", "USDCNH", "VHSI", "BTC"])
    return df.sort_values("Date").drop_duplicates(subset=["Date"], keep="last")


def _yf_download(ticker: str, start: str, label: str) -> pd.DataFrame:
    for attempt in range(3):
        try:
            with open(_os.devnull, "w") as fnull:
                old_stderr = _sys.stderr
                _sys.stderr = fnull
                try:
                    df = yf.download(ticker, start=start, progress=False, timeout=30)
                finally:
                    _sys.stderr = old_stderr
            if df is not None and not df.empty:
                # Flatten MultiIndex columns returned by newer yfinance
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.droplevel(1)
                df = df.reset_index()
                df = df.rename(columns={"Date": "Date", "Close": label})
                df["Date"] = pd.to_datetime(df["Date"]).dt.tz_localize(None)
                df[label] = pd.to_numeric(df[label], errors="coerce")
                return df[["Date", label]].dropna(subset=["Date"]).sort_values("Date")
        except Exception:
            pass
        if attempt < 2:
            time.sleep(10)
    return pd.DataFrame(columns=["Date", label])


def _load_hsi(s: requests.Session) -> pd.DataFrame:
    return _yf_download("^HSI", "2018-01-01", "HSI")


def _load_btc(s: requests.Session) -> pd.DataFrame:
    return _yf_download("BTC-USD", "2018-01-01", "BTC")


def _load_hstech_proxy(s: requests.Session) -> pd.DataFrame:
    return _yf_download("HSTECH.HK", "2020-07-27", "HSTECH_proxy")


def _load_vix(s: requests.Session) -> pd.DataFrame:
    vix = pd.read_csv(StringIO(_get_text(s, URL_VIX)))
    vix["Date"] = pd.to_datetime(vix["DATE"], format="%m/%d/%Y")
    vix["VIX"] = pd.to_numeric(vix["CLOSE"], errors="coerce")
    return vix[["Date", "VIX"]].dropna(subset=["Date"]).sort_values("Date")


def _load_usdcny() -> pd.DataFrame:
    return _yf_download("CNH=X", "2018-01-01", "USDCNH")



def _last_le(series: pd.Series, d: pd.Timestamp) -> float:
    s = series.loc[:d]
    if s.empty:
        return float("nan")
    return float(s.iloc[-1])


def build_dataset(existing: pd.DataFrame, backfill_days: int = 0) -> pd.DataFrame:
    s = _session()
    hsi = _load_hsi(s)
    btc = _load_btc(s)
    fx = _load_usdcny()
    h_proxy = _load_hstech_proxy(s)
    vix = _load_vix(s)

    hsi = hsi[hsi["Date"] >= START_DATE].copy()
    existing = existing[existing["Date"] >= START_DATE].copy()
    if existing.empty:
        raise RuntimeError("Existing data file is empty; initialize it manually first.")

    max_existing = existing["Date"].max()
    if backfill_days > 0:
        max_existing = max_existing - pd.Timedelta(days=backfill_days)
    target_dates = hsi.loc[hsi["Date"] > max_existing, "Date"].drop_duplicates().sort_values()
    if target_dates.empty:
        return existing

    fit_h = _fit_linear(
        x=existing.merge(h_proxy, on="Date", how="inner")["HSTECH_proxy"],
        y=existing.merge(h_proxy, on="Date", how="inner")["HSTECH"],
    )
    fit_v = _fit_linear(
        x=existing.merge(vix, on="Date", how="inner")["VIX"],
        y=existing.merge(vix, on="Date", how="inner")["VHSI"],
    )

    hsi_s = hsi.set_index("Date")["HSI"].sort_index()
    btc_s = btc.set_index("Date")["BTC"].sort_index()
    fx_s = fx.set_index("Date")["USDCNH"].sort_index()
    hp_s = h_proxy.set_index("Date")["HSTECH_proxy"].sort_index()
    vix_s = vix.set_index("Date")["VIX"].sort_index()

    rows: list[dict[str, float | pd.Timestamp]] = []
    for d in target_dates:
        hsi_val = _last_le(hsi_s, d)
        btc_val = _last_le(btc_s, d)
        fx_val = _last_le(fx_s, d)
        hp_val = _last_le(hp_s, d)
        vix_val = _last_le(vix_s, d)

        hstech_val = float("nan")
        if d >= HSTECH_OFFICIAL_START and fit_h is not None and math.isfinite(hp_val):
            a, b = fit_h
            hstech_val = a + b * hp_val

        vhsi_val = float("nan")
        if fit_v is not None and math.isfinite(vix_val):
            a2, b2 = fit_v
            vhsi_val = a2 + b2 * vix_val

        rows.append(
            {
                "Date": d,
                "HSI": hsi_val if math.isfinite(hsi_val) else float("nan"),
                "HSTECH": hstech_val,
                "USDCNH": fx_val if math.isfinite(fx_val) else float("nan"),
                "VHSI": vhsi_val if math.isfinite(vhsi_val) else float("nan"),
                "BTC": btc_val if math.isfinite(btc_val) else float("nan"),
            }
        )

    update_df = pd.DataFrame(rows)
    keep_df = existing.loc[~existing["Date"].isin(update_df["Date"])].copy()
    out = pd.concat([keep_df, update_df], ignore_index=True)
    out = out[["Date", "HSI", "HSTECH", "USDCNH", "VHSI", "BTC"]]
    out = out.sort_values("Date").drop_duplicates(subset=["Date"], keep="last")
    return out


def save_dataset(df: pd.DataFrame) -> None:
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    out = df.copy()
    out["Date"] = out["Date"].dt.strftime("%Y-%m-%d")
    out.to_csv(DATA_FILE, index=False, float_format="%.6f")


def _normalize_for_compare(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out = out.sort_values("Date").drop_duplicates(subset=["Date"], keep="last").reset_index(drop=True)
    for c in ["HSI", "HSTECH", "USDCNH", "VHSI", "BTC"]:
        out[c] = pd.to_numeric(out[c], errors="coerce").round(6)
    out["Date"] = pd.to_datetime(out["Date"]).dt.strftime("%Y-%m-%d")
    return out[["Date", "HSI", "HSTECH", "USDCNH", "VHSI", "BTC"]]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Update HSI 5-factor dataset")
    parser.add_argument("--backfill-days", type=int, default=0, help="Recompute last N days")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    before = _load_existing()
    after = build_dataset(before, backfill_days=args.backfill_days)

    if _normalize_for_compare(before).equals(_normalize_for_compare(after)):
        print(
            f"rows: {len(before)} -> {len(after)} | "
            f"range: {after['Date'].min().date()} ~ {after['Date'].max().date()} | unchanged"
        )
        return 0

    save_dataset(after)

    print(
        f"rows: {len(before)} -> {len(after)} | "
        f"range: {after['Date'].min().date()} ~ {after['Date'].max().date()}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
