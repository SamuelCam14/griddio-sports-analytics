"""
Modelo SQLAlchemy para Activos Coleccionables (Sports Cards).
Permite modelar tarjetas NBA y contratos NIL universitarios (NCAA)
con seguimiento de precios, graduaciones (PSA/BGS), paralelos y valoración cuantitativa.
"""
from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import relationship

from app.core.database import Base


class SportsCard(Base):
    """Representa una tarjeta coleccionable (NBA o NIL NCAA)."""
    __tablename__ = "sports_cards"

    id = Column(Integer, primary_key=True, index=True)
    player_name = Column(String(100), nullable=False, index=True)
    sport = Column(String(20), default="NBA", nullable=False)
    season = Column(String(20), nullable=False)  # ej. "2023-24"
    
    # Equipo NBA (si aplica) o Universitario
    team_id = Column(Integer, ForeignKey("teams.id"), nullable=True)
    college_team = Column(String(100), nullable=True)  # ej. "Duke Blue Devils"
    
    # Metadatos de fabricación
    manufacturer = Column(String(50), nullable=False)  # "Panini", "Topps", "Bowman"
    brand = Column(String(50), nullable=False)         # "Prizm", "Bowman U", "Optic", "Select"
    card_number = Column(String(30), nullable=True)    # ej. "#136"
    card_type = Column(String(30), nullable=False)      # "NIL", "Rookie Card", "Veteran"
    
    # Atributos de rareza
    parallel_name = Column(String(80), default="Base") # "Base", "Silver", "Gold /10"
    print_run = Column(Integer, nullable=True)         # ej. 10, 25, 99, None para base
    has_autograph = Column(Boolean, default=False)
    autograph_type = Column(String(30), nullable=True) # "On-Card", "Sticker"
    grade = Column(String(20), default="Raw")          # "Raw", "PSA 9", "PSA 10"
    
    image_url = Column(Text, nullable=True)
    
    # Métricas de mercado y valoración
    current_market_price = Column(Float, nullable=False)  # Precio actual observado en eBay / marketplace
    fair_market_value = Column(Float, nullable=False)     # Valor justo intrínseco predicho por el modelo
    ev_opportunity = Column(Boolean, default=False)       # ¿Oportunidad de arbitraje (EV+)?
    ev_percentage = Column(Float, default=0.0)            # % de ganancia neta proyectada
    liquidity_score = Column(Float, default=5.0)          # 1 a 10 (frecuencia de ventas)

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), onupdate=lambda: datetime.now(timezone.utc))

    team = relationship("Team", foreign_keys=[team_id], lazy="joined")
