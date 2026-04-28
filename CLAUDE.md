# Simple Strategy — ORB Trading Robot

## Цель проекта

Торговый робот на Python для MOEX, стратегия ORB (Opening Range Breakout), брокер Tinkoff Invest API.

## Стратегия ORB — ключевые параметры

- **Диапазон:** первые 2 свечи M5 после открытия сессии MOEX (10:00 МСК = 07:00 UTC)
- **Вход:** пробой верхней/нижней границы диапазона + фильтр VWAP
- **Стоп:** за противоположную границу диапазона
- **Тейк:** 1:2 к стопу
- **Лимит:** одна сделка в день на инструмент
- **Статистика (бэктест 1999–2025):** winrate ~38%, матожидание +0.14 на рубль риска

## Технический стек

- Python 3.12, venv в `.venv/`
- REST API Tinkoff Invest (не gRPC SDK — недоступен на PyPI)
- pandas, requests, python-dotenv, matplotlib, rich
- Секреты в `.env` (не коммитить): `TINKOFF_TOKEN`, `TINKOFF_SANDBOX=true`

## Структура проекта

```
simple-strategy/
├── src/
│   ├── data.py         # свечи M5 из Tinkoff REST API
│   ├── strategy.py     # ORB диапазон, VWAP фильтр, сигналы
│   ├── broker.py       # ордера sandbox/prod
│   └── robot.py        # боевой цикл с rich live-дашбордом
├── backtest/
│   ├── run_backtest.py # бэктест + CSV + equity.png
│   ├── results.csv     # результаты последнего прогона
│   └── equity.png      # график equity curve
├── docs/
│   ├── backtest_analysis.md  # анализ результатов и найденные баги
│   └── Торговая система.md   # исследовательский конспект
├── requirements.txt
└── .env                # токен (не в git)
```

## Текущее состояние (апрель 2026)

Все модули написаны и работают в sandbox-режиме.

### Результаты бэктеста (SiM6, 2026-01-09 → 2026-04-25)

| Метрика | Значение |
|---|---|
| Сделок | 75 |
| Winrate | 38.7% |
| Суммарный P&L | +869 пп |
| Средний выигрыш | +462 пп |
| Средний проигрыш | −272 пп |
| Матожидание | +11.59 пп/сделку |

## ЗАДАЧА — три исправления бэктеста

Подробный анализ: `docs/backtest_analysis.md`

### Исправление 1 — Фильтр по размеру ORB (приоритет 1)

**Файл:** `src/strategy.py`, функция `get_signal()`

8 сделок с ORB > 450 пп дают −689 пп убытка. Без них P&L = +1558 пп.

Добавить константу `MAX_ORB = 400` и в начало `get_signal()`:
```python
if orb.size > MAX_ORB:
    return None
```

### Исправление 2 — Реалистичная цена входа

**Файл:** `src/strategy.py`, функция `get_signal()`

Сейчас: `entry = orb.high` — но сигнал срабатывает когда цена уже ушла за ORB.  
Надо: `entry = close` (цена закрытия сигнальной свечи).

```python
# Лонг
if close > orb.high and close > current_vwap:
    entry = close          # было: entry = orb.high
    stop  = orb.low
    return Signal("long", entry, stop, entry + RR * (entry - stop))

# Шорт
if close < orb.low and close < current_vwap:
    entry = close          # было: entry = orb.low
    stop  = orb.high
    return Signal("short", entry, stop, entry - RR * (stop - entry))
```

### Исправление 3 — Порядок стоп/тейк в симуляции

**Файл:** `backtest/run_backtest.py`, функция `simulate_trade()`

Если в одной свече задеты оба уровня — код всегда засчитывал стоп.  
Надо проверять какой уровень ближе к `candle["open"]`:

```python
if hit_stop and hit_take:
    if abs(candle["open"] - sig.stop) <= abs(candle["open"] - sig.take):
        hit_take = False
    else:
        hit_stop = False
```

## После исправлений

```bash
python backtest/run_backtest.py
```

Ожидаемый результат: P&L ~+1500 пп (было +869), более честная оценка стратегии.  
Затем: `git add -A && git commit -m "fix: ORB filter, realistic entry, stop/take order"`

## Важно

- Секреты в `.env`, не коммитить
- После каждого рабочего блока: `git commit` + `git push origin main`
- Запуск робота: `python src/robot.py` (rich live-дашборд)
- Запуск бэктеста: `python backtest/run_backtest.py`
