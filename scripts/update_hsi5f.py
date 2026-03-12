from __future__ import annotations

import datetime as dt
import json
import math
import time
from io import StringIO
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd
import requests


REPO = Path(__file__).resolve().parents[1]
DATA_FILE = REPO / "data" / "hsi5f.csv"
START_DATE = pd.Timestamp("2018-01-01")
HSTECH_OFFICIAL_START = pd.Timestamp("2020-07-27")


URL_HSI = "https://stooq.com/q/d/l/?s=^hsi&i=d"
URL_BTC = "https://stooq.com/q/d/l/?s=btcusd&i=d"
URL_HSTECH_PROXY = "https://stooq.com/q/d/l/?s=3032.hk&i=d"
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
        except Exception as exc:  # noqa: BLE001
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


def _load_hsi(s: requests.Session) -> pd.DataFrame:
    hsi = pd.read_csv(StringIO(_get_text(s, URL_HSI)))
    hsi["Date"] = pd.to_datetime(hsi["Date"])
    hsi["HSI"] = pd.to_numeric(hsi["Close"], errors="coerce")
    return hsi[["Date", "HSI"]].dropna(subset=["Date"]).sort_values("Date")


def _load_btc(s: requests.Session) -> pd.DataFrame:
    btc = pd.read_csv(StringIO(_get_text(s, URL_BTC)))
    btc["Date"] = pd.to_datetime(btc["Date"])
    btc["BTC"] = pd.to_numeric(btc["Close"], errors="coerce")
    return btc[["Date", "BTC"]].dropna(subset=["Date"]).sort_values("Date")


def _load_hstech_proxy(s: requests.Session) -> pd.DataFrame:
    pxy = pd.read_csv(StringIO(_get_text(s, URL_HSTECH_PROXY)))
    pxy["Date"] = pd.to_datetime(pxy["Date"])
    pxy["HSTECH_proxy"] = pd.to_numeric(pxy["Close"], errors="coerce")
    return pxy[["Date", "HSTECH_proxy"]].dropna(subset=["Date"]).sort_values("Date")


def _load_vix(s: requests.Session) -> pd.DataFrame:
    vix = pd.read_csv(StringIO(_get_text(s, URL_VIX)))
    vix["Date"] = pd.to_datetime(vix["DATE"], format="%m/%d/%Y")
    vix["VIX"] = pd.to_numeric(vix["CLOSE"], errors="coerce")
    return vix[["Date", "VIX"]].dropna(subset=["Date"]).sort_values("Date")


def _load_usdcny(s: requests.Session) -> pd.DataFrame:
    end = dt.date.today().isoformat()
    url = f"https://api.frankfurter.app/{START_DATE.date()}..{end}?from=USD&to=CNY"
    obj = json.loads(_get_text(s, url))
    rows = [{"Date": k, "USDCNH": v.get("CNY")} for k, v in obj.get("rates", {}).items()]
    fx = pd.DataFrame(rows)
    if fx.empty:
        return pd.DataFrame(columns=["Date", "USDCNH"])
    fx["Date"] = pd.to_datetime(fx["Date"])
    fx["USDCNH"] = pd.to_numeric(fx["USDCNH"], errors="coerce")
    return fx[["Date", "USDCNH"]].sort_values("Date")


def _last_le(series: pd.Series, d: pd.Timestamp) -> float:
    s = series.loc[:d]
    if s.empty:
        return float("nan")
    return float(s.iloc[-1])


def build_dataset(existing: pd.DataFrame) -> pd.DataFrame:
    s = _session()
    hsi = _load_hsi(s)
    btc = _load_btc(s)
    fx = _load_usdcny(s)
    h_proxy = _load_hstech_proxy(s)
    vix = _load_vix(s)

    hsi = hsi[hsi["Date"] >= START_DATE].copy()
    existing = existing[existing["Date"] >= START_DATE].copy()
    if existing.empty:
        raise RuntimeError("Existing data file is empty; initialize it manually first.")

    max_existing = existing["Date"].max()
    target_dates = hsi.loc[hsi["Date"] > max_existing, "Date"].drop_duplicates().sort_values()
    if target_dates.empty:
        return existing

    # Build mappings on historical overlap using existing stable data.
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

    last_hsi = float(existing["HSI"].dropna().iloc[-1])
    last_btc = float(existing["BTC"].dropna().iloc[-1])
    last_fx = float(existing["USDCNH"].dropna().iloc[-1])
    last_vhsi = float(existing["VHSI"].dropna().iloc[-1])

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
                "HSI": hsi_val if math.isfinite(hsi_val) else last_hsi,
                "HSTECH": hstech_val,
                "USDCNH": fx_val if math.isfinite(fx_val) else last_fx,
                "VHSI": vhsi_val if math.isfinite(vhsi_val) else last_vhsi,
                "BTC": btc_val if math.isfinite(btc_val) else last_btc,
            }
        )

        if math.isfinite(hsi_val):
            last_hsi = float(hsi_val)
        if math.isfinite(btc_val):
            last_btc = float(btc_val)
        if math.isfinite(fx_val):
            last_fx = float(fx_val)
        if math.isfinite(vhsi_val):
            last_vhsi = float(vhsi_val)

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


def main() -> int:
    before = _load_existing()
    after = build_dataset(before)

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
