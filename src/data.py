import os
from datetime import datetime, timezone
from dotenv import load_dotenv
import requests
import pandas as pd

load_dotenv()

TOKEN = os.getenv("TINKOFF_TOKEN")
SANDBOX = os.getenv("TINKOFF_SANDBOX", "false").lower() == "true"

BASE_URL = (
    "https://sandbox-invest-public-api.tinkoff.ru/rest"
    if SANDBOX
    else "https://invest-public-api.tinkoff.ru/rest"
)

HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Content-Type": "application/json",
}

INTERVALS = {
    "1m":  "CANDLE_INTERVAL_1_MIN",
    "5m":  "CANDLE_INTERVAL_5_MIN",
    "15m": "CANDLE_INTERVAL_15_MIN",
    "1h":  "CANDLE_INTERVAL_HOUR",
    "1d":  "CANDLE_INTERVAL_DAY",
}


def _quotation_to_float(q: dict) -> float:
    return int(q.get("units", 0)) + q.get("nano", 0) / 1_000_000_000


def find_instrument(ticker: str) -> dict | None:
    url = f"{BASE_URL}/tinkoff.public.invest.api.contract.v1.InstrumentsService/FindInstrument"
    resp = requests.post(url, json={"query": ticker}, headers=HEADERS, timeout=10)
    resp.raise_for_status()
    instruments = resp.json().get("instruments", [])
    for inst in instruments:
        if inst.get("ticker") == ticker:
            return inst
    return instruments[0] if instruments else None


def get_candles(figi: str, from_dt: datetime, to_dt: datetime, interval: str = "5m") -> pd.DataFrame:
    url = f"{BASE_URL}/tinkoff.public.invest.api.contract.v1.MarketDataService/GetCandles"
    payload = {
        "figi": figi,
        "from": from_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "to": to_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "interval": INTERVALS[interval],
    }
    resp = requests.post(url, json=payload, headers=HEADERS, timeout=30)
    resp.raise_for_status()

    rows = []
    for c in resp.json().get("candles", []):
        rows.append({
            "time":   c["time"],
            "open":   _quotation_to_float(c["open"]),
            "high":   _quotation_to_float(c["high"]),
            "low":    _quotation_to_float(c["low"]),
            "close":  _quotation_to_float(c["close"]),
            "volume": int(c.get("volume", 0)),
        })

    df = pd.DataFrame(rows)
    if not df.empty:
        df["time"] = pd.to_datetime(df["time"], utc=True)
        df.set_index("time", inplace=True)
    return df


if __name__ == "__main__":
    print(f"Режим: {'sandbox' if SANDBOX else 'prod'}")
    print(f"Токен: {TOKEN[:10]}..." if TOKEN else "ТОКЕН НЕ НАЙДЕН!")

    # Ищем текущий контракт Si (июнь 2026)
    for ticker in ("SiM6", "SiU6", "SiZ6", "SiH6"):
        inst = find_instrument(ticker)
        if inst:
            break

    if inst:
        print(f"Инструмент: {inst.get('name')} | FIGI: {inst.get('figi')}")
        figi = inst["figi"]
        # Берём данные за конкретный торговый день (пн 14 апр 2025, сессия MOEX)
        from_dt = datetime(2025, 4, 14, 7, 0, tzinfo=timezone.utc)   # 10:00 МСК
        to_dt   = datetime(2025, 4, 14, 16, 0, tzinfo=timezone.utc)  # 19:00 МСК
        df = get_candles(figi, from_dt, to_dt, "5m")
        print(f"Свечей получено: {len(df)}")
        print(df.head())
    else:
        print("Инструмент не найден")
