#!/usr/bin/env python3
"""
Aggiorna le colonne US_* del CSV con i rendimenti mensili del Treasury 10Y.

Fonte : FRED DGS10 (giornaliero). Valore mensile = ultima osservazione
        disponibile del mese (convenzione del dataset Swinkels, es. 2022-12 = 3.88).
Formula (identica al foglio Improved_US_1947, t in mesi, maturity 10 - 1/12):
    d_t      = (1 + y_t/200) ^ (-2 * (10 - 1/12))
    Return_M = y_{t-1}/1200 + (y_{t-1}/y_t) * (1 - d_t) + d_t - 1
    Cum_Ret  = Cum_{t-1} * (1 + Return_M)
Aggiunge solo i mesi chiusi mancanti; le righe esistenti non vengono toccate.
"""
import csv
import io
import os
import sys
import time
from datetime import date, timedelta

import pandas as pd
import requests

CSV_PATH = os.environ.get("CSV_PATH", "msci_ftse/international_gov_bond_10y_monthly.csv")
FRED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS10"
DGS10_LOCAL = os.environ.get("DGS10_LOCAL")  # solo per test offline
GRACE_DAYS = 3        # giorni dopo fine mese prima di considerarlo chiuso (lag pubblicazione FRED)
MATURITY = 10 - 1 / 12


def fetch_dgs10() -> pd.Series:
    if DGS10_LOCAL:
        raw = open(DGS10_LOCAL).read()
    else:
        for attempt in range(4):
            try:
                r = requests.get(FRED_URL, timeout=60,
                                 headers={"User-Agent": "Mozilla/5.0 (github-actions data updater)"})
                r.raise_for_status()
                raw = r.text
                break
            except requests.RequestException as e:
                print(f"Tentativo {attempt + 1} fallito: {e}", file=sys.stderr)
                time.sleep(10 * (attempt + 1))
        else:
            sys.exit("Impossibile scaricare DGS10 da FRED")
    df = pd.read_csv(io.StringIO(raw))
    df.columns = ["date", "value"]                        # observation_date / DGS10
    df["date"] = pd.to_datetime(df["date"])
    df["value"] = pd.to_numeric(df["value"], errors="coerce")  # '.' o vuoto = festivo
    return df.dropna().set_index("date")["value"].sort_index()


def monthly_close(daily: pd.Series, today: date) -> pd.Series:
    m = daily.groupby(daily.index.to_period("M")).last()
    m.index = m.index.to_timestamp(how="end").normalize()     # -> ultimo giorno del mese
    closed = m.index <= pd.Timestamp(today - timedelta(days=GRACE_DAYS))
    return m[closed]


def ret_m(y_prev: float, y: float) -> float:
    d = (1 + y / 200) ** (-2 * MATURITY)
    return y_prev / 1200 + (y_prev / y) * (1 - d) + d - 1


def main():
    hist = pd.read_csv(CSV_PATH, parse_dates=["observation_date"])
    header = list(hist.columns)
    us = hist.dropna(subset=["US_Yield_10Y", "US_Cum_Ret"])
    last = us.iloc[-1]
    last_date = last["observation_date"]
    if last_date != hist["observation_date"].max():
        sys.exit("Esistono righe successive all'ultimo dato US: gestione non prevista, verificare il CSV")

    monthly = monthly_close(fetch_dgs10(), date.today())
    new = monthly[monthly.index > last_date]
    if new.empty:
        print(f"Nessun nuovo mese chiuso (ultimo nel CSV: {last_date.date()})")
        return

    expected = pd.date_range(last_date, periods=len(new) + 1, freq="ME")[1:]
    if not new.index.equals(expected):
        sys.exit(f"Mesi non contigui da FRED: {list(new.index.date)}")

    y_prev, cum = float(last["US_Yield_10Y"]), float(last["US_Cum_Ret"])
    rows = []
    for dt, y in new.items():
        r = ret_m(y_prev, y)
        cum *= 1 + r
        row = dict.fromkeys(header, "")
        row.update(observation_date=dt.strftime("%Y-%m-%d"),
                   US_Yield_10Y=repr(float(y)), US_Return_M=repr(r), US_Cum_Ret=repr(cum))
        rows.append(row)
        y_prev = y

    with open(CSV_PATH, "rb") as f:                      # garantisce newline finale
        f.seek(-1, os.SEEK_END)
        needs_nl = f.read(1) != b"\n"
    with open(CSV_PATH, "a", newline="") as f:
        if needs_nl:
            f.write("\n")
        csv.DictWriter(f, fieldnames=header, lineterminator="\n").writerows(rows)

    print(f"Aggiunti {len(rows)} mesi: {rows[0]['observation_date']} -> {rows[-1]['observation_date']}")


if __name__ == "__main__":
    main()
