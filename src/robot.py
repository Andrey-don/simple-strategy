"""
ORB Trading Robot — главный цикл.

Логика:
  1. Каждые 5 минут (после закрытия свечи) получаем свежие M5-данные.
  2. Вычисляем ORB-диапазон и VWAP.
  3. Если сигнал есть и сделка в этот день ещё не совершалась — входим.
  4. Мониторим позицию: закрываем по стопу или тейку.
  5. После 18:45 МСК (15:45 UTC) новых входов нет; позиция закрывается принудительно.
"""

import os
import sys
import time
import logging
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(__file__))
from data import find_instrument, get_candles
from strategy import opening_range, get_signal, calc_vwap, Signal
from broker import (
    get_or_create_account, top_up,
    place_order, get_position,
    DIRECTION_BUY, DIRECTION_SELL,
)
from dotenv import load_dotenv

load_dotenv()

SANDBOX = os.getenv("TINKOFF_SANDBOX", "false").lower() == "true"

# Параметры
TICKER        = "SiM6"
QUANTITY      = 1               # лотов
POLL_SECONDS  = 30              # пауза между проверками
SESSION_END_UTC_HOUR = 15       # 18:45 МСК ≈ 15:45 UTC — прекращаем входы

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def session_date_range() -> tuple[datetime, datetime]:
    """Сегодняшняя сессия: 07:00–16:00 UTC."""
    today = now_utc().date()
    from_dt = datetime(today.year, today.month, today.day, 7, 0, tzinfo=timezone.utc)
    to_dt   = from_dt + timedelta(hours=9)
    return from_dt, to_dt


def close_position(figi: str, account_id: str) -> None:
    pos = get_position(account_id, figi)
    if not pos:
        return
    qty = int(pos.get("quantity", {}).get("units", 0))
    if qty == 0:
        return
    direction = DIRECTION_SELL if qty > 0 else DIRECTION_BUY
    place_order(figi, direction, abs(qty), account_id)
    log.info("Позиция закрыта принудительно (%+d лот)", qty)


def run() -> None:
    log.info("Робот запущен | режим=%s | тикер=%s", "SANDBOX" if SANDBOX else "PROD", TICKER)

    account_id = get_or_create_account()
    log.info("Аккаунт: %s", account_id)

    if SANDBOX:
        top_up(account_id, 500_000)

    inst = find_instrument(TICKER)
    if not inst:
        log.error("Инструмент %s не найден", TICKER)
        return
    figi = inst["figi"]
    log.info("Инструмент: %s | FIGI: %s", inst["name"], figi)

    traded_today: str | None = None   # дата когда уже была сделка
    active_signal: Signal | None = None

    while True:
        now = now_utc()
        today_str = now.date().isoformat()

        # Конец торгового дня — закрыть позицию и сбросить флаг
        if now.hour >= SESSION_END_UTC_HOUR + 1:
            if active_signal:
                log.info("Конец сессии — закрываем позицию")
                close_position(figi, account_id)
                active_signal = None
            time.sleep(POLL_SECONDS)
            continue

        # Получаем свечи текущей сессии
        from_dt, to_dt = session_date_range()
        df = get_candles(figi, from_dt, now, "5m")

        if df.empty:
            log.info("Свечей нет — ждём открытия сессии")
            time.sleep(POLL_SECONDS)
            continue

        orb = opening_range(df)
        if orb is None:
            log.info("ORB ещё не сформирован (нужно 2 свечи)")
            time.sleep(POLL_SECONDS)
            continue

        # --- Мониторинг открытой позиции ---
        if active_signal:
            close = df["close"].iloc[-1]
            if active_signal.direction == "long":
                if close <= active_signal.stop:
                    log.info("СТОП-ЛОСС сработал: close=%.0f stop=%.0f", close, active_signal.stop)
                    close_position(figi, account_id)
                    active_signal = None
                elif close >= active_signal.take:
                    log.info("ТЕЙК-ПРОФИТ сработал: close=%.0f take=%.0f", close, active_signal.take)
                    close_position(figi, account_id)
                    active_signal = None
            else:  # short
                if close >= active_signal.stop:
                    log.info("СТОП-ЛОСС сработал: close=%.0f stop=%.0f", close, active_signal.stop)
                    close_position(figi, account_id)
                    active_signal = None
                elif close <= active_signal.take:
                    log.info("ТЕЙК-ПРОФИТ сработал: close=%.0f take=%.0f", close, active_signal.take)
                    close_position(figi, account_id)
                    active_signal = None
            time.sleep(POLL_SECONDS)
            continue

        # --- Поиск сигнала (одна сделка в день) ---
        if traded_today == today_str:
            log.info("Сделка сегодня уже была — пропускаем до конца сессии")
            time.sleep(POLL_SECONDS)
            continue

        sig = get_signal(df, candle_idx=-1)
        if sig:
            log.info(
                "СИГНАЛ %s | entry=%.0f stop=%.0f take=%.0f risk=%.0f",
                sig.direction.upper(), sig.entry, sig.stop, sig.take, sig.risk,
            )
            direction = DIRECTION_BUY if sig.direction == "long" else DIRECTION_SELL
            order = place_order(figi, direction, QUANTITY, account_id)
            log.info("Ордер %s — статус: %s", order.get("orderId"), order.get("executionReportStatus"))
            active_signal = sig
            traded_today = today_str
        else:
            vwap_val = calc_vwap(df).iloc[-1]
            close = df["close"].iloc[-1]
            log.info(
                "ORB %.0f–%.0f | close=%.0f | vwap=%.0f | сигнала нет",
                orb.low, orb.high, close, vwap_val,
            )

        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    try:
        run()
    except KeyboardInterrupt:
        log.info("Робот остановлен (Ctrl+C)")
