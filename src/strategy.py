from dataclasses import dataclass
from typing import Literal
import pandas as pd

# 10:00 МСК = 07:00 UTC
SESSION_OPEN_UTC_HOUR = 7
ORB_CANDLES = 2        # первые 2 свечи M5 формируют диапазон
RR = 2.0               # соотношение риск/прибыль


@dataclass
class OpeningRange:
    high: float
    low: float

    @property
    def size(self) -> float:
        return self.high - self.low


@dataclass
class Signal:
    direction: Literal["long", "short"]
    entry: float
    stop: float
    take: float

    @property
    def risk(self) -> float:
        return abs(self.entry - self.stop)


def _session_open(df: pd.DataFrame) -> pd.Timestamp:
    """07:00 UTC (10:00 МСК) для даты первой свечи в df."""
    date = df.index[0].date()
    return pd.Timestamp(year=date.year, month=date.month, day=date.day,
                        hour=SESSION_OPEN_UTC_HOUR, tz="UTC")


def opening_range(df: pd.DataFrame) -> OpeningRange | None:
    """Диапазон первых ORB_CANDLES свечей M5 после открытия сессии."""
    open_ts = _session_open(df)
    end_ts = open_ts + pd.Timedelta(minutes=5 * ORB_CANDLES)
    candles = df[(df.index >= open_ts) & (df.index < end_ts)]
    if len(candles) < ORB_CANDLES:
        return None
    return OpeningRange(high=candles["high"].max(), low=candles["low"].min())


def calc_vwap(df: pd.DataFrame) -> pd.Series:
    """Внутридневной VWAP от открытия сессии."""
    open_ts = _session_open(df)
    session = df[df.index >= open_ts].copy()
    typical = (session["high"] + session["low"] + session["close"]) / 3
    return (typical * session["volume"]).cumsum() / session["volume"].cumsum()


def get_signal(df: pd.DataFrame, candle_idx: int = -1) -> Signal | None:
    """
    Проверяет сигнал на пробой ORB для свечи candle_idx.
    Возвращает Signal или None.
    """
    orb = opening_range(df)
    if orb is None:
        return None

    open_ts = _session_open(df)
    orb_end = open_ts + pd.Timedelta(minutes=5 * ORB_CANDLES)

    candle_time = df.index[candle_idx]
    if candle_time < orb_end:
        return None  # диапазон ещё не сформирован

    close = df["close"].iloc[candle_idx]
    vwap_series = calc_vwap(df)

    # vwap может не содержать candle_idx если свеча вне сессии
    if candle_time not in vwap_series.index:
        return None
    current_vwap = vwap_series[candle_time]

    # Лонг: пробой вверх И цена выше VWAP
    if close > orb.high and close > current_vwap:
        entry = orb.high
        stop = orb.low
        return Signal(
            direction="long",
            entry=entry,
            stop=stop,
            take=entry + RR * (entry - stop),
        )

    # Шорт: пробой вниз И цена ниже VWAP
    if close < orb.low and close < current_vwap:
        entry = orb.low
        stop = orb.high
        return Signal(
            direction="short",
            entry=entry,
            stop=stop,
            take=entry - RR * (orb.high - entry),
        )

    return None


if __name__ == "__main__":
    import sys
    import os
    sys.path.insert(0, os.path.dirname(__file__))
    from data import find_instrument, get_candles
    from datetime import datetime, timezone

    inst = find_instrument("SiM6")
    if not inst:
        print("Инструмент не найден")
        sys.exit(1)

    figi = inst["figi"]
    print(f"Инструмент: {inst['name']} | FIGI: {figi}")

    from_dt = datetime(2026, 4, 14, 7, 0, tzinfo=timezone.utc)
    to_dt   = datetime(2026, 4, 14, 16, 0, tzinfo=timezone.utc)
    df = get_candles(figi, from_dt, to_dt, "5m")

    if df.empty:
        print("Нет данных за выбранный период")
        sys.exit(1)

    orb = opening_range(df)
    print(f"\nORB: High={orb.high:.0f}  Low={orb.low:.0f}  Size={orb.size:.0f}")

    vwap = calc_vwap(df)
    print(f"VWAP (последний): {vwap.iloc[-1]:.0f}")

    print("\nПервый сигнал за день (одна сделка в день):")
    for i in range(len(df)):
        sig = get_signal(df, candle_idx=i)
        if sig:
            t = df.index[i].strftime("%H:%M UTC")
            vwap_val = calc_vwap(df)[df.index[i]]
            print(f"  Время:     {t}")
            print(f"  Сигнал:    {sig.direction.upper()}")
            print(f"  Вход:      {sig.entry:.0f}")
            print(f"  Стоп:      {sig.stop:.0f}")
            print(f"  Тейк:      {sig.take:.0f}")
            print(f"  Риск:      {sig.risk:.0f} пп")
            print(f"  VWAP:      {vwap_val:.0f}")
            break
    else:
        print("  Сигналов нет")
