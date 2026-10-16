"""
Modelos SQLAlchemy para la base de datos.

Tablas:
  - teams         : Catálogo de equipos NBA/NFL
  - games         : Partidos históricos y futuros
  - odds_snapshots: Historial de momios (TimescaleDB hypertable)
  - predictions   : Predicciones del modelo con probabilidades
  - bankroll      : Registro de apuestas y gestión de bankroll
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean, Column, DateTime, Float, ForeignKey,
    Integer, String, Text, UniqueConstraint, func,
)
from sqlalchemy.orm import relationship

from app.core.database import Base


class Team(Base):
    __tablename__ = "teams"

    id          = Column(Integer, primary_key=True)
    api_id      = Column(String(20), unique=True, nullable=False, index=True)
    sport       = Column(String(10), nullable=False, default="NBA")  # NBA | NFL
    name        = Column(String(100), nullable=False)
    abbreviation= Column(String(10), nullable=False)
    city        = Column(String(100))
    conference  = Column(String(10))   # East | West | AFC | NFC
    division    = Column(String(30))
    market_size_score = Column(Float, default=5.0)  # 1-10 (para Sports Cards)
    logo_url    = Column(String(255))
    created_at  = Column(DateTime(timezone=True), server_default=func.now())

    home_games  = relationship("Game", foreign_keys="Game.home_team_id", back_populates="home_team")
    away_games  = relationship("Game", foreign_keys="Game.away_team_id", back_populates="away_team")


class Game(Base):
    __tablename__ = "games"

    id              = Column(Integer, primary_key=True)
    external_id     = Column(String(50), unique=True, nullable=False, index=True)
    sport           = Column(String(10), nullable=False, default="NBA")
    season          = Column(String(10), nullable=False)  # "2025-26"
    season_type     = Column(String(20), default="Regular Season")
    game_date       = Column(DateTime(timezone=True), nullable=False, index=True)
    home_team_id    = Column(Integer, ForeignKey("teams.id"), nullable=False)
    away_team_id    = Column(Integer, ForeignKey("teams.id"), nullable=False)

    # Resultado
    home_score      = Column(Integer)
    away_score      = Column(Integer)
    home_win        = Column(Boolean)
    status          = Column(String(20), default="scheduled")  # scheduled|live|final

    # Trazabilidad canónica (P2)
    source          = Column(String(50), nullable=True)        # ej: "ESPN via SportsDataverse"
    source_ref      = Column(String(255), nullable=True)       # ej: SHA256 del dataset o manifest
    fetched_at      = Column(DateTime(timezone=True), nullable=True) # Cuándo se descargó
    observed_at     = Column(DateTime(timezone=True), nullable=True) # Cuándo se consolidó el partido (as_of)
    missing_reason  = Column(String(255), nullable=True)       # Razón si no se puede usar (ej: "No history", "TBD teams")

    # Stats de equipo en el momento del partido (snapshot legacy)
    home_wpct       = Column(Float)
    away_wpct       = Column(Float)
    home_net_rtg    = Column(Float)
    away_net_rtg    = Column(Float)
    home_off_rtg    = Column(Float)
    away_off_rtg    = Column(Float)
    home_def_rtg    = Column(Float)
    away_def_rtg    = Column(Float)
    home_pace       = Column(Float)
    away_pace       = Column(Float)
    home_l10_wpct   = Column(Float)
    away_l10_wpct   = Column(Float)
    home_loc_wpct   = Column(Float)    # Home W% at home
    away_loc_wpct   = Column(Float)    # Away W% on road
    home_days_rest  = Column(Integer)
    away_days_rest  = Column(Integer)
    back_to_back_home = Column(Boolean, default=False)
    back_to_back_away = Column(Boolean, default=False)

    created_at      = Column(DateTime(timezone=True), server_default=func.now())
    updated_at      = Column(DateTime(timezone=True), onupdate=func.now())

    home_team       = relationship("Team", foreign_keys=[home_team_id], back_populates="home_games")
    away_team       = relationship("Team", foreign_keys=[away_team_id], back_populates="away_games")
    predictions     = relationship("Prediction", back_populates="game")
    odds_snapshots  = relationship("OddsSnapshot", back_populates="game")
    crosswalk_entries = relationship("ProviderGameCrosswalk", back_populates="game", cascade="all, delete-orphan")


class ProviderGameCrosswalk(Base):
    """
    Tabla de correspondencias entre identificadores de proveedores externos
    (ej: 'the_odds_api', 'nflverse', 'espn') y el Game canónico interno.
    Garantiza una identidad unificada evitando registros duplicados.
    """
    __tablename__ = "provider_game_crosswalk"

    id                  = Column(Integer, primary_key=True)
    game_id             = Column(Integer, ForeignKey("games.id", ondelete="CASCADE"), nullable=False, index=True)
    provider            = Column(String(50), nullable=False)   # "the_odds_api" | "nflverse" | "espn"
    provider_event_id   = Column(String(100), nullable=False)  # ID externo en el proveedor
    created_at          = Column(DateTime(timezone=True), server_default=func.now())
    updated_at          = Column(DateTime(timezone=True), onupdate=func.now())

    game                = relationship("Game", back_populates="crosswalk_entries")

    __table_args__ = (
        UniqueConstraint("provider", "provider_event_id", name="uq_provider_event"),
    )


class OddsSnapshot(Base):
    """
    Historial de momios en el tiempo — convertida a hypertable TimescaleDB.
    Permite ver movimientos de línea y detectar sharp money.
    """
    __tablename__ = "odds_snapshots"

    id              = Column(Integer, primary_key=True)
    game_id         = Column(Integer, ForeignKey("games.id"), nullable=False, index=True)
    bookmaker       = Column(String(50), nullable=False)  # "draftkings", "fanduel"...
    market          = Column(String(20), nullable=False)  # "h2h" | "spreads" | "totals"
    captured_at     = Column(DateTime(timezone=True), nullable=False)
    observed_at     = Column(DateTime(timezone=True), nullable=False, index=True)

    # Moneyline
    home_ml         = Column(Integer)   # Momio americano (ej: -130)
    away_ml         = Column(Integer)

    # Spread
    home_spread     = Column(Float)     # ej: -3.5
    home_spread_ml  = Column(Integer)   # ej: -110
    away_spread     = Column(Float)
    away_spread_ml  = Column(Integer)

    # Totals (Over/Under)
    total_line      = Column(Float)     # ej: 220.5
    over_ml         = Column(Integer)
    under_ml        = Column(Integer)

    # Probabilidades implícitas calculadas
    home_implied_prob = Column(Float)
    away_implied_prob = Column(Float)

    game            = relationship("Game", back_populates="odds_snapshots")

    __table_args__ = (
        UniqueConstraint("game_id", "bookmaker", "market", "observed_at",
                         name="uq_odds_snapshot"),
    )


class Prediction(Base):
    """
    Predicciones del modelo ML por partido.
    Incluye EV calculado y recomendación de Kelly.
    """
    __tablename__ = "predictions"

    id                  = Column(Integer, primary_key=True)
    game_id             = Column(Integer, ForeignKey("games.id"), nullable=False, index=True)
    model_version       = Column(String(50), nullable=False)
    predicted_at        = Column(DateTime(timezone=True), server_default=func.now())

    # Trazabilidad canónica
    dataset_sha256      = Column(String(255), nullable=False) # Hash del set de entrenamiento
    artifact_sha256     = Column(String(255), nullable=False) # Hash de los pesos del modelo
    inference_input_sha256 = Column(String(255), nullable=False) # Hash del snapshot de features/odds real al momento de inferir
    as_of               = Column(DateTime(timezone=True), nullable=True)

    # Probabilidades del modelo
    home_win_prob       = Column(Float, nullable=False)
    away_win_prob       = Column(Float, nullable=False)
    confluence_tier     = Column(String(10))   # "PREMIUM" | "VALUE" | "SKIP"
    model_agreement     = Column(Integer)      # Cuántos de 3 modelos coinciden

    # EV calculado contra el mejor momio disponible
    best_bookmaker      = Column(String(50))
    best_home_ml        = Column(Integer)
    best_away_ml        = Column(Integer)
    home_ev             = Column(Float)        # Expected Value en %
    away_ev             = Column(Float)
    home_kelly          = Column(Float)        # Fracción Kelly recomendada
    away_kelly          = Column(Float)
    home_has_edge       = Column(Boolean, default=False)   # EV+ y ventaja >3%
    away_has_edge       = Column(Boolean, default=False)

    # Resultado post-partido (para backtesting)
    outcome_correct     = Column(Boolean)      # ¿Acertó el pick principal?

    game                = relationship("Game", back_populates="predictions")

    __table_args__ = (
        UniqueConstraint("game_id", "model_version", "artifact_sha256", "inference_input_sha256", name="uq_prediction_run"),
    )


class BankrollEntry(Base):
    """
    Registro de apuestas realizadas y gestión de bankroll.
    """
    __tablename__ = "bankroll_entries"

    id              = Column(Integer, primary_key=True)
    game_id         = Column(Integer, ForeignKey("games.id"), nullable=True)
    bet_type        = Column(String(20))  # "single" | "parlay"
    description     = Column(Text)       # "LAL ML -130 + GSW -105 (parlay)"
    stake           = Column(Float, nullable=False)
    odds_ml         = Column(Integer)
    expected_payout = Column(Float)
    ev_pct          = Column(Float)
    kelly_fraction  = Column(Float)
    bankroll_before = Column(Float)
    bankroll_after  = Column(Float)
    result          = Column(String(10))  # "win" | "loss" | "push" | "pending"
    profit_loss     = Column(Float)
    placed_at       = Column(DateTime(timezone=True), server_default=func.now())
    settled_at      = Column(DateTime(timezone=True))
    notes           = Column(Text)
