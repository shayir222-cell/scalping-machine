"""
Pair ranking: score pairs by recent performance metrics for intelligent trading prioritization.
"""
from datetime import datetime, timedelta, timezone
from collections import defaultdict
from dataclasses import dataclass, field
from loguru import logger


UTC = timezone.utc


@dataclass
class PairStats:
    """Track performance metrics for a trading pair."""
    symbol: str
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    total_pnl: float = 0.0
    last_updated: datetime = field(default_factory=lambda: datetime.now(UTC))
    
    @property
    def win_rate(self) -> float:
        if self.total_trades == 0:
            return 0.0
        return (self.wins / self.total_trades) * 100
    
    @property
    def avg_pnl_per_trade(self) -> float:
        if self.total_trades == 0:
            return 0.0
        return self.total_pnl / self.total_trades
    
    @property
    def expectancy(self) -> float:
        """Expected profit per trade (simplified)."""
        if self.total_trades == 0:
            return 0.0
        return self.total_pnl / self.total_trades
    
    @property
    def score(self) -> float:
        """Ranking score (0-100): prefer high win_rate + positive expectancy."""
        if self.total_trades < 3:
            return 0.0  # Need minimum sample size
        
        wr = self.win_rate / 100  # normalize to 0-1
        exp_factor = min(1.0, max(-1.0, self.expectancy / 50))  # clamp to -1 to 1
        
        # 70% win_rate + 30% expectancy
        return (wr * 70) + (max(0, exp_factor * 100) * 0.3)


class PairRanking:
    """Maintain and rank trading pair performance."""
    
    def __init__(self):
        self.stats: dict[str, PairStats] = {}
        self.lookback_days = 7
    
    def record_trade_result(self, symbol: str, is_win: bool, pnl: float) -> None:
        """Record trade outcome for pair."""
        if symbol not in self.stats:
            self.stats[symbol] = PairStats(symbol=symbol)
        
        s = self.stats[symbol]
        s.total_trades += 1
        if is_win:
            s.wins += 1
        else:
            s.losses += 1
        s.total_pnl += pnl
        s.last_updated = datetime.now(UTC)
        
        logger.debug(
            f"{symbol}: {s.wins}W/{s.losses}L (WR={s.win_rate:.1f}%) | "
            f"PnL=${s.total_pnl:.2f} | Expectancy=${s.expectancy:.2f}"
        )
    
    def get_ranked_pairs(self) -> list[tuple[str, float]]:
        """Return list of (symbol, score) sorted by score descending."""
        pairs = []
        for sym, stats in self.stats.items():
            if stats.total_trades >= 3:  # Minimum sample
                pairs.append((sym, stats.score))
        
        return sorted(pairs, key=lambda x: x[1], reverse=True)
    
    def get_pair_stats(self, symbol: str) -> dict:
        """Get detailed stats for a pair."""
        if symbol not in self.stats:
            return {}
        
        s = self.stats[symbol]
        return {
            "symbol": s.symbol,
            "total_trades": s.total_trades,
            "wins": s.wins,
            "losses": s.losses,
            "win_rate": f"{s.win_rate:.1f}%",
            "total_pnl": f"${s.total_pnl:.2f}",
            "avg_pnl": f"${s.avg_pnl_per_trade:.2f}",
            "expectancy": f"${s.expectancy:.2f}",
            "score": f"{s.score:.1f}",
            "last_updated": s.last_updated.isoformat(),
        }
    
    def get_top_pairs(self, top_n: int = 3) -> list[str]:
        """Get top N pairs by performance."""
        ranked = self.get_ranked_pairs()
        return [sym for sym, _ in ranked[:top_n]]
    
    def get_weak_pairs(self, threshold: float = 40.0) -> list[str]:
        """Get pairs with score below threshold (avoid trading)."""
        weak = []
        for sym, stats in self.stats.items():
            if stats.total_trades >= 3 and stats.score < threshold:
                weak.append(sym)
        return weak
    
    def priority_for_symbol(self, symbol: str) -> str:
        """Return trading priority: HIGH / NORMAL / LOW / AVOID."""
        if symbol not in self.stats:
            return "NORMAL"
        
        stats = self.stats[symbol]
        if stats.total_trades < 3:
            return "NORMAL"
        
        score = stats.score
        if score >= 70:
            return "HIGH"
        elif score >= 50:
            return "NORMAL"
        elif score >= 30:
            return "LOW"
        else:
            return "AVOID"


# Singleton instance
_instance: PairRanking | None = None


def get_pair_ranking() -> PairRanking:
    global _instance
    if _instance is None:
        _instance = PairRanking()
    return _instance
