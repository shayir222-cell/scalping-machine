"""
Forensic replay: для каждой закрытой сделки из trades.json подтягиваем
1m свечи Binance Futures за время удержания и считаем:
  peak_R    — максимум favorable движения в R-эквиваленте
  min_R     — максимум adverse (просадка)
  hold_min  — приблизительное время удержания (первая свеча, где
              цена коснулась exit_price)
  fee_ratio — оценка fees / |pnl| (round-trip 0.07%)

R-нормализация выполняется через приближение SL_dist ≈ 0.5% entry.
Это даёт COMPARATIVE peak в R: точные значения могут отличаться на
±30% при реальном ATR-SL, но форма (% сделок с peak ≥ X) устойчива.

Запуск:
  python scripts/forensic.py [path/to/trades.json]
По умолчанию читает trades.json из домашней папки.
"""
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
import urllib.request

UTC = timezone.utc
DEFAULT_TRADES = Path.home() / "trades.json"
BINANCE_FAPI = "https://fapi.binance.com/fapi/v1/klines"
SL_PCT_APPROX = 0.005     # 0.5% — приближение SL-дистанции
FEE_RT = 0.0007           # round-trip: maker 0.02% + taker 0.05%
HOLD_CAP_MIN = 120        # окно replay (минут)


def parse_utc(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)


def fetch_klines(symbol: str, start_ms: int, end_ms: int, retries: int = 3):
    url = (
        f"{BINANCE_FAPI}?symbol={symbol}&interval=1m"
        f"&startTime={start_ms}&endTime={end_ms}&limit=500"
    )
    last_err = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=15) as r:
                return json.loads(r.read())
        except Exception as e:
            last_err = e
            time.sleep(1.5 ** attempt)
    raise RuntimeError(f"klines fetch failed: {last_err}")


def analyze_trade(t: dict) -> dict | None:
    opened = parse_utc(t["opened_at"])
    entry = float(t["entry_price"])
    exit_p = float(t["exit_price"])
    pnl = float(t["pnl_usdt"])
    side = t["side"]
    sym = t["symbol"]

    if entry <= 0 or exit_p <= 0:
        return None

    start_ms = int(opened.timestamp() * 1000)
    end_ms = start_ms + HOLD_CAP_MIN * 60 * 1000
    klines = fetch_klines(sym, start_ms, end_ms)
    if not klines:
        return None

    peak_pct = 0.0
    min_pct = 0.0
    close_idx = None
    tol = 0.0008

    for i, k in enumerate(klines):
        high = float(k[2])
        low = float(k[3])
        if side == "LONG":
            fav = (high - entry) / entry
            adv = (low - entry) / entry
        else:
            fav = (entry - low) / entry
            adv = (entry - high) / entry
        peak_pct = max(peak_pct, fav)
        min_pct = min(min_pct, adv)
        if close_idx is None and low * (1 - tol) <= exit_p <= high * (1 + tol):
            close_idx = i

    hold_min = (close_idx + 1) if close_idx is not None else len(klines)
    peak_R = peak_pct / SL_PCT_APPROX
    min_R = min_pct / SL_PCT_APPROX

    move = (exit_p - entry) * (1 if side == "LONG" else -1)
    qty_est = pnl / move if abs(move) > 1e-9 else 0.0
    notional_est = entry * abs(qty_est)
    fees_est = notional_est * FEE_RT
    fee_ratio = fees_est / max(abs(pnl), 0.01)

    return {
        "id": t["id"], "sym": sym, "side": side, "score": t["score"],
        "pnl": pnl, "exit_reason": t["exit_reason"],
        "peak_pct": peak_pct * 100, "min_pct": min_pct * 100,
        "peak_R": peak_R, "min_R": min_R,
        "hold_min": hold_min, "fee_ratio": fee_ratio,
        "notional_est": notional_est, "fees_est": fees_est,
    }


def median(xs: list) -> float:
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def pct(xs: list, threshold: float) -> float:
    if not xs:
        return 0.0
    return 100.0 * sum(1 for x in xs if x >= threshold) / len(xs)


def main():
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_TRADES
    trades = json.loads(path.read_text(encoding="utf-8"))
    closed = [t for t in trades if t["status"] == "closed"]
    print(f"Analyzing {len(closed)} closed trades from {path}\n")

    results = []
    for i, t in enumerate(closed, 1):
        print(f"  [{i:2d}/{len(closed)}] {t['symbol']:9s} {t['side']:5s} "
              f"{t['opened_at']}", end=" ... ", flush=True)
        try:
            r = analyze_trade(t)
            if r:
                results.append(r)
                print(f"peak={r['peak_R']:+5.2f}R  min={r['min_R']:+5.2f}R  "
                      f"hold={r['hold_min']:>3d}m  fee={r['fee_ratio']:.0%}  "
                      f"reason={r['exit_reason']}")
            else:
                print("no data")
        except Exception as e:
            print(f"ERR {e}")
        time.sleep(0.15)

    if not results:
        print("\nNo results.")
        return

    peaks = [r["peak_R"] for r in results]
    mins = [r["min_R"] for r in results]
    holds = [r["hold_min"] for r in results]

    print("\n" + "=" * 70)
    print("FORENSIC AGGREGATES")
    print("=" * 70)
    print(f"Analyzed trades: {len(results)}  (SL_dist approx = 0.5% entry)\n")

    print("PEAK EXCURSION (favorable move in R-equivalent):")
    print(f"  median peak:               {median(peaks):+.2f}R  "
          f"({median(peaks)*0.5:+.2f}% move)")
    print(f"  trades with peak >= 0.75R: {pct(peaks, 0.75):.0f}%")
    print(f"  trades with peak >= 1.00R: {pct(peaks, 1.00):.0f}%  <- reachable if TP1=1.0R")
    print(f"  trades with peak >= 1.50R: {pct(peaks, 1.50):.0f}%  <- reachable if TP1=1.5R (current bot)")
    print(f"  trades with peak >= 2.00R: {pct(peaks, 2.00):.0f}%")

    print("\nADVERSE EXCURSION (drawdown before exit):")
    print(f"  median min:                {median(mins):+.2f}R")
    print(f"  trades that touched -1.0R: {sum(1 for m in mins if m <= -1.0)}/{len(mins)}")
    print(f"  trades that touched -1.5R: {sum(1 for m in mins if m <= -1.5)}/{len(mins)}  (would have hit SL)")

    print("\nHOLD TIME:")
    print(f"  median hold: {median(holds):.0f} min")
    print(f"  < 5 min:   {sum(1 for h in holds if h < 5)}/{len(holds)} ({100*sum(1 for h in holds if h < 5)/len(holds):.0f}%)")
    print(f"  < 15 min:  {sum(1 for h in holds if h < 15)}/{len(holds)} ({100*sum(1 for h in holds if h < 15)/len(holds):.0f}%)")
    print(f"  >= 60 min: {sum(1 for h in holds if h >= 60)}/{len(holds)}")

    print("\nSIGNAL_CLOSE BREAKDOWN (the main exit reason):")
    sc = [r for r in results if r["exit_reason"] == "signal_close"]
    if sc:
        sc_peaks = [r["peak_R"] for r in sc]
        sc_wins = sum(1 for r in sc if r["pnl"] > 0)
        print(f"  count: {len(sc)} / {len(results)} ({100*len(sc)/len(results):.0f}% of all)")
        print(f"  win rate inside signal_close: {100*sc_wins/len(sc):.0f}%")
        print(f"  median peak inside signal_close: {median(sc_peaks):+.2f}R")
        print(f"  leaked >= 1.0R (TP1=1.0R would have caught): "
              f"{sum(1 for p in sc_peaks if p >= 1.0)}/{len(sc)} "
              f"({pct(sc_peaks, 1.0):.0f}%)")
        print(f"  leaked >= 1.5R (TP1=1.5R would have caught): "
              f"{sum(1 for p in sc_peaks if p >= 1.5)}/{len(sc)} "
              f"({pct(sc_peaks, 1.5):.0f}%)")

    print("\nFEE DRAG:")
    total_pnl = sum(r["pnl"] for r in results)
    abs_pnl = sum(abs(r["pnl"]) for r in results)
    fees_total = sum(r["fees_est"] for r in results)
    print(f"  total PnL:           {total_pnl:+7.3f} USDT")
    print(f"  est. total fees:     {fees_total:7.3f} USDT")
    print(f"  fee / |PnL| ratio:   {100*fees_total/max(abs_pnl,0.01):.0f}%")

    print("\nPER-SYMBOL (sorted by sample size):")
    syms: dict[str, list] = {}
    for r in results:
        syms.setdefault(r["sym"], []).append(r)
    print(f"  {'symbol':<10s} {'n':>3s} {'WR%':>5s} {'PnL':>9s} {'med_peak':>10s} {'med_min':>10s} {'med_hold':>9s}")
    for sym in sorted(syms, key=lambda s: -len(syms[s])):
        rs = syms[sym]
        wr = 100 * sum(1 for r in rs if r["pnl"] > 0) / len(rs)
        pnl = sum(r["pnl"] for r in rs)
        mp = median([r["peak_R"] for r in rs])
        mm = median([r["min_R"] for r in rs])
        mh = median([r["hold_min"] for r in rs])
        print(f"  {sym:<10s} {len(rs):>3d} {wr:>5.0f} {pnl:>+9.3f} "
              f"{mp:>+9.2f}R {mm:>+9.2f}R {mh:>8.0f}m")


if __name__ == "__main__":
    main()
