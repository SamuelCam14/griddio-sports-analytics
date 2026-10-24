"""
Test suite de verificación integral para el motor de disponibilidad e impacto de producción fantasy.
Valida:
1. Jugadores confirmados OUT/IR reciben p_absence = 1.0 y generan pérdida matemática real.
2. Jugadores Questionable con línea activa en The Odds API reciben p_absence = 0.0 (juegan, sin penalización).
3. Jugadores Questionable sin línea cuando el mercado está abierto (>= 1 jugador con línea) reciben p_absence = 1.0.
4. Regla de seguridad de mercado: Si el mercado entrega 0 líneas para todos los jugadores, Questionable
   se maneja SIN penalización (p_absence = 0.0) hasta actualización pre-kickoff.
5. Diferenciación matemática por jugador: WR estrella vs secundario, snaps de línea ofensiva y defensiva.
6. Integración punto a punto con el contexto de Game 16 (Steelers vs Saints) con datos reales nflverse.
"""
from datetime import datetime, timezone, timedelta
import pytest
from app.services.nfl_availability_service import (
    NFLAvailabilityService,
    CoverageState,
    PlayerInjuryRecord,
    TeamAvailabilitySummary,
)
from app.services.matchup_context_service import _IMPACT_CACHE


@pytest.fixture
def availability_service(tmp_path):
    return NFLAvailabilityService(cache_dir=tmp_path)


def test_confirmed_out_player_receives_full_absence_penalty(availability_service):
    """Jugador reportado como OUT recibe p_absence = 1.0 y su impacto se calcula contra su PPR previo."""
    svc = availability_service
    record = svc.process_player_injury(
        {
            "player_name": "Justin Jefferson",
            "position": "WR",
            "status": "OUT",
            "injury_detail": "Hamstring",
            "observed_at": "2026-10-24T12:00:00Z",
        },
        team="MIN",
        game_start_utc="2026-10-25T17:00:00Z",
        decision_cutoff="2026-10-25T15:00:00Z",
    )
    assert record.p_absence == 1.0
    assert record.p_play == 0.0
    assert record.canonical_player_id is not None

    # Calcular pérdida contra prior roster
    prior_stats = {
        "00-0036322": {"rolling_ppr": 18.5, "rolling_off_pct": 0.95, "rolling_def_pct": 0.0},
        "00-0036323": {"rolling_ppr": 10.0, "rolling_off_pct": 0.80, "rolling_def_pct": 0.0},
        "justin jefferson": {"rolling_ppr": 18.5, "rolling_off_pct": 0.95, "rolling_def_pct": 0.0},
    }
    summary = svc.compute_team_production_lost(
        team="MIN",
        records=[record],
        prior_roster_stats=prior_stats,
        is_source_available=True,
        decision_cutoff="2026-10-25T15:00:00Z",
        allow_unresolved_market=True,
    )
    assert summary.coverage_state == CoverageState.COVERED_WITH_INJURIES.value
    # Team potential PPR = 18.5 + 10.0 = 28.5. Jefferson lost = 18.5 -> 18.5 / 28.5 = 0.6491
    assert summary.ppr_lost_pct is not None
    assert summary.ppr_lost_pct == pytest.approx(18.5 / 28.5, rel=1e-3)
    assert summary.off_snaps_lost_pct == pytest.approx(0.95 / 11.0, rel=1e-3)


def test_questionable_with_active_line_plays_without_penalty(availability_service):
    """Questionable con línea en The Odds API cuando el mercado está activo -> p_absence = 0.0 (va a jugar)."""
    svc = availability_service
    live_props = [
        {
            "event_id": "event_123",
            "player_name": "Josh Allen",
            "prop_type": "player_pass_yds",
            "market_line": 245.5,
            "price": -115,
            "bookmaker": "draftkings",
            "timestamp": "2026-10-24T14:00:00Z",
        },
        {
            "event_id": "event_123",
            "player_name": "James Cook",
            "prop_type": "player_rush_yds",
            "market_line": 65.5,
            "price": -110,
            "bookmaker": "draftkings",
            "timestamp": "2026-10-24T14:00:00Z",
        },
    ]
    record = svc.process_player_injury(
        {
            "player_name": "Josh Allen",
            "position": "QB",
            "status": "QUESTIONABLE",
            "injury_detail": "Right shoulder",
            "observed_at": "2026-10-24T12:00:00Z",
        },
        team="BUF",
        game_start_utc="2026-10-25T17:00:00Z",
        decision_cutoff="2026-10-25T15:00:00Z",
        live_props=live_props,
        expected_event_id="event_123",
        binary_market_resolution=True,
    )
    assert record.p_absence == 0.0
    assert record.p_play == 1.0
    assert record.market_signal == "listed_at_cutoff"


def test_questionable_without_line_when_market_open_marked_out(availability_service):
    """Questionable sin línea en The Odds API cuando el mercado sí tiene líneas para otros -> p_absence = 1.0 (OUT)."""
    svc = availability_service
    live_props = [
        {
            "event_id": "event_123",
            "player_name": "James Cook",
            "prop_type": "player_rush_yds",
            "market_line": 65.5,
            "price": -110,
            "bookmaker": "draftkings",
            "timestamp": "2026-10-24T14:00:00Z",
        },
        {
            "event_id": "event_123",
            "player_name": "Dalton Kincaid",
            "prop_type": "player_reception_yds",
            "market_line": 45.5,
            "price": -115,
            "bookmaker": "draftkings",
            "timestamp": "2026-10-24T14:00:00Z",
        },
    ]
    record = svc.process_player_injury(
        {
            "player_name": "Keon Coleman",
            "position": "WR",
            "status": "QUESTIONABLE",
            "injury_detail": "Wrist",
            "observed_at": "2026-10-24T12:00:00Z",
        },
        team="BUF",
        game_start_utc="2026-10-25T17:00:00Z",
        decision_cutoff="2026-10-25T15:00:00Z",
        live_props=live_props,
        expected_event_id="event_123",
        binary_market_resolution=True,
    )
    # Al haber líneas para otros y ninguna para Coleman, se marca OUT
    assert record.p_absence == 1.0
    assert record.p_play == 0.0
    assert record.market_signal == "not_listed_with_coverage"
    assert record.coverage_flags.get("market_ruled_out") is True


def test_safety_guard_market_unopened_zero_lines_no_penalty(availability_service):
    """Si el mercado entrega 0 líneas para todos (error o mercado no abierto), Questionable NO se penaliza."""
    svc = availability_service
    # 0 líneas en el evento
    live_props = []
    record = svc.process_player_injury(
        {
            "player_name": "Rico Dowdle",
            "position": "RB",
            "status": "QUESTIONABLE",
            "injury_detail": "Hip",
            "observed_at": "2026-10-24T12:00:00Z",
        },
        team="PIT",
        game_start_utc="2026-10-25T17:00:00Z",
        decision_cutoff="2026-10-25T15:00:00Z",
        live_props=live_props,
        expected_event_id="event_unopened",
        binary_market_resolution=True,
    )
    # Regla estricta: si 0 jugadores tienen línea, NO penalizar como OUT
    assert record.p_absence == 0.0
    assert record.p_play == 1.0
    assert record.market_signal == "unknown"


def test_player_differentiation_star_wr_vs_secondary(availability_service):
    """El modelo diferencia matemáticamente entre un receptor estrella y uno secundario."""
    svc = availability_service
    prior_stats = {
        "00-001": {"rolling_ppr": 20.0, "rolling_off_pct": 0.90, "rolling_def_pct": 0.0},
        "00-002": {"rolling_ppr": 3.0, "rolling_off_pct": 0.25, "rolling_def_pct": 0.0},
        "00-003": {"rolling_ppr": 77.0, "rolling_off_pct": 1.0, "rolling_def_pct": 0.0},
        "star wr": {"rolling_ppr": 20.0, "rolling_off_pct": 0.90, "rolling_def_pct": 0.0},
        "backup wr": {"rolling_ppr": 3.0, "rolling_off_pct": 0.25, "rolling_def_pct": 0.0},
    }

    # Caso A: Se lesiona el estrella
    rec_star = svc.process_player_injury(
        {
            "player_name": "Star WR",
            "canonical_player_id": "00-001",
            "position": "WR",
            "status": "OUT",
            "observed_at": "2026-10-24T12:00:00Z",
        },
        team="TEST",
        game_start_utc="2026-10-25T17:00:00Z",
        decision_cutoff="2026-10-25T15:00:00Z",
    )
    sum_star = svc.compute_team_production_lost(
        team="TEST",
        records=[rec_star],
        prior_roster_stats=prior_stats,
        is_source_available=True,
        decision_cutoff="2026-10-25T15:00:00Z",
        allow_unresolved_market=True,
    )

    # Caso B: Se lesiona el secundario
    rec_backup = svc.process_player_injury(
        {
            "player_name": "Backup WR",
            "canonical_player_id": "00-002",
            "position": "WR",
            "status": "OUT",
            "observed_at": "2026-10-24T12:00:00Z",
        },
        team="TEST",
        game_start_utc="2026-10-25T17:00:00Z",
        decision_cutoff="2026-10-25T15:00:00Z",
    )
    sum_backup = svc.compute_team_production_lost(
        team="TEST",
        records=[rec_backup],
        prior_roster_stats=prior_stats,
        is_source_available=True,
        decision_cutoff="2026-10-25T15:00:00Z",
        allow_unresolved_market=True,
    )

    assert sum_star.ppr_lost_pct is not None
    assert sum_backup.ppr_lost_pct is not None
    # El impacto del estrella (20.0 / 100.0 = 20.0%) debe ser significativamente mayor al secundario (3.0 / 100.0 = 3.0%)
    assert sum_star.ppr_lost_pct > sum_backup.ppr_lost_pct * 5.0
    assert sum_star.off_snaps_lost_pct > sum_backup.off_snaps_lost_pct * 3.0


def test_offensive_and_defensive_line_snap_differentiation(availability_service):
    """Jugadores de línea ofensiva (C/G) y defensiva (DL) que no tienen PPR afectan correctamente los porcentajes de snaps."""
    svc = availability_service
    prior_stats = {
        "00-0036408": {"rolling_ppr": 0.0, "rolling_off_pct": 1.0, "rolling_def_pct": 0.0},
        "cesar ruiz": {"rolling_ppr": 0.0, "rolling_off_pct": 1.0, "rolling_def_pct": 0.0},
        "00-0034760": {"rolling_ppr": 0.0, "rolling_off_pct": 0.0, "rolling_def_pct": 0.70},
        "nathan shepherd": {"rolling_ppr": 0.0, "rolling_off_pct": 0.0, "rolling_def_pct": 0.70},
        "00-0099999": {"rolling_ppr": 100.0, "rolling_off_pct": 1.0, "rolling_def_pct": 0.0},
    }

    rec_ruiz = svc.process_player_injury(
        {"player_name": "Cesar Ruiz", "position": "G", "status": "OUT", "observed_at": "2026-10-24T12:00:00Z"},
        team="NO",
        game_start_utc="2026-10-25T17:00:00Z",
        decision_cutoff="2026-10-25T15:00:00Z",
    )
    rec_shepherd = svc.process_player_injury(
        {"player_name": "Nathan Shepherd", "position": "DT", "status": "OUT", "observed_at": "2026-10-24T12:00:00Z"},
        team="NO",
        game_start_utc="2026-10-25T17:00:00Z",
        decision_cutoff="2026-10-25T15:00:00Z",
    )

    summary = svc.compute_team_production_lost(
        team="NO",
        records=[rec_ruiz, rec_shepherd],
        prior_roster_stats=prior_stats,
        is_source_available=True,
        decision_cutoff="2026-10-25T15:00:00Z",
        allow_unresolved_market=True,
    )
    assert summary.coverage_state == CoverageState.COVERED_WITH_INJURIES.value
    # PPR no se ve afectado porque son linieros sin stats ofensivas registradas
    assert summary.ppr_lost_pct == 0.0
    # Snaps ofensivos perdidos = 1.0 / 11.0 = 9.09%
    assert summary.off_snaps_lost_pct == pytest.approx(1.0 / 11.0, rel=1e-3)
    # Snaps defensivos perdidos = 0.70 / 11.0 = 6.36%
    assert summary.def_snaps_lost_pct == pytest.approx(0.70 / 11.0, rel=1e-3)


@pytest.mark.asyncio
async def test_matchup_context_game_16_returns_verified_production_loss():
    """El endpoint y servicio get_matchup_context para Game 16 (Steelers @ Saints) retorna métricas reales y no guiones."""
    from app.core.database import AsyncSessionLocal
    from app.services.matchup_context_service import get_matchup_context

    async with AsyncSessionLocal() as db:
        ctx = await get_matchup_context(16, db)
        assert ctx is not None
        assert "injuries" in ctx
        inj = ctx["injuries"]
        assert "home_impact" in inj
        assert "away_impact" in inj

        home_impact = inj["home_impact"]
        away_impact = inj["away_impact"]

        # Ninguno debe ser 'unknown'
        assert home_impact["coverage_state"] == "covered_with_injuries"
        assert away_impact["coverage_state"] == "covered_with_injuries"

        # Deben tener floats válidos (no None)
        assert isinstance(home_impact["ppr_lost_pct"], float)
        assert isinstance(home_impact["off_snaps_lost_pct"], float)
        assert isinstance(home_impact["def_snaps_lost_pct"], float)

        assert isinstance(away_impact["ppr_lost_pct"], float)
        assert isinstance(away_impact["off_snaps_lost_pct"], float)
        assert isinstance(away_impact["def_snaps_lost_pct"], float)

        # PIT tiene lesionados confirmados con estadísticas reales (Ben Skowronek, Brandin Echols, Sebastian Joseph-Day)
        assert away_impact["ppr_lost_pct"] > 0.0
        assert away_impact["off_snaps_lost_pct"] > 0.0
        assert away_impact["def_snaps_lost_pct"] > 0.0


def test_practice_participation_modulation(tmp_path):
    """Verifica que la participación en prácticas module adecuadamente P(absence) para Questionable."""
    from app.services.nfl_availability_service import NFLAvailabilityService, AvailabilityStatus, parse_practice_participation, resolve_base_p_absence

    svc = NFLAvailabilityService()
    cw = {"p_dnp": "ID_DNP", "p_fp": "ID_FP", "p_lp": "ID_LP", "p_none": "ID_NONE"}
    game_start = "2026-10-25T18:00:00Z"
    cutoff = "2026-10-23T20:00:00Z"
    obs = "2026-10-23T18:00:00Z"

    # 1. DNP en viernes -> P(absence) sube al 70% (0.70)
    rec_dnp = svc.process_player_injury(
        player_dict={
            "player_name": "Player DNP",
            "position": "RB",
            "status": "QUESTIONABLE",
            "practice_status": "Did not practice on Friday (ankle)",
            "espn_id": "p_dnp",
            "observed_at": obs,
        },
        team="KC",
        game_start_utc=game_start,
        decision_cutoff=cutoff,
        crosswalk=cw,
    )
    assert rec_dnp.p_absence == 0.70
    assert rec_dnp.coverage_flags.get("practice_participation") == "DNP"
    assert rec_dnp.coverage_flags.get("practice_modulated") is True

    # 2. FP en viernes -> P(absence) baja al 5% (0.05)
    rec_fp = svc.process_player_injury(
        player_dict={
            "player_name": "Player FP",
            "position": "WR",
            "status": "QUESTIONABLE",
            "practice_status": "Upgraded to full participant on Friday",
            "espn_id": "p_fp",
            "observed_at": obs,
        },
        team="KC",
        game_start_utc=game_start,
        decision_cutoff=cutoff,
        crosswalk=cw,
    )
    assert rec_fp.p_absence == 0.05
    assert rec_fp.coverage_flags.get("practice_participation") == "FP"
    assert rec_fp.coverage_flags.get("practice_modulated") is True

    # 3. LP en viernes -> P(absence) conserva base histórica 25% (0.25)
    rec_lp = svc.process_player_injury(
        player_dict={
            "player_name": "Player LP",
            "position": "TE",
            "status": "QUESTIONABLE",
            "practice_status": "Limited participant in practice Friday",
            "espn_id": "p_lp",
            "observed_at": obs,
        },
        team="KC",
        game_start_utc=game_start,
        decision_cutoff=cutoff,
        crosswalk=cw,
    )
    assert rec_lp.p_absence == 0.25
    assert rec_lp.coverage_flags.get("practice_participation") == "LP"

    # 4. Sin reporte de práctica -> P(absence) base histórica 25% (0.25)
    rec_none = svc.process_player_injury(
        player_dict={
            "player_name": "Player None",
            "position": "QB",
            "status": "QUESTIONABLE",
            "practice_status": None,
            "espn_id": "p_none",
            "observed_at": obs,
        },
        team="KC",
        game_start_utc=game_start,
        decision_cutoff=cutoff,
        crosswalk=cw,
    )
    assert rec_none.p_absence == 0.25


def test_get_cached_player_props_unpacks_odds_api_wrapper(tmp_path, monkeypatch):
    """Verifica que get_cached_player_props_for_event desempaquete correctamente archivos con envoltura 'data'."""
    import json
    from app.services.nfl_availability_service import NFLAvailabilityService

    raw_dir = tmp_path / "odds_api" / "2026-10-23"
    raw_dir.mkdir(parents=True)
    payload = {
        "source": "the_odds_api_v4",
        "endpoint": "/sports/americanfootball_nfl/events/evt_test_999/odds",
        "data": {
            "id": "evt_test_999",
            "sport_key": "americanfootball_nfl",
            "bookmakers": [
                {
                    "key": "draftkings",
                    "title": "DraftKings",
                    "last_update": "2026-10-23T18:00:00Z",
                    "markets": [
                        {
                            "key": "player_rush_yds",
                            "outcomes": [
                                {
                                    "name": "Over",
                                    "description": "Rico Dowdle",
                                    "price": -115,
                                    "point": 48.5,
                                }
                            ],
                        }
                    ],
                }
            ],
        },
    }
    mock_file = raw_dir / "mock_event.json"
    mock_file.write_text(json.dumps(payload), encoding="utf-8")

    monkeypatch.setattr("app.services.nfl_availability_service.Path", lambda p: tmp_path / "odds_api" if p == "data/raw/odds_api" else Path(p))

    svc = NFLAvailabilityService()
    props = svc.get_cached_player_props_for_event("evt_test_999")
    assert len(props) == 1
    assert props[0]["player_name"] == "Rico Dowdle"
    assert props[0]["market_line"] == 48.5
    assert props[0]["prop_type"] == "player_rush_yds"
    assert props[0]["bookmaker"] == "draftkings"

