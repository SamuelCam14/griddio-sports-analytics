-- ============================================================
--  Sports Analytics Platform — DB Initialization Script
--  Se ejecuta automáticamente al crear el contenedor por primera vez
-- ============================================================

-- Habilitar extensión TimescaleDB
CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;

-- Habilitar UUID
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Crear hypertable para odds_snapshots (series temporales de momios)
-- Esta instrucción se ejecuta DESPUÉS de que SQLAlchemy crea las tablas
-- via el lifespan de FastAPI. Este script es solo para extensiones.

-- Nota: La conversión de odds_snapshots a hypertable se hace en la
-- migración de Alembic correspondiente.

COMMENT ON DATABASE sports_analytics IS 'Sports Analytics Platform — NBA/NFL EV Betting System';
