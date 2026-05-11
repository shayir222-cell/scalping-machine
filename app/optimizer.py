"""
AI Optimizer: analyze strategy performance every 72h and generate recommendations.
"""
import asyncio
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from loguru import logger
from sqlalchemy import select, func
from .database import SessionLocal, Trade as DbTrade


UTC = timezone.utc


@dataclass
class OptimizationReport:
    """Analysis results from last period."""
    timestamp: datetime
    total_trades: int
    win_rate: float
    expectancy: float
    best_pair: str
    worst_pair: str
    avg_leverage: float
    best_setup_score: int
    worst_setup_score: int
    score_accuracy: float  # How well score predicted wins
    leverage_efficiency: float  # Profit per leverage unit
    recommendations: list[str]


class AIOptimizer:
    """Analyze trades and generate recommendations."""
    
    def __init__(self):
        self.last_analysis: datetime | None = None
        self.analysis_interval = timedelta(hours=72)
    
    async def should_analyze(self) -> bool:
        """Check if 72 hours have passed since last analysis."""
        if self.last_analysis is None:
            return True
        elapsed = datetime.now(UTC) - self.last_analysis
        return elapsed >= self.analysis_interval
    
    async def analyze_performance(self) -> OptimizationReport | None:
        """Comprehensive analysis of recent trades."""
        if not await self.should_analyze():
            return None
        
        async with SessionLocal() as db:
            # Get recent trades
            cutoff = datetime.now(UTC) - timedelta(hours=72)
            stmt = select(DbTrade).where(DbTrade.closed_at >= cutoff)
            result = await db.execute(stmt)
            trades = result.scalars().all()
            
            if not trades:
                logger.info("No trades in last 72h for analysis")
                return None
            
            # Calculate metrics
            wins = sum(1 for t in trades if t.pnl_usdt and t.pnl_usdt > 0)
            total = len(trades)
            win_rate = (wins / total) * 100 if total > 0 else 0
            total_pnl = sum(t.pnl_usdt or 0 for t in trades)
            expectancy = total_pnl / total if total > 0 else 0
            avg_leverage = sum(t.leverage or 0 for t in trades) / total if total > 0 else 1
            
            # Pair analysis
            pair_pnl: dict[str, float] = {}
            for t in trades:
                if t.symbol not in pair_pnl:
                    pair_pnl[t.symbol] = 0
                pair_pnl[t.symbol] += t.pnl_usdt or 0
            
            best_pair = max(pair_pnl, key=pair_pnl.get) if pair_pnl else "—"
            worst_pair = min(pair_pnl, key=pair_pnl.get) if pair_pnl else "—"
            
            # Score analysis
            scores = [t.score for t in trades if t.score]
            best_score = max(scores) if scores else 0
            worst_score = min(scores) if scores else 0
            
            # Score accuracy: correlation between score and win
            score_wins = sum(1 for t in trades if t.score >= 85 and t.pnl_usdt and t.pnl_usdt > 0)
            score_accuracy = (score_wins / sum(1 for t in trades if t.score >= 85)) * 100 if sum(1 for t in trades if t.score >= 85) > 0 else 0
            
            # Leverage efficiency
            leverage_efficiency = total_pnl / (avg_leverage * total) if avg_leverage > 0 and total > 0 else 0
            
            # Generate recommendations
            recommendations = self._generate_recommendations(
                win_rate, expectancy, best_pair, worst_pair, avg_leverage, score_accuracy
            )
            
            self.last_analysis = datetime.now(UTC)
            
            return OptimizationReport(
                timestamp=datetime.now(UTC),
                total_trades=total,
                win_rate=win_rate,
                expectancy=expectancy,
                best_pair=best_pair,
                worst_pair=worst_pair,
                avg_leverage=avg_leverage,
                best_setup_score=best_score,
                worst_setup_score=worst_score,
                score_accuracy=score_accuracy,
                leverage_efficiency=leverage_efficiency,
                recommendations=recommendations,
            )
    
    def _generate_recommendations(
        self,
        win_rate: float,
        expectancy: float,
        best_pair: str,
        worst_pair: str,
        avg_leverage: float,
        score_accuracy: float,
    ) -> list[str]:
        """Generate actionable recommendations based on metrics."""
        rec = []
        
        # Win rate analysis
        if win_rate < 45:
            rec.append(f"⚠️ Win rate {win_rate:.1f}% — consider stricter score threshold (+5 points)")
        elif win_rate > 65:
            rec.append(f"✅ Win rate {win_rate:.1f}% — can afford slight leverage increase")
        
        # Expectancy analysis
        if expectancy < 0:
            rec.append(f"❌ Negative expectancy ${expectancy:.2f} — review entry logic")
        elif expectancy > 5:
            rec.append(f"💡 Strong expectancy ${expectancy:.2f}/trade — consider AGGRESSIVE mode")
        
        # Pair concentration
        if best_pair != "—":
            rec.append(f"📈 Best performer: {best_pair} — allocate more trades here")
        if worst_pair != "—":
            rec.append(f"📉 Worst performer: {worst_pair} — reduce frequency or avoid")
        
        # Leverage optimization
        if avg_leverage > 8 and win_rate < 50:
            rec.append("⚠️ High leverage + low win rate — reduce leverage by 25%")
        elif avg_leverage < 4 and win_rate > 55 and expectancy > 2:
            rec.append("💪 Low leverage + good win rate — increase leverage by 25%")
        
        # Score accuracy
        if score_accuracy < 50:
            rec.append(f"🎯 Score accuracy {score_accuracy:.1f}% — recalibrate scoring weights")
        elif score_accuracy > 70:
            rec.append(f"🎯 Score accuracy {score_accuracy:.1f}% — current scoring is effective")
        
        # Default if no specific issues
        if not rec:
            rec.append("📊 System performing as expected — continue monitoring")
        
        return rec
    
    def format_report(self, report: OptimizationReport) -> str:
        """Format report for Telegram/logging."""
        lines = [
            f"<b>🤖 AI Optimizer Report (72h)</b>",
            f"━━━━━━━━━━━━━━━━━━━━━━━━",
            f"<b>Overview:</b>",
            f"  Trades: {report.total_trades}",
            f"  Win Rate: {report.win_rate:.1f}%",
            f"  Expectancy: ${report.expectancy:.2f}/trade",
            f"  Avg Leverage: x{report.avg_leverage:.1f}",
            f"<b>Pair Analysis:</b>",
            f"  🏆 Best: {report.best_pair}",
            f"  📉 Worst: {report.worst_pair}",
            f"<b>Quality Metrics:</b>",
            f"  Score Accuracy: {report.score_accuracy:.1f}%",
            f"  Leverage Efficiency: {report.leverage_efficiency:.2f}",
            f"<b>🎯 Recommendations:</b>",
        ]
        lines.extend([f"  • {r}" for r in report.recommendations])
        return "\n".join(lines)


# Singleton instance
_instance: AIOptimizer | None = None


def get_optimizer() -> AIOptimizer:
    global _instance
    if _instance is None:
        _instance = AIOptimizer()
    return _instance
