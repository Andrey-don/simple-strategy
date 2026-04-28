"""
ORB Trading Robot — главный цикл с rich-дашбордом.

Запуск:
    python src/robot.py

Дашборд обновляется каждые POLL_SECONDS секунд.
"""

import os
import sys
import time
from collections import deque
from datetime import datetime, timezone, timedelta

from rich.live import Live
from rich.layout import Layout
from rich.panel import Panel
from rich.table import Table
from rich.console import Console
from rich import box

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

SANDBOX              = os.getenv("TINKOFF_SANDBOX", "false").lower() == "true"
TICKER               = "SiM6"
QUANTITY             = 1
POLL_SECONDS         = 30
SESSION_END_UTC_HOUR = 15

console   = Console()
LOG_LINES: deque[str] = deque(maxlen=15)


def _log(msg: str, style: str = "white") -> None:
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    LOG_LINES.append(f"[dim]{ts}[/]  [{style}]{msg}[/{style}]")


def _build_display(
    now: datetime,
    orb,
    price: float | None,
    vwap_val: float | None,
    active_signal: Signal | None,
    traded_today: bool,
    session_status: str,
) -> Layout:
    layout = Layout()
    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="body", ratio=1),
        Layout(name="logs", size=16),
    )
    layout["body"].split_row(
        Layout(name="market"),
        Layout(name="signal"),
    )

    mode_tag = "[yellow]SANDBOX[/]" if SANDBOX else "[green]PROD[/]"
    layout["header"].update(Panel(
        f"[bold]ORB ROBOT[/]  {TICKER}  │  "
        f"{now.strftime('%H:%M:%S UTC')}  │  {mode_tag}  │  [cyan]{session_status}[/]",
        box=box.MINIMAL,
    ))

    # --- Рынок ---
    t = Table(box=box.SIMPLE, show_header=False, padding=(0, 2))
    t.add_column("k", style="dim", min_width=12)
    t.add_column("v")
    orb_str = (
        f"[white]{orb.low:.0f}[/] – [white]{orb.high:.0f}[/]  [dim]({orb.size:.0f} пп)[/]"
        if orb else "[dim]ожидание...[/]"
    )
    price_str = f"[bold white]{price:.0f}[/]" if price is not None else "[dim]—[/]"
    vwap_str  = f"[white]{vwap_val:.0f}[/]" if vwap_val is not None else "[dim]—[/]"
    t.add_row("ORB",          orb_str)
    t.add_row("Цена",         price_str)
    t.add_row("VWAP",         vwap_str)
    t.add_row("Сделка сег.",  "[green]✔ была[/]" if traded_today else "[dim]не было[/]")
    layout["market"].update(Panel(t, title="[bold]Рынок[/]", border_style="blue"))

    # --- Позиция ---
    if active_signal:
        sc = "green" if active_signal.direction == "long" else "red"
        s = Table(box=box.SIMPLE, show_header=False, padding=(0, 2))
        s.add_column("k", style="dim", min_width=10)
        s.add_column("v")
        s.add_row("", f"[bold {sc}]▶ {active_signal.direction.upper()}[/]")
        s.add_row("Вход",  f"[white]{active_signal.entry:.0f}[/]")
        s.add_row("Стоп",  f"[red]{active_signal.stop:.0f}[/]")
        s.add_row("Тейк",  f"[green]{active_signal.take:.0f}[/]")
        s.add_row("Риск",  f"[dim]{active_signal.risk:.0f} пп[/]")
        layout["signal"].update(Panel(s, title="[bold]Позиция[/]", border_style=sc))
    else:
        layout["signal"].update(Panel(
            "\n[dim]нет открытой позиции[/]",
            title="Позиция", border_style="dim",
        ))

    # --- Журнал ---
    lines = "\n".join(LOG_LINES) if LOG_LINES else "[dim]ожидание...[/]"
    layout["logs"].update(Panel(lines, title="Журнал", border_style="dim"))

    return layout


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def session_date_range() -> tuple[datetime, datetime]:
    today = now_utc().date()
    from_dt = datetime(today.year, today.month, today.day, 7, 0, tzinfo=timezone.utc)
    return from_dt, from_dt + timedelta(hours=9)


def close_position(figi: str, account_id: str) -> None:
    pos = get_position(account_id, figi)
    if not pos:
        return
    qty = int(pos.get("quantity", {}).get("units", 0))
    if qty == 0:
        return
    direction = DIRECTION_SELL if qty > 0 else DIRECTION_BUY
    place_order(figi, direction, abs(qty), account_id)
    _log("Позиция закрыта принудительно", "yellow")


def run() -> None:
    _log(f"Робот запущен | {'SANDBOX' if SANDBOX else 'PROD'} | {TICKER}")

    account_id = get_or_create_account()
    _log(f"Аккаунт: {account_id}")

    if SANDBOX:
        top_up(account_id, 500_000)

    inst = find_instrument(TICKER)
    if not inst:
        console.print(f"[red]Инструмент {TICKER} не найден[/]")
        return
    figi = inst["figi"]
    _log(f"Инструмент: {inst['name']} | FIGI: {figi}")

    traded_today: str | None = None
    active_signal: Signal | None = None

    with Live(console=console, refresh_per_second=1, screen=True) as live:
        while True:
            now       = now_utc()
            today_str = now.date().isoformat()
            orb       = None
            price     = None
            vwap_val  = None
            session_status = "активна"

            # --- Конец торгового дня ---
            if now.hour >= SESSION_END_UTC_HOUR + 1:
                session_status = "завершена"
                if active_signal:
                    _log("Конец сессии — закрываем позицию", "yellow")
                    close_position(figi, account_id)
                    active_signal = None
                live.update(_build_display(
                    now, orb, price, vwap_val, active_signal,
                    traded_today == today_str, session_status,
                ))
                time.sleep(POLL_SECONDS)
                continue

            # --- Получаем свечи ---
            from_dt, _ = session_date_range()
            df = get_candles(figi, from_dt, now, "5m")

            if df.empty:
                session_status = "ожидание открытия"
                _log("Свечей нет — ждём открытия сессии")
                live.update(_build_display(
                    now, orb, price, vwap_val, active_signal,
                    traded_today == today_str, session_status,
                ))
                time.sleep(POLL_SECONDS)
                continue

            orb   = opening_range(df)
            price = float(df["close"].iloc[-1])
            try:
                vwap_val = float(calc_vwap(df).iloc[-1])
            except Exception:
                vwap_val = None

            if orb is None:
                session_status = "формирование ORB"
                live.update(_build_display(
                    now, orb, price, vwap_val, active_signal,
                    traded_today == today_str, session_status,
                ))
                time.sleep(POLL_SECONDS)
                continue

            # --- Мониторинг открытой позиции ---
            if active_signal:
                close_p = price
                hit = False
                if active_signal.direction == "long":
                    if close_p <= active_signal.stop:
                        _log(f"СТОП-ЛОСС: {close_p:.0f} ≤ {active_signal.stop:.0f}", "red")
                        hit = True
                    elif close_p >= active_signal.take:
                        _log(f"ТЕЙК-ПРОФИТ: {close_p:.0f} ≥ {active_signal.take:.0f}", "green")
                        hit = True
                else:
                    if close_p >= active_signal.stop:
                        _log(f"СТОП-ЛОСС: {close_p:.0f} ≥ {active_signal.stop:.0f}", "red")
                        hit = True
                    elif close_p <= active_signal.take:
                        _log(f"ТЕЙК-ПРОФИТ: {close_p:.0f} ≤ {active_signal.take:.0f}", "green")
                        hit = True
                if hit:
                    close_position(figi, account_id)
                    active_signal = None
                live.update(_build_display(
                    now, orb, price, vwap_val, active_signal,
                    traded_today == today_str, session_status,
                ))
                time.sleep(POLL_SECONDS)
                continue

            # --- Поиск сигнала (одна сделка в день) ---
            if traded_today == today_str:
                session_status = "сделка выполнена"
                live.update(_build_display(
                    now, orb, price, vwap_val, active_signal, True, session_status,
                ))
                time.sleep(POLL_SECONDS)
                continue

            sig = get_signal(df, candle_idx=-1)
            if sig:
                _log(
                    f"СИГНАЛ {sig.direction.upper()}: "
                    f"entry={sig.entry:.0f}  stop={sig.stop:.0f}  take={sig.take:.0f}",
                    "cyan",
                )
                direction = DIRECTION_BUY if sig.direction == "long" else DIRECTION_SELL
                order = place_order(figi, direction, QUANTITY, account_id)
                _log(
                    f"Ордер {order.get('orderId', '')[:8]}...  "
                    f"статус: {order.get('executionReportStatus', '?')}",
                    "cyan",
                )
                active_signal = sig
                traded_today  = today_str
            else:
                vwap_s = f"{vwap_val:.0f}" if vwap_val is not None else "?"
                _log(f"ORB {orb.low:.0f}–{orb.high:.0f}  цена={price:.0f}  vwap={vwap_s}  ждём сигнал")

            live.update(_build_display(
                now, orb, price, vwap_val, active_signal,
                traded_today == today_str, session_status,
            ))
            time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    try:
        run()
    except KeyboardInterrupt:
        console.print("\n[yellow]Робот остановлен (Ctrl+C)[/]")
