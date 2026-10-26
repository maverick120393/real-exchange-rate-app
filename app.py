"""FX Lens | Real Exchange Rate Explorer.

Run with: streamlit run app.py
Data are retrieved from the public IMF SDMX 3.0 CPI and ER dataflows.
"""
from __future__ import annotations

import csv
import io
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st
from plotly.subplots import make_subplots
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

APP_TITLE = "FX Lens | Real Exchange Rate Explorer"
BASE_URL = "https://api.imf.org/external/sdmx/3.0"
AGENCY = "IMF.STA"
START_PERIOD = "1976-M01"
CACHE_TTL_SECONDS = 60 * 60 * 6

# Used only if IMF metadata cannot be reached. The live catalogue is preferred.
FALLBACK_COUNTRIES: dict[str, str] = {
    "ARG": "Argentina", "AUS": "Australia", "AUT": "Austria", "BEL": "Belgium",
    "BGD": "Bangladesh", "BRA": "Brazil", "CAN": "Canada", "CHL": "Chile",
    "CHN": "China", "COL": "Colombia", "CZE": "Czechia", "DNK": "Denmark",
    "EGY": "Egypt", "FIN": "Finland", "FRA": "France", "DEU": "Germany",
    "GRC": "Greece", "HKG": "Hong Kong SAR", "HUN": "Hungary", "IND": "India",
    "IDN": "Indonesia", "IRL": "Ireland", "ISR": "Israel", "ITA": "Italy",
    "JPN": "Japan", "KEN": "Kenya", "KOR": "Korea, Republic of", "MYS": "Malaysia",
    "MEX": "Mexico", "MAR": "Morocco", "NLD": "Netherlands", "NZL": "New Zealand",
    "NGA": "Nigeria", "NOR": "Norway", "PAK": "Pakistan", "PER": "Peru",
    "PHL": "Philippines", "POL": "Poland", "PRT": "Portugal", "ROU": "Romania",
    "RUS": "Russian Federation", "SAU": "Saudi Arabia", "SGP": "Singapore",
    "ZAF": "South Africa", "ESP": "Spain", "SWE": "Sweden", "CHE": "Switzerland",
    "THA": "Thailand", "TUR": "Türkiye", "UKR": "Ukraine", "ARE": "United Arab Emirates",
    "GBR": "United Kingdom", "USA": "United States", "VNM": "Vietnam", "G163": "Euro Area",
}
CURRENCY_HINTS = {
    "USA": "USD", "IND": "INR", "G163": "EUR", "GBR": "GBP", "JPN": "JPY",
    "CHN": "CNY", "KOR": "KRW", "CAN": "CAD", "AUS": "AUD", "CHE": "CHF",
    "MEX": "MXN", "BRA": "BRL", "ZAF": "ZAR", "TUR": "TRY", "RUS": "RUB",
    "IDN": "IDR", "THA": "THB", "SGP": "SGD", "NGA": "NGN", "EGY": "EGP",
}


class IMFDataError(RuntimeError):
    """A recoverable IMF retrieval or parsing error."""


@dataclass(frozen=True)
class Entity:
    code: str
    name: str

    @property
    def label(self) -> str:
        return f"{flag_for(self.code)} {self.name}"


def flag_for(code: str) -> str:
    """Return a flag without external image URLs; G163 is the Euro-area entity."""
    if code == "G163":
        return "🇪🇺"
    # IMF mostly uses ISO-3 codes; a small explicit mapping keeps this robust.
    iso2 = {"USA": "US", "IND": "IN", "GBR": "GB", "DEU": "DE", "FRA": "FR", "JPN": "JP",
            "CHN": "CN", "CAN": "CA", "AUS": "AU", "BRA": "BR", "MEX": "MX", "KOR": "KR",
            "RUS": "RU", "ZAF": "ZA", "TUR": "TR", "CHE": "CH", "ITA": "IT", "ESP": "ES"}.get(code)
    return "".join(chr(127397 + ord(c)) for c in iso2) if iso2 else "🌐"


def build_session() -> requests.Session:
    retry = Retry(total=3, backoff_factor=0.7, status_forcelist=(429, 500, 502, 503, 504),
                  allowed_methods=("GET",), respect_retry_after_header=True)
    session = requests.Session()
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.headers.update({"User-Agent": "FX-Lens-RER-Explorer/1.0", "Accept": "text/csv"})
    return session


def get_response(url: str, accept: str = "text/csv") -> requests.Response:
    try:
        response = build_session().get(url, headers={"Accept": accept}, timeout=(8, 45))
    except requests.RequestException as exc:
        raise IMFDataError("Could not contact the IMF API. Check your connection and try again.") from exc
    if response.status_code == 429:
        raise IMFDataError("The IMF API is rate-limiting requests. Please wait a moment and retry.")
    if response.status_code == 404:
        raise IMFDataError("The requested IMF series was not found. It may be unavailable for this entity.")
    if response.status_code >= 400:
        raise IMFDataError(f"The IMF API returned HTTP {response.status_code}. Please try again later.")
    if not response.content:
        raise IMFDataError("The IMF API returned an empty response.")
    return response


def data_url(flow: str, key: str) -> str:
    return f"{BASE_URL}/data/dataflow/{AGENCY}/{flow}/~/{key}?c[TIME_PERIOD]=ge:{START_PERIOD}"


def normalize_period(values: pd.Series) -> pd.DatetimeIndex:
    text = values.astype(str).str.strip().str.replace(r"-M(\d{2})$", r"-\1", regex=True)
    dates = pd.to_datetime(text, format="%Y-%m", errors="coerce")
    return pd.DatetimeIndex(dates).to_period("M").to_timestamp()


def parse_imf_csv(payload: bytes, series_name: str) -> pd.Series:
    """Parse CSV response defensively and collapse duplicate months only if values agree."""
    try:
        frame = pd.read_csv(io.BytesIO(payload))
    except (UnicodeDecodeError, pd.errors.ParserError) as exc:
        raise IMFDataError(f"IMF returned an unreadable {series_name} response.") from exc
    columns = {str(c).upper(): c for c in frame.columns}
    if "TIME_PERIOD" not in columns or "OBS_VALUE" not in columns:
        raise IMFDataError(f"IMF {series_name} response did not include TIME_PERIOD and OBS_VALUE columns.")
    parsed = pd.DataFrame({
        "month": normalize_period(frame[columns["TIME_PERIOD"]]),
        "value": pd.to_numeric(frame[columns["OBS_VALUE"]], errors="coerce"),
    }).dropna(subset=["month"])
    # Do not choose among conflicting duplicate data without disclosure.
    conflicting = parsed.groupby("month")["value"].nunique(dropna=True)
    if (conflicting > 1).any():
        raise IMFDataError(f"IMF {series_name} response has conflicting duplicate monthly observations.")
    return parsed.groupby("month")["value"].first().sort_index().rename(series_name)


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False)
def fetch_series(code: str, flow: str, indicator: str) -> pd.Series:
    key = f"{code}.{indicator}.M"
    return parse_imf_csv(get_response(data_url(flow, key)).content, f"{flow}_{code}")


def _walk_country_candidates(value: Any) -> Iterable[tuple[str, str]]:
    """Tolerate the alternate JSON shapes used by SDMX structure responses."""
    if isinstance(value, dict):
        code = value.get("id") or value.get("code")
        name = value.get("name") or value.get("names")
        if isinstance(name, dict):
            name = name.get("en") or next(iter(name.values()), None)
        if isinstance(code, str) and isinstance(name, str) and 2 <= len(code) <= 5:
            yield code, name
        for nested in value.values():
            yield from _walk_country_candidates(nested)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_country_candidates(item)


def _country_codelists(value: Any) -> Iterable[Any]:
    """Locate only codelists explicitly identified as country/geography lists."""
    if isinstance(value, dict):
        identifier = str(value.get("id", ""))
        if "COUNTRY" in identifier.upper() or "GEOGRAPH" in identifier.upper():
            yield value
        for key, nested in value.items():
            if "codelist" in key.lower():
                if isinstance(nested, dict):
                    for list_id, list_value in nested.items():
                        if "COUNTRY" in str(list_id).upper() or "GEOGRAPH" in str(list_id).upper():
                            yield list_value
                elif isinstance(nested, list):
                    for list_value in nested:
                        if isinstance(list_value, dict) and ("COUNTRY" in str(list_value.get("id", "")).upper() or "GEOGRAPH" in str(list_value.get("id", "")).upper()):
                            yield list_value
            yield from _country_codelists(nested)
    elif isinstance(value, list):
        for item in value:
            yield from _country_codelists(item)


@st.cache_data(ttl=24 * 60 * 60, show_spinner=False)
def fetch_entities() -> tuple[dict[str, str], str | None]:
    """Try structure metadata; return a validated fallback rather than blocking the dashboard."""
    url = f"{BASE_URL}/structure/dataflow/{AGENCY}/CPI/~?references=all"
    try:
        # IMF currently serves its structure payload under the unversioned SDMX JSON media type.
        response = get_response(url, "application/vnd.sdmx.structure+json")
        document = response.json()
        candidates: dict[str, str] = {}
        for codelist in _country_codelists(document):
            candidates.update(_walk_country_candidates(codelist))
        usable = {k: v for k, v in candidates.items() if k in FALLBACK_COUNTRIES or re.fullmatch(r"[A-Z]{3}", k)}
        if len(usable) >= 100:
            usable["G163"] = "Euro Area"
            return dict(sorted(usable.items(), key=lambda item: item[1])), None
    except (IMFDataError, ValueError, requests.RequestException):
        pass
    return dict(sorted(FALLBACK_COUNTRIES.items(), key=lambda item: item[1])), "IMF country metadata is unavailable; using the built-in country list."


def isolated_interpolation(raw: pd.Series, enabled: bool) -> tuple[pd.Series, pd.Series]:
    """Fill only one missing interior calendar month; retain a separate Boolean provenance flag."""
    raw = raw.astype(float).copy()
    used = raw.copy()
    flag = pd.Series(False, index=raw.index, dtype=bool)
    if not enabled:
        return used, flag
    for pos in range(1, len(raw) - 1):
        if pd.isna(raw.iloc[pos]) and pd.notna(raw.iloc[pos - 1]) and pd.notna(raw.iloc[pos + 1]):
            # Index is a complete monthly range, so neighbors are immediately adjacent months.
            used.iloc[pos] = (raw.iloc[pos - 1] + raw.iloc[pos + 1]) / 2
            flag.iloc[pos] = True
    return used, flag


def full_month_index(series_list: list[pd.Series]) -> pd.DatetimeIndex:
    observed = pd.concat(series_list).dropna()
    if observed.empty:
        raise IMFDataError("The IMF returned no usable monthly observations for this comparison.")
    return pd.date_range(observed.index.min(), observed.index.max(), freq="MS")


def make_calculation(c1: Entity, c2: Entity, interpolate: bool) -> pd.DataFrame:
    """Retrieve sources, preserve raw values, and calculate bilateral nominal and real measures."""
    cpi1, fx1 = fetch_series(c1.code, "CPI", "CPI._T.IX"), fetch_series(c1.code, "ER", "XDC_USD.PA_RT")
    if c1.code == c2.code:
        cpi2, fx2 = cpi1.copy(), fx1.copy()
    else:
        cpi2, fx2 = fetch_series(c2.code, "CPI", "CPI._T.IX"), fetch_series(c2.code, "ER", "XDC_USD.PA_RT")
    index = full_month_index([cpi1, cpi2, fx1, fx2])
    frame = pd.DataFrame(index=index)
    for name, series in (("CPI_1", cpi1), ("CPI_2", cpi2), ("FX_1", fx1), ("FX_2", fx2)):
        frame[f"{name}_Raw"] = series.reindex(index)
        frame[f"{name}_Used"], frame[f"{name}_Interpolated"] = isolated_interpolation(frame[f"{name}_Raw"], interpolate)
    positive = pd.DataFrame({name: frame[f"{name}_Used"] for name in ("CPI_1", "CPI_2", "FX_1", "FX_2")})
    frame["Valid_RER"] = positive.notna().all(axis=1) & np.isfinite(positive).all(axis=1) & (positive > 0).all(axis=1)
    frame["Nominal_S_C2_per_C1"] = np.where(frame["Valid_RER"], frame["FX_2_Used"] / frame["FX_1_Used"], np.nan)
    frame["RER"] = np.where(frame["Valid_RER"], frame["Nominal_S_C2_per_C1"] * frame["CPI_1_Used"] / frame["CPI_2_Used"], np.nan)
    return frame


def apply_sample(frame: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> tuple[pd.DataFrame, float, int]:
    selected = frame.loc[(frame.index >= start) & (frame.index <= end)].copy()
    valid = selected.loc[selected["Valid_RER"], "RER"]
    mean = float(valid.mean()) if not valid.empty else float("nan")
    selected["RER_Sample_Mean"] = mean
    selected["Real_Index"] = np.where(selected["Valid_RER"] & np.isfinite(mean) & (mean > 0), 100 * selected["RER"] / mean, np.nan)
    return selected, mean, int(valid.size)


def currency(code: str) -> str:
    return CURRENCY_HINTS.get(code, "local currency")


def chart(sample: pd.DataFrame, mode: str, c1: Entity, c2: Entity) -> go.Figure:
    nominal_title = f"Nominal S ({currency(c2.code)} per {currency(c1.code)})"
    if mode == "Both":
        fig = make_subplots(specs=[[{"secondary_y": True}]])
        fig.add_trace(go.Scatter(x=sample.index, y=sample["Real_Index"], name="Real Index", line=dict(color="#087e8b", width=3)), secondary_y=False)
        fig.add_trace(go.Scatter(x=sample.index, y=sample["Nominal_S_C2_per_C1"], name="Nominal S", line=dict(color="#f28e2b", width=2.5)), secondary_y=True)
        fig.add_hline(y=100, line_dash="dot", line_color="#64748b", annotation_text="Sample average = 100", secondary_y=False)
        fig.update_yaxes(title_text="Real Exchange Rate Index", secondary_y=False)
        fig.update_yaxes(title_text=nominal_title, secondary_y=True)
    else:
        column, title, color = (("Real_Index", "Real Exchange Rate Index", "#087e8b") if mode == "Real" else ("Nominal_S_C2_per_C1", nominal_title, "#f28e2b"))
        fig = go.Figure(go.Scatter(x=sample.index, y=sample[column], name=title, line=dict(color=color, width=3)))
        fig.update_yaxes(title_text=title)
        if mode == "Real":
            fig.add_hline(y=100, line_dash="dot", line_color="#64748b", annotation_text="Sample average = 100")
    fig.update_layout(template="plotly_white", height=470, hovermode="x unified", margin=dict(l=10, r=10, t=45, b=10), legend=dict(orientation="h", y=1.12), xaxis_title="Month")
    fig.update_xaxes(showgrid=False)
    return fig


def export_csv(sample: pd.DataFrame, c1: Entity, c2: Entity) -> bytes:
    exported = sample.copy()
    exported.insert(0, "Month", exported.index.strftime("%Y-%m"))
    exported.insert(1, "Country_1", c1.name)
    exported.insert(2, "Country_2", c2.name)
    exported.insert(3, "Country_1_Currency", currency(c1.code))
    exported.insert(4, "Country_2_Currency", currency(c2.code))
    order = ["Month", "Country_1", "Country_2", "Country_1_Currency", "Country_2_Currency", "CPI_1_Raw", "CPI_2_Raw", "CPI_1_Used", "CPI_2_Used", "FX_1_Raw", "FX_2_Raw", "FX_1_Used", "FX_2_Used", "Nominal_S_C2_per_C1", "RER", "RER_Sample_Mean", "Real_Index", "CPI_1_Interpolated", "CPI_2_Interpolated", "FX_1_Interpolated", "FX_2_Interpolated", "Valid_RER"]
    return exported.reindex(columns=order).to_csv(index=False, quoting=csv.QUOTE_MINIMAL).encode("utf-8")


def month_label(value: pd.Timestamp) -> str:
    return value.strftime("%B %Y")


def main() -> None:
    st.set_page_config(page_title=APP_TITLE, page_icon="📈", layout="wide")
    st.markdown("""<style>
    .block-container {max-width: 1400px; padding-top: 2rem;} [data-testid='stMetric'] {background:#f8fafc; border:1px solid #e2e8f0; border-radius:14px; padding:1rem;} .hero {background:linear-gradient(120deg,#0f2747,#087e8b);color:white;border-radius:18px;padding:1.5rem 2rem;margin-bottom:1.4rem;} .hero h1 {margin:0;font-size:2rem;} .hero p {margin:.4rem 0 0;opacity:.9;}</style>""", unsafe_allow_html=True)
    countries, catalog_warning = fetch_entities()
    options = [Entity(code, name) for code, name in countries.items()]
    by_code = {e.code: e for e in options}
    default1, default2 = by_code.get("USA", options[0]), by_code.get("IND", options[min(1, len(options)-1)])
    with st.sidebar:
        st.header("Comparison settings")
        c1 = st.selectbox("Country 1", options, index=options.index(default1), format_func=lambda e: e.label)
        c2 = st.selectbox("Country 2", options, index=options.index(default2), format_func=lambda e: e.label)
        handling = st.radio("Missing-data handling", ["Conservative interpolation", "Original IMF data only"], help="Conservative mode fills only isolated interior monthly gaps.")
        mode = st.segmented_control("Chart", ["Real", "Nominal", "Both"], default="Real")
        if st.button("Reset to defaults", use_container_width=True):
            st.rerun()
    st.markdown(f"<div class='hero'><h1>FX Lens <span style='opacity:.55'>|</span> Real Exchange Rate Explorer</h1><p>Explore currency movements through inflation-adjusted exchange rates.</p><p><b>{c1.label} &nbsp; vs &nbsp; {c2.label}</b> &nbsp;•&nbsp; S = {currency(c2.code)} per {currency(c1.code)}</p></div>", unsafe_allow_html=True)
    if catalog_warning:
        st.info(catalog_warning)
    try:
        with st.spinner("Retrieving available monthly IMF CPI and exchange-rate data…"):
            calculated = make_calculation(c1, c2, handling == "Conservative interpolation")
    except IMFDataError as exc:
        st.error(str(exc))
        st.caption("Source: IMF SDMX 3.0 CPI and ER dataflows. No values were fabricated.")
        return
    valid_dates = calculated.index[calculated["Valid_RER"]]
    if valid_dates.empty:
        st.warning("The selected entities have no common months with all four valid source observations. Try a different pair.")
        return
    latest = valid_dates.max()
    default_start = max(valid_dates.min(), latest - pd.DateOffset(years=10) + pd.offsets.MonthBegin(0))
    with st.sidebar:
        months = list(calculated.index)
        start = st.selectbox("Start month", months, index=months.index(default_start) if default_start in months else 0, format_func=month_label)
        allowed_end = [m for m in months if m >= start]
        end = st.selectbox("End month", allowed_end, index=len(allowed_end)-1, format_func=month_label)
    sample, rer_mean, count = apply_sample(calculated, start, end)
    if not count:
        st.warning("No valid RER observations exist in this selected period. Expand or change the dates.")
        return
    recent = sample.loc[sample["Valid_RER"]].iloc[-1]
    interpolated_count = int(sample[["CPI_1_Interpolated", "CPI_2_Interpolated", "FX_1_Interpolated", "FX_2_Interpolated"]].sum().sum())
    metrics = st.columns(5)
    metrics[0].metric("Latest real index", f"{recent['Real_Index']:.2f}")
    metrics[1].metric("Latest nominal S", f"{recent['Nominal_S_C2_per_C1']:.6g}")
    metrics[2].metric("RER* (sample mean)", f"{rer_mean:.6g}")
    metrics[3].metric("Valid months", f"{count:,}")
    metrics[4].metric("Selected sample", f"{start:%b %Y} – {end:%b %Y}")
    st.caption(f"Currency direction: **{c2.name} currency units per one {c1.name} currency unit**. Latest valid observation: {recent.name:%B %Y}.")
    if interpolated_count:
        st.warning(f"{interpolated_count} monthly source observation(s) were interpolated in the selected period; these are estimates, not IMF-published values.")
    st.plotly_chart(chart(sample, mode or "Real", c1, c2), use_container_width=True, config={"displaylogo": False})
    filename = f"fx_lens_{c1.code}_{c2.code}_{start:%Y%m}_{end:%Y%m}.csv"
    st.download_button("Download CSV", export_csv(sample, c1, c2), file_name=filename, mime="text/csv", use_container_width=False)
    with st.expander("Methodology & Data Quality"):
        st.markdown(f"""**Formula.** `S(t) = E2(t) / E1(t)`, where each `E` is domestic currency units per USD from IMF. Therefore `S` is {currency(c2.code)} per {currency(c1.code)}. The bilateral real exchange rate is `RER(t) = S(t) × P1(t) / P2(t)`.

**Index.** `RER*` is the arithmetic mean of valid monthly RER observations in your selected period. The Real Index is `100 × RER(t) / RER*`; 100 means the selected sample average, not an estimated equilibrium or a verdict that a currency is over- or undervalued.

**Data quality.** IMF CPI levels may use different bases across countries. Currency replacements, redenominations, rebasing, and methodological changes can create discontinuities; this dashboard discloses missing data and does not apply unverified adjustments. Interpolation, when enabled, fills only isolated interior gaps using the average of immediately adjacent months. Endpoint and multi-month gaps remain missing.

**Coverage.** Available coverage runs from {calculated.index.min():%B %Y} to {calculated.index.max():%B %Y}; the effective comparison period is the overlap of all four required series. Raw and used values, flags, and unavailable calculations are included in the CSV.""")
    st.caption(f"Data source: International Monetary Fund, SDMX 3.0 CPI and ER dataflows • Last successful retrieval in this session: {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}")


if __name__ == "__main__":
    main()
