# FX Lens | Real Exchange Rate Explorer

FX Lens is a Streamlit dashboard for comparing two economies using monthly Consumer Price Index (CPI) data and exchange rates from the International Monetary Fund (IMF). It calculates a bilateral nominal exchange rate, a real exchange rate (RER), and a real-exchange-rate index that is normalized to the period you choose.

It is designed to run locally with no account, API key, paid service, or downloaded dataset.

## What it does

- Retrieves monthly IMF CPI (`CPI`) and exchange-rate (`ER`) observations.
- Compares countries through a searchable selector; the Euro Area is available as `G163`.
- Calculates a bilateral nominal rate, unindexed RER, sample RER mean, and 100-based real index.
- Offers real, nominal, and two-axis comparison charts.
- Transparently handles an isolated missing source month, or uses original IMF data only.
- Exports raw data, processed data, calculations, and interpolation flags to UTF-8 CSV.

## Prerequisites

You need Python 3.12 and an internet connection. The public IMF API must be reachable from your network.

### Install Python 3.12

- **Windows:** download the Python 3.12 installer from [python.org](https://www.python.org/downloads/). During setup, check **Add Python to PATH**.
- **macOS:** download the macOS installer from [python.org](https://www.python.org/downloads/), then run it.
- **Linux:** use your distribution's package manager, or install Python 3.12 from [python.org](https://www.python.org/downloads/).

Open Terminal (macOS/Linux) or PowerShell (Windows), then confirm:

```bash
python --version
```

On some macOS/Linux installations use `python3 --version` instead.

## Install and run

Put these three files in one new folder. Open a terminal in that folder.

### 1. Create a virtual environment

**Windows PowerShell**

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
```

If PowerShell blocks activation, run `Set-ExecutionPolicy -Scope Process Bypass`, then activate again.

**macOS/Linux**

```bash
python3.12 -m venv .venv
source .venv/bin/activate
```

Your prompt should now show `(.venv)`.

### 2. Install packages

```bash
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### 3. Start the dashboard

```bash
streamlit run app.py
```

Streamlit normally opens your browser automatically. If it does not, open the displayed local URL, usually `http://localhost:8501`. To stop the app, return to the terminal and press `Ctrl+C`.

## How to use it

1. Pick **Country 1** and **Country 2** in the sidebar. The quoted nominal direction is always Country 2 currency units per one Country 1 currency unit.
2. Choose missing-data handling. **Conservative interpolation** is the default and estimates only a single missing interior month with both neighboring months observed. **Original IMF data only** performs no interpolation.
3. Set the start and end months. The displayed RER mean and real index recalculate immediately.
4. Choose Real, Nominal, or Both. The Both view uses separate axes so the real index and nominal currency value are never misleadingly placed on one scale.
5. Select **Download CSV** to obtain every calendar month in the selected range, including unavailable calculations.

## Methodology

The IMF ER source reports domestic currency units per USD.

- `E1(t)` = Country 1 currency units per USD
- `E2(t)` = Country 2 currency units per USD
- `S(t) = E2(t) / E1(t)` = Country 2 currency units per one Country 1 currency unit
- `RER(t) = S(t) × P1(t) / P2(t)`

For the date range you select, the app computes `RER*`, the arithmetic mean of all valid monthly RER values. It then computes:

`Real Index(t) = 100 × RER(t) / RER*`

Thus 100 is the average for **your selected sample**, not an estimate of fair value or economic equilibrium. A value above or below 100 should not automatically be called overvaluation or undervaluation.

Only months with all four positive, finite source values are valid. The nominal rate is never rebased or normalized.

## Interpolation and limitations

Conservative interpolation fills only an isolated missing calendar month when immediately preceding and following observed values are both available. It uses their arithmetic average. It never extrapolates, bridges multi-month gaps, overwrites raw IMF data, or invents unavailable data.

Countries can have different CPI bases, reporting histories, currency changes, redenominations, or methodological breaks. The dashboard does not silently splice series or apply unverified conversion factors. Read the in-app data-quality disclosure and inspect the CSV before drawing historical conclusions, especially around currency-regime changes and Euro adoption.

## IMF source and API

The app uses the official [IMF Data API](https://data.imf.org/en/Resource-Pages/IMF-API) and SDMX 3.0 endpoint:

`https://api.imf.org/external/sdmx/3.0`

It uses IMF Statistics Department CPI and ER topic dataflows. IMF moved legacy IFS indicators into topic datasets; see the [IMF's IFS migration notice](https://data.imf.org/en/news/accessing%20international%20financial%20statistics).

## Troubleshooting

| Problem | What to try |
|---|---|
| `streamlit` is not recognized | Activate `.venv`, then run `pip install -r requirements.txt` again. |
| IMF connection error | Check internet/VPN/firewall access to `api.imf.org`; wait briefly and rerun. The app retries temporary failures. |
| No common coverage | Pick another pair or a more recent/older date range. The app will not fabricate unavailable source observations. |
| Country not in fallback list | Retry later: the app normally loads the broader IMF metadata list when the API is reachable. |
| Port 8501 is busy | Run `streamlit run app.py --server.port 8502`. |
| Browser does not open | Copy the `Local URL` printed in the terminal into a browser. |

## Deploy with Streamlit Community Cloud

1. Create a GitHub account at [github.com](https://github.com), if needed.
2. Create a new repository and upload only `app.py`, `requirements.txt`, and `README.md` to its top level.
3. Go to [share.streamlit.io](https://share.streamlit.io/) and sign in with GitHub.
4. Select **Create app**, choose your repository and branch, and set the main file path to `app.py`.
5. Click **Deploy**. Streamlit installs `requirements.txt` automatically.

The deployed app still requires the visitor's network and Streamlit's hosting environment to reach the public IMF API.

## Future enhancements

- Event annotations for monetary-policy changes or currency crises.
- Saved favorite pairs and shareable dashboard links.
- Additional measures such as annual RER changes or rolling averages.
- A multi-pair comparison mode.
- Export of charts as publication-ready images.
