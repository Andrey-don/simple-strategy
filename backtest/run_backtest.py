"""
Бэктест ORB-стратегии на исторических M5-свечах Tinkoff API.

Запуск:
    python backtest/run_backtest.py

Результат: таблица сделок + итоговая статистика.
"""

import sys
import os
import csv
import time
import dataclasses
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta, date

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from data import find_instrument, get_candles
from strategy import opening_range, get_signal, Signal

import pandas as pd

# ---------------------------------------------------------------------------
# Параметры бэктеста
# ---------------------------------------------------------------------------

TICKER     = "SiM6"
DATE_FROM  = date(2026, 1, 9)    # начало периода (рабочий день)
DATE_TO    = date(2026, 4, 25)   # конец периода
QUANTITY   = 1                   # лотов (для P&L в пунктах)
PAUSE_SEC  = 0.5                 # пауза между запросами к API


# ---------------------------------------------------------------------------
# Симуляция сделки
# ---------------------------------------------------------------------------

@dataclass
class Trade:
    date:      str
    direction: str
    entry:     float
    stop:      float
    take:      float
    exit_price: float
    result:    str    # "win" / "loss" / "timeout"
    pnl:       float  # в пунктах


def simulate_trade(df: pd.DataFrame, sig: Signal, entry_idx: int) -> Trade:
    """
    После сигнала на свече entry_idx проверяем каждую следующую свечу:
    задела ли цена стоп или тейк.
    Если до конца дня — timeout (закрытие по последней цене).
    """
    entry_date = df.index[entry_idx].date().isoformat()
    after = df.iloc[entry_idx + 1:]

    for _, candle in after.iterrows():
        if sig.direction == "long":
            if candle["low"] <= sig.stop:
                pnl = sig.stop - sig.entry
                return Trade(entry_date, "LONG", sig.entry, sig.stop, sig.take,
                             sig.stop, "loss", pnl)
            if candle["high"] >= sig.take:
                pnl = sig.take - sig.entry
                return Trade(entry_date, "LONG", sig.entry, sig.stop, sig.take,
                             sig.take, "win", pnl)
        else:  # short
            if candle["high"] >= sig.stop:
                pnl = sig.entry - sig.stop
                return Trade(entry_date, "SHORT", sig.entry, sig.stop, sig.take,
                             sig.stop, "loss", pnl)
            if candle["low"] <= sig.take:
                pnl = sig.entry - sig.take
                return Trade(entry_date, "SHORT", sig.entry, sig.stop, sig.take,
                             sig.take, "win", pnl)

    # Не достигли ни стопа, ни тейка — закрываем по последней цене
    last_close = df["close"].iloc[-1]
    if sig.direction == "long":
        pnl = last_close - sig.entry
    else:
        pnl = sig.entry - last_close
    result = "win" if pnl > 0 else "loss"
    return Trade(entry_date, sig.direction.upper(), sig.entry, sig.stop, sig.take,
                 last_close, f"timeout({result})", pnl)


# ---------------------------------------------------------------------------
# Основной цикл
# ---------------------------------------------------------------------------

def trading_days(d_from: date, d_to: date):
    """Генератор рабочих дней (пн–пт)."""
    current = d_from
    while current <= d_to:
        if current.weekday() < 5:  # 0=пн … 4=пт
            yield current
        current += timedelta(days=1)


def run_backtest(ticker: str, d_from: date, d_to: date) -> list[Trade]:
    inst = find_instrument(ticker)
    if not inst:
        print(f"Инструмент {ticker} не найден")
        return []
    figi = inst["figi"]
    print(f"Инструмент: {inst['name']} | FIGI: {figi}")
    print(f"Период:     {d_from} → {d_to}\n")

    trades: list[Trade] = []

    for day in trading_days(d_from, d_to):
        from_dt = datetime(day.year, day.month, day.day, 7, 0, tzinfo=timezone.utc)
        to_dt   = from_dt + timedelta(hours=9)

        df = get_candles(figi, from_dt, to_dt, "5m")
        time.sleep(PAUSE_SEC)

        if df.empty or len(df) < 3:
            print(f"{day}  нет данных")
            continue

        orb = opening_range(df)
        if orb is None:
            print(f"{day}  ORB не сформирован")
            continue

        # Ищем первый сигнал дня
        signal_found = False
        for i in range(len(df)):
            sig = get_signal(df, candle_idx=i)
            if sig:
                trade = simulate_trade(df, sig, entry_idx=i)
                trades.append(trade)
                print(f"{day}  {trade.direction:5s}  "
                      f"entry={trade.entry:.0f}  stop={trade.stop:.0f}  take={trade.take:.0f}  "
                      f"exit={trade.exit_price:.0f}  pnl={trade.pnl:+.0f}  [{trade.result}]")
                signal_found = True
                break

        if not signal_found:
            print(f"{day}  нет сигнала  "
                  f"(ORB {orb.low:.0f}–{orb.high:.0f})")

    return trades


# ---------------------------------------------------------------------------
# Статистика
# ---------------------------------------------------------------------------

def print_stats(trades: list[Trade]) -> None:
    if not trades:
        print("\nСделок нет.")
        return

    total  = len(trades)
    wins   = [t for t in trades if "win" in t.result]
    losses = [t for t in trades if "loss" in t.result]
    pnl    = sum(t.pnl for t in trades)

    winrate    = len(wins) / total * 100
    avg_win    = sum(t.pnl for t in wins) / len(wins) if wins else 0
    avg_loss   = sum(t.pnl for t in losses) / len(losses) if losses else 0
    expectancy = pnl / total

    print("\n" + "=" * 60)
    print(f"  Итого сделок:      {total}")
    print(f"  Прибыльных:        {len(wins)}  ({winrate:.1f}%)")
    print(f"  Убыточных:         {len(losses)}")
    print(f"  Суммарный P&L:     {pnl:+.0f} пп")
    print(f"  Средний выигрыш:   {avg_win:+.0f} пп")
    print(f"  Средний проигрыш:  {avg_loss:+.0f} пп")
    print(f"  Матожидание:       {expectancy:+.2f} пп/сделку")
    print("=" * 60)


# ---------------------------------------------------------------------------
# Экспорт
# ---------------------------------------------------------------------------

def export_csv(trades: list[Trade], path: str = "backtest/results.csv") -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=[fi.name for fi in dataclasses.fields(Trade)])
        w.writeheader()
        for t in trades:
            w.writerow(dataclasses.asdict(t))
    print(f"CSV сохранён:    {path}")


def plot_equity(trades: list[Trade], path: str = "backtest/equity.png") -> None:
    if not trades:
        return

    equity = [0.0]
    for t in trades:
        equity.append(equity[-1] + t.pnl)

    peak = equity[0]
    drawdown = []
    for e in equity:
        peak = max(peak, e)
        drawdown.append(e - peak)

    colors = ["#2ecc71" if "win" in t.result else "#e74c3c" for t in trades]

    fig, (ax1, ax2) = plt.subplots(
        2, 1, figsize=(14, 8),
        gridspec_kw={"height_ratios": [3, 1]},
        facecolor="#1a1a2e",
    )
    for ax in (ax1, ax2):
        ax.set_facecolor("#16213e")
        for sp in ("top", "right"):
            ax.spines[sp].set_visible(False)
        ax.spines["left"].set_color("#444")
        ax.spines["bottom"].set_color("#444")
        ax.tick_params(colors="#aaa", labelsize=9)

    x = range(len(equity))
    ax1.plot(x, equity, color="#00d4ff", linewidth=2, zorder=3)
    ax1.fill_between(x, equity, 0,
                     where=[e >= 0 for e in equity], alpha=0.2, color="#2ecc71")
    ax1.fill_between(x, equity, 0,
                     where=[e < 0 for e in equity], alpha=0.2, color="#e74c3c")
    ax1.fill_between(x, drawdown, 0, alpha=0.25, color="#e74c3c", label="Просадка")
    ax1.axhline(0, color="#555", linewidth=0.8, linestyle="--")
    wins = sum("win" in t.result for t in trades)
    ax1.set_title(
        f"ORB Strategy  |  {len(trades)} сделок  |  "
        f"Winrate {wins/len(trades)*100:.1f}%  |  P&L {equity[-1]:+.0f} пп",
        color="white", pad=10, fontsize=11,
    )
    ax1.set_ylabel("P&L, пп", color="#aaa")
    ax1.grid(True, alpha=0.15, color="#444")
    ax1.legend(facecolor="#16213e", labelcolor="#aaa", fontsize=9)

    ax2.bar(range(len(trades)), [t.pnl for t in trades],
            color=colors, width=0.8, alpha=0.85)
    ax2.axhline(0, color="#555", linewidth=0.8)
    ax2.set_ylabel("Сделка, пп", color="#aaa", fontsize=9)
    ax2.set_xlabel("Сделка №", color="#aaa")
    ax2.grid(True, alpha=0.15, color="#444", axis="y")

    plt.tight_layout(pad=1.5)
    plt.savefig(path, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close()
    print(f"График сохранён: {path}")


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    trades = run_backtest(TICKER, DATE_FROM, DATE_TO)
    print_stats(trades)
    if trades:
        export_csv(trades)
        plot_equity(trades)
