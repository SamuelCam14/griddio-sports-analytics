import asyncio
from sqlalchemy import text
from app.core.database import engine

async def migrate():
    print("Starting DDL migration for side-specific odds provenance...")
    async with engine.begin() as conn:
        ddl = """
        DO $$
        BEGIN
            -- OddsSnapshot side-specific provenance columns
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='odds_snapshots' AND column_name='home_bookmaker') THEN
                ALTER TABLE odds_snapshots ADD COLUMN home_bookmaker VARCHAR(50);
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='odds_snapshots' AND column_name='away_bookmaker') THEN
                ALTER TABLE odds_snapshots ADD COLUMN away_bookmaker VARCHAR(50);
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='odds_snapshots' AND column_name='over_bookmaker') THEN
                ALTER TABLE odds_snapshots ADD COLUMN over_bookmaker VARCHAR(50);
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='odds_snapshots' AND column_name='under_bookmaker') THEN
                ALTER TABLE odds_snapshots ADD COLUMN under_bookmaker VARCHAR(50);
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='odds_snapshots' AND column_name='benchmark_bookmaker') THEN
                ALTER TABLE odds_snapshots ADD COLUMN benchmark_bookmaker VARCHAR(50);
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='odds_snapshots' AND column_name='benchmark_observed_at') THEN
                ALTER TABLE odds_snapshots ADD COLUMN benchmark_observed_at TIMESTAMP WITH TIME ZONE;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='odds_snapshots' AND column_name='h2h_benchmark_bookmaker') THEN
                ALTER TABLE odds_snapshots ADD COLUMN h2h_benchmark_bookmaker VARCHAR(50);
            END IF;

            -- Backfill legacy records where side provenance is NULL
            UPDATE odds_snapshots SET home_bookmaker = bookmaker WHERE home_bookmaker IS NULL;
            UPDATE odds_snapshots SET away_bookmaker = bookmaker WHERE away_bookmaker IS NULL;
            UPDATE odds_snapshots SET over_bookmaker = bookmaker WHERE market = 'totals' AND over_bookmaker IS NULL;
            UPDATE odds_snapshots SET under_bookmaker = bookmaker WHERE market = 'totals' AND under_bookmaker IS NULL;
            UPDATE odds_snapshots SET benchmark_bookmaker = home_bookmaker WHERE market = 'h2h' AND benchmark_bookmaker IS NULL;
            UPDATE odds_snapshots SET h2h_benchmark_bookmaker = benchmark_bookmaker WHERE market = 'h2h' AND h2h_benchmark_bookmaker IS NULL;
            UPDATE odds_snapshots SET benchmark_observed_at = observed_at WHERE market = 'h2h' AND benchmark_observed_at IS NULL;
        END $$;
        """
        await conn.execute(text(ddl))
        print("Side provenance migration applied successfully.")

if __name__ == "__main__":
    asyncio.run(migrate())
