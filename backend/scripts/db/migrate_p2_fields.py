import asyncio
from sqlalchemy import text
from app.core.database import engine

async def migrate():
    print("Starting DDL migration for P2...")
    async with engine.begin() as conn:
        ddl = """
        DO $$
        DECLARE
            c_oid oid;
            c_def text;
        BEGIN
            -- Game fields
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='games' AND column_name='source') THEN
                ALTER TABLE games ADD COLUMN source VARCHAR(50);
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='games' AND column_name='source_ref') THEN
                ALTER TABLE games ADD COLUMN IF NOT EXISTS source VARCHAR(50);\n            ALTER TABLE games ADD COLUMN source_ref VARCHAR(255);
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='games' AND column_name='fetched_at') THEN
                ALTER TABLE games ADD COLUMN fetched_at TIMESTAMP WITH TIME ZONE;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='games' AND column_name='observed_at') THEN
                ALTER TABLE games ADD COLUMN observed_at TIMESTAMP WITH TIME ZONE;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='games' AND column_name='missing_reason') THEN
                ALTER TABLE games ADD COLUMN missing_reason VARCHAR(255);
            END IF;

            -- OddsSnapshot fields
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='odds_snapshots' AND column_name='observed_at') THEN
                ALTER TABLE odds_snapshots ADD COLUMN observed_at TIMESTAMP WITH TIME ZONE;
            END IF;

            -- Prediction fields
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='predictions' AND column_name='dataset_sha256') THEN
                ALTER TABLE predictions ADD COLUMN dataset_sha256 VARCHAR(255);
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='predictions' AND column_name='artifact_sha256') THEN
                ALTER TABLE predictions ADD COLUMN artifact_sha256 VARCHAR(255);
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='predictions' AND column_name='inference_input_sha256') THEN
                ALTER TABLE predictions ADD COLUMN inference_input_sha256 VARCHAR(255);
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='predictions' AND column_name='as_of') THEN
                ALTER TABLE predictions ADD COLUMN as_of TIMESTAMP WITH TIME ZONE;
            END IF;

            -- Step 1: Create an archive table for legacy predictions explicitly specifying columns
            CREATE TABLE IF NOT EXISTS predictions_legacy_p2_migration AS 
            SELECT id, game_id, model_version, predicted_at, dataset_sha256, artifact_sha256, inference_input_sha256, as_of, home_win_prob, away_win_prob, confluence_tier, model_agreement, best_bookmaker, best_home_ml, best_away_ml, home_ev, away_ev, home_kelly, away_kelly, home_has_edge, away_has_edge, outcome_correct
            FROM predictions WHERE FALSE;

            -- Step 2: Ensure the target table has all the latest columns by running ALTER TABLE ADD COLUMN IF NOT EXISTS ...
            BEGIN
                BEGIN
                    ALTER TABLE predictions_legacy_p2_migration ADD COLUMN confluence_tier VARCHAR(10);
                    ALTER TABLE predictions_legacy_p2_migration ADD COLUMN model_agreement INTEGER;
                    ALTER TABLE predictions_legacy_p2_migration ADD COLUMN best_bookmaker VARCHAR(50);
                    ALTER TABLE predictions_legacy_p2_migration ADD COLUMN best_home_ml INTEGER;
                    ALTER TABLE predictions_legacy_p2_migration ADD COLUMN best_away_ml INTEGER;
                    ALTER TABLE predictions_legacy_p2_migration ADD COLUMN home_ev FLOAT;
                    ALTER TABLE predictions_legacy_p2_migration ADD COLUMN away_ev FLOAT;
                    ALTER TABLE predictions_legacy_p2_migration ADD COLUMN home_kelly FLOAT;
                    ALTER TABLE predictions_legacy_p2_migration ADD COLUMN away_kelly FLOAT;
                    ALTER TABLE predictions_legacy_p2_migration ADD COLUMN home_has_edge BOOLEAN;
                    ALTER TABLE predictions_legacy_p2_migration ADD COLUMN away_has_edge BOOLEAN;
                    ALTER TABLE predictions_legacy_p2_migration ADD COLUMN outcome_correct BOOLEAN;
                EXCEPTION WHEN duplicate_column THEN NULL;
                END;
            END;
            
            -- Step 2b: Move existing predictions lacking strict tracking to the legacy table
            WITH moved AS (
                DELETE FROM predictions 
                WHERE dataset_sha256 IS NULL 
                   OR artifact_sha256 IS NULL 
                   OR inference_input_sha256 IS NULL
                RETURNING id, game_id, model_version, predicted_at, dataset_sha256, artifact_sha256, inference_input_sha256, as_of, home_win_prob, away_win_prob, confluence_tier, model_agreement, best_bookmaker, best_home_ml, best_away_ml, home_ev, away_ev, home_kelly, away_kelly, home_has_edge, away_has_edge, outcome_correct
            )
            INSERT INTO predictions_legacy_p2_migration (id, game_id, model_version, predicted_at, dataset_sha256, artifact_sha256, inference_input_sha256, as_of, home_win_prob, away_win_prob, confluence_tier, model_agreement, best_bookmaker, best_home_ml, best_away_ml, home_ev, away_ev, home_kelly, away_kelly, home_has_edge, away_has_edge, outcome_correct)
            SELECT id, game_id, model_version, predicted_at, dataset_sha256, artifact_sha256, inference_input_sha256, as_of, home_win_prob, away_win_prob, confluence_tier, model_agreement, best_bookmaker, best_home_ml, best_away_ml, home_ev, away_ev, home_kelly, away_kelly, home_has_edge, away_has_edge, outcome_correct FROM moved;

            -- Step 3: Enforce NOT NULL for tracking on the main table
            ALTER TABLE predictions ALTER COLUMN dataset_sha256 SET NOT NULL;
            ALTER TABLE predictions ALTER COLUMN artifact_sha256 SET NOT NULL;
            ALTER TABLE predictions ALTER COLUMN inference_input_sha256 SET NOT NULL;

            -- Step 4: Drop legacy constraint unconditionally if it exists
            IF EXISTS (
                SELECT 1 FROM pg_constraint c 
                JOIN pg_class t ON c.conrelid = t.oid 
                JOIN pg_namespace n ON t.relnamespace = n.oid
                WHERE c.conname = 'uq_prediction_game_model' AND t.relname = 'predictions' AND n.nspname = current_schema()
            ) THEN
                ALTER TABLE predictions DROP CONSTRAINT uq_prediction_game_model;
            END IF;

            -- Step 5: Add strictly deterministic run constraint safely
            SELECT c.oid INTO c_oid
            FROM pg_constraint c
            JOIN pg_class t ON c.conrelid = t.oid
            JOIN pg_namespace n ON t.relnamespace = n.oid
            WHERE c.conname = 'uq_prediction_run' AND t.relname = 'predictions' AND n.nspname = current_schema();

            IF c_oid IS NOT NULL THEN
                c_def := pg_get_constraintdef(c_oid);
                IF c_def <> 'UNIQUE (game_id, model_version, artifact_sha256, inference_input_sha256)' THEN
                    ALTER TABLE predictions DROP CONSTRAINT uq_prediction_run;
                    ALTER TABLE predictions ADD CONSTRAINT uq_prediction_run UNIQUE (game_id, model_version, artifact_sha256, inference_input_sha256);
                END IF;
            ELSE
                ALTER TABLE predictions ADD CONSTRAINT uq_prediction_run UNIQUE (game_id, model_version, artifact_sha256, inference_input_sha256);
            END IF;
            
        END
        $$;
        """
        await conn.execute(text(ddl))
        await conn.execute(text("""
        -- 3. Migrate odds_snapshots to use observed_at
        DO $$ 
        BEGIN
            -- Backfill legacy records
            UPDATE odds_snapshots SET observed_at = captured_at WHERE observed_at IS NULL;
            
            -- Make it NOT NULL
            ALTER TABLE odds_snapshots ALTER COLUMN observed_at SET NOT NULL;
            
            -- Drop old constraint
            BEGIN
                ALTER TABLE odds_snapshots DROP CONSTRAINT IF EXISTS uq_odds_snapshot;
            EXCEPTION WHEN undefined_object THEN NULL;
            END;
            
            -- Create new constraint
            BEGIN
                ALTER TABLE odds_snapshots ADD CONSTRAINT uq_odds_snapshot UNIQUE (game_id, bookmaker, market, observed_at);
            EXCEPTION WHEN duplicate_table OR duplicate_object THEN NULL;
            END;
        END $$;
"""))
        print("Migration applied successfully.")

if __name__ == "__main__":
    asyncio.run(migrate())
