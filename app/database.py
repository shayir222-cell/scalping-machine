import os
from datetime import datetime, date as date_type
from typing import Optional, AsyncGenerator

from sqlalchemy import (
    String, Integer, Numeric, Boolean, Text,
    DateTime, Date, JSON, BigInteger,
)
from sqlalchemy.ext.asyncio import (
    create_async_engine, AsyncSession, async_sessionmaker,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.sql import func

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "sqlite+aiosqlite:///./scalping.db",
)

_is_sqlite = DATABASE_URL.startswith("sqlite")
engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    **({} if _is_sqlite else {"pool_size": 5, "max_overflow": 10}),
)
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


_BigPK = BigInteger().with_variant(Integer, "sqlite")


class Signal(Base):
    __tablename__ = "signals"
    id: Mapped[int] = mapped_column(_BigPK, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(20))
    action: Mapped[str] = mapped_column(String(20))
    price: Mapped[Optional[float]] = mapped_column(Numeric(20, 8))
    score: Mapped[Optional[int]] = mapped_column(Integer)
    atr: Mapped[Optional[float]] = mapped_column(Numeric(20, 8))
    tf_alignment: Mapped[Optional[int]] = mapped_column(Integer, default=0)
    raw_payload: Mapped[Optional[dict]] = mapped_column(JSON)
    processed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Trade(Base):
    __tablename__ = "trades"
    id: Mapped[int] = mapped_column(_BigPK, primary_key=True, autoincrement=True)
    signal_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    symbol: Mapped[str] = mapped_column(String(20))
    side: Mapped[str] = mapped_column(String(5))   # LONG | SHORT
    score: Mapped[Optional[int]] = mapped_column(Integer)
    leverage: Mapped[Optional[int]] = mapped_column(Integer)
    risk_pct: Mapped[Optional[float]] = mapped_column(Numeric(5, 3))
    entry_price: Mapped[Optional[float]] = mapped_column(Numeric(20, 8))
    quantity: Mapped[Optional[float]] = mapped_column(Numeric(20, 8))
    sl_price: Mapped[Optional[float]] = mapped_column(Numeric(20, 8))
    tp1_price: Mapped[Optional[float]] = mapped_column(Numeric(20, 8))
    tp2_price: Mapped[Optional[float]] = mapped_column(Numeric(20, 8))
    tp3_price: Mapped[Optional[float]] = mapped_column(Numeric(20, 8))
    exit_price: Mapped[Optional[float]] = mapped_column(Numeric(20, 8))
    pnl_usdt: Mapped[Optional[float]] = mapped_column(Numeric(20, 4))
    pnl_pct: Mapped[Optional[float]] = mapped_column(Numeric(10, 4))
    fees_usdt: Mapped[Optional[float]] = mapped_column(Numeric(20, 4))
    exit_reason: Mapped[Optional[str]] = mapped_column(String(50))
    hold_time_sec: Mapped[Optional[int]] = mapped_column(Integer)
    session: Mapped[Optional[str]] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default="open")
    tp1_filled: Mapped[bool] = mapped_column(Boolean, default=False)
    tp2_filled: Mapped[bool] = mapped_column(Boolean, default=False)
    binance_order_id: Mapped[Optional[str]] = mapped_column(String(50))
    opened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class DailyStat(Base):
    __tablename__ = "daily_stats"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date: Mapped[date_type] = mapped_column(Date, unique=True)
    equity_start: Mapped[Optional[float]] = mapped_column(Numeric(20, 4))
    equity_end: Mapped[Optional[float]] = mapped_column(Numeric(20, 4))
    pnl_usdt: Mapped[Optional[float]] = mapped_column(Numeric(20, 4))
    pnl_pct: Mapped[Optional[float]] = mapped_column(Numeric(10, 4))
    max_drawdown_pct: Mapped[Optional[float]] = mapped_column(Numeric(10, 4))
    total_trades: Mapped[int] = mapped_column(Integer, default=0)
    wins: Mapped[int] = mapped_column(Integer, default=0)
    losses: Mapped[int] = mapped_column(Integer, default=0)
    best_pair: Mapped[Optional[str]] = mapped_column(String(20))
    worst_pair: Mapped[Optional[str]] = mapped_column(String(20))
    notes: Mapped[Optional[str]] = mapped_column(Text)


class EvolutionReport(Base):
    __tablename__ = "evolution_reports"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cycle_start: Mapped[Optional[date_type]] = mapped_column(Date)
    cycle_end: Mapped[Optional[date_type]] = mapped_column(Date)
    metrics: Mapped[Optional[dict]] = mapped_column(JSON)
    recommendations: Mapped[Optional[list]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        yield session
