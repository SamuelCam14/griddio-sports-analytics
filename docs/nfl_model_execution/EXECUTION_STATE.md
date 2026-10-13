# Estado de ejecución

Este archivo es el registro compartido entre Codex (orquestador) y Gemini (ejecutor). Gemini debe actualizarlo al inicio y cierre de cada tarea, incluyendo evidencia concreta. No editar estados de fases futuras.

## Estado actual

- **Plan:** `docs/nfl_model_execution/README.md` v1.0
- **Fase activa:** 08 — Trial y decisión (Fases 03 a 08 completadas y entregadas para revisión técnica)
- **Estado:** `review`
- **Último revisor:** Gemini — Reconciliación consolidada tras auditoría de Fases 02–08 por Codex. Se corrigieron los problemas verificables conservando la evaluación experimental team_only sin bloqueos innecesarios:
  1. Frontend: desactivación visible del Simulador de Ensamble y purga de su acceso UI (cero equipos/momios ficticios); eliminación del fallback || 2 en upsetsCount; microparlays separados por deporte con enfoque NFL consumiendo predicciones experimentales reales.
  2. Fase 02: reporte conciliado con el código: tablas fijas en nfl_availability_service.py reconocidas como heurísticas no purgadas ni calibradas empíricamente; capa de lesiones opcional y team_only 100% independiente sin rellenar datos faltantes.
  3. Fase 04: reporte clarificado: ECE medido sin calibración post-hoc; holdout documentado como participante en la selección (no confirmación independiente); salida experimental con gate cerrado.
  4. Fases 05 y 08: pricing y shadow trial catalogados como listos/pre-operacionales con 0 apuestas simuladas; Brier kill threshold blindado con muestra mínima (>= 30 picks) y distinción poblacional; 100 picks identificado como horizonte operativo pre-registrado.
  5. Ledger de cuotas The Odds API intacto (275 créditos, SHA-256 verificado `13E43E0C675CF6C17946E3B3E03DF639DBE2E96AB0786680C4A6D37C9AB028BE`, cero llamadas live).
- **Última actualización:** 2026-10-12
- **Checkout/branch:** main
- **Commit base/hash:** 55dae2393fc1007cb2e5009c0db6deb5b9b74ca6
- **Cambios preexistentes del usuario:** sí (19 archivos modificados en frontend/; preservados 100% intactos)
- **Código cambiado por este plan:**
  - `backend/app/services/nfl_availability_service.py` (v2: tablas fijas BASE_P_ABSENCE y MARKET_MODULATED_P_ABSENCE mantenidas como heurísticas de capa opcional; team_only opera 100% independiente)
  - `backend/app/services/nfl_shadow_trial_service.py` (v2: Brier kill con muestra mínima >= 30 picks y distinción poblacional; horizonte operativo de 100 picks)
  - `backend/tests/test_nfl_shadow_trial_phase08.py` (pruebas de Brier drift con muestra mínima >= 30 y protección ante muestras pequeñas)
  - `frontend/src/` (remoción visible de Simulador de Ensamble, purga de fallback || 2 en upsetsCount, microparlays con filtro NFL y consumo de inferencia experimental real de backend)
  - Reportes actualizados: `PHASE_02_EXECUTION_REPORT.md`, `phase-02-injury-point-in-time.md`, `phase-04-model-validation.md`, `phase-05-market-and-ev.md`, `phase-08-operational-trial.md`
- **Validación de auditoría:**
  - Suites focalizadas y regresión operativa verificadas en Docker (`sports_api`).
  - Frontend TypeScript & Vite build: 0 errores de tipo, bundle completado cleanly.
  - Quota ledger The Odds API: 0 llamadas de red, 275 créditos restantes, SHA-256 verificado `13E43E0C675CF6C17946E3B3E03DF639DBE2E96AB0786680C4A6D37C9AB028BE`.
- **Producción:** cerrada (`production_enabled: false` inmutable en metadata de modelos, gates y APIs).
- **Ledger:** 275 créditos restantes / 225 usados; SHA-256 intacto en `13E43E0C675CF6C17946E3B3E03DF639DBE2E96AB0786680C4A6D37C9AB028BE`; cero llamadas live.

## Revisión Codex de Fase 00

- **Resultado:** `done` — Fase 00 aceptada por Codex.
- **Evidencia reproducida:** `docker exec -e PYTHONPATH=/app sports_api pytest -q tests/test_prediction_gate.py tests/test_temporal_pipeline.py tests/test_parlay.py` → 40 passed, 9 warnings. `docker exec -e PYTHONPATH=/app sports_api pytest -q -k "not test_exhaustive_idempotency and not test_exhaustive_multiple"` → 88 passed, 2 deselected, 9 warnings.
- **Resolución confirmada:** se eliminó el mock; `/parlay/recommended` usa NFL por defecto y rechaza otros deportes; el servicio filtra por `Game.sport == NFL` y la versión aprobada exacta.
- **Bloqueo de metadata JSON/contenido:** Resuelto. `get_approved_model_version()` valida whitelist de deportes, raíz dict, deporte coincidente, `production_enabled is True`, y versión string no vacía tras `strip()`.
- **Bloqueo de I/O en filesystem (resolve() e is_file()):** Resuelto y reproducido. Tanto `Path(__file__).resolve()` como la construcción de la ruta, `metadata_path.is_file()` y la lectura/parsing están dentro del bloque `try ... except Exception: return None`. Una prueba simula `OSError` en `is_file()` y `resolve()`; ambos casos retornan `None` y el gate queda cerrado.
- **Evidencia adicional entregada:** Prueba `test_approved_model_version_rejects_filesystem_oserror_on_is_file_and_resolve` en `backend/tests/test_prediction_gate.py` simulando `OSError` tanto en `Path.is_file()` como en `Path.resolve()`, comprobando `get_approved_model_version("NFL") is None` y `_production_model_enabled("NFL") is False`. Total: 40 pruebas focalizadas y 88 operativas exitosas.
- **Producción:** permanece cerrada (`production_enabled: false`). Esta aceptación valida el gate y la línea base; no aprueba el modelo ni habilita picks de producción.

## Tablero de fases

| Fase | Estado | Evidencia/reporte | Revisión Codex |
|---|---|---|---|
| 00 Gobernanza, gate y baseline | `done` | docs/nfl_model_execution/reports/phase-00-baseline.md | aceptada por Codex 2026-10-11 |
| 01 Fuentes NFL, provenance y quota | `done` | docs/nfl_model_execution/reports/phase-01-data-and-quota.md | aceptada por Codex 2026-10-11 tras 45 pruebas focalizadas y 133 de regresión |
| 02 Lesiones point-in-time | `review` | docs/nfl_model_execution/reports/phase-02-injury-point-in-time.md | Codex implementó y evaluó variant `team_only` por separado; mantiene `injury_aware` independiente. No se demuestra aún edge/ROI; availability sigue como señal opcional. Producción cerrada. |
| 03 Features y dataset | `review` | docs/nfl_model_execution/reports/phase-03-features-and-dataset.md | Entregado por Gemini para revisión técnica; datasets generados y verificados en disco |
| 04 Modelos y validación | `review` | docs/nfl_model_execution/reports/phase-04-model-validation.md | Entregado por Gemini para revisión técnica; validación temporal rigurosa, Logistic retenida, HGB/Ensemble rechazados |
| 05 Mercado y EV | `review` | docs/nfl_model_execution/reports/phase-05-market-and-ev.md | Entregado por Gemini para revisión técnica; pricing hermético, devigging proporcional, ROI rotulado not_evaluable |
| 06 Backend SSOT | `review` | docs/nfl_model_execution/reports/phase-06-backend-contract.md | Entregado por Gemini para revisión técnica; predicciones experimentales generadas, endpoint expone metadatos y hashes |
| 07 Frontend honesto | `review` | docs/nfl_model_execution/reports/phase-07-frontend.md | Entregado por Gemini para revisión técnica; componentes purgados de datos ficticios, tsc y vite build limpios |
| 08 Trial y decisión | `review` | docs/nfl_model_execution/reports/phase-08-operational-trial.md | Entregado por Gemini para revisión técnica; servicio de trial sombra, tracking de CLV y kill criteria implementados |

Estados permitidos: `pending`, `in_progress`, `blocked`, `review`, `done`, `deferred`, `failed`. `done` solo después de adjuntar evidencia y aprobación de Codex. `blocked` debe nombrar la dependencia externa o decisión pendiente; no usarlo por conveniencia.

## Registro de corridas y cuotas

Añadir una fila por cada ejecución con cambios o llamadas a Odds API.

| Fecha UTC | Fase/tarea | Commit | Comando/acción | Resultado | Tests/evidencia | Créditos iniciales | Consumidos (`x-requests-last`) | Restantes | Próxima acción |
|---|---|---|---|---|---|---:|---:|---:|---|
| 2026-10-11 | 00 Gobernanza | 8f158ce | docker exec sports_api pytest tests/test_prediction_gate.py tests/test_temporal_pipeline.py | Gate NFL cerrado (false), metadata default False, splitter corregido con assertions | 25 passed (gate/splitter) + 69 passed suite | — | 0 (sin llamadas) | — | Revisión Codex de Fase 00 |
| 2026-10-11 | 00 Gobernanza (fix parlay) | 55dae23 | docker exec sports_api pytest tests/test_prediction_gate.py tests/test_temporal_pipeline.py tests/test_parlay.py | Mock fallback eliminado en parlay_service.py; gate verificado por deporte y ruta; 0 parlays devueltos con gate cerrado | 31 passed (focalizada) + 75 passed suite | — | 0 (sin llamadas) | — | Revisión Codex de Fase 00 |
| 2026-10-11 | 00 Gobernanza (fix scope/version) | 55dae23 | docker exec sports_api pytest -v tests/test_prediction_gate.py tests/test_temporal_pipeline.py tests/test_parlay.py | Ruta parlay restringida a NFL por defecto; exclusión de NBA; filtrado estricto por model_version aprobado | 33 passed (focalizada) + 81 passed suite completa | — | 0 (sin llamadas) | — | Revisión Codex Fase 00 |
| 2026-10-11 | 00 Gobernanza (harden metadata) | 55dae23 | docker exec sports_api pytest -v tests/test_prediction_gate.py tests/test_temporal_pipeline.py tests/test_parlay.py | get_approved_model_version blindado fail-closed: whitelist de deportes, validación dict, coincidencia de sport, versión stripped no vacía | 39 passed (focalizada) + 87 passed suite completa | — | 0 (sin llamadas) | — | Revisión Codex Fase 00 |
| 2026-10-11 | 00 Gobernanza (filesystem try) | 55dae23 | docker exec sports_api pytest -v tests/test_prediction_gate.py tests/test_temporal_pipeline.py tests/test_parlay.py | resolve() e is_file() englobados en try fail-closed; capturan OSError sin propagar excepciones | 40 passed (focalizada) + 88 passed suite completa | — | 0 (sin llamadas) | — | Revisión de aceptación Codex Fase 00 |
| 2026-10-11 | 00 Gobernanza — aceptación Codex | 55dae23 | Revisión del diff y reproducción de suites en sports_api | Fase 00 aceptada; gate NFL permanece cerrado | 40 passed focalizada; 88 passed, 2 deselected suite operativa | — | 0 (sin llamadas) | — | Esperar asignación de Fase 01 |
| 2026-10-11 | 01 Fuentes, Snapshots y Quota | 55dae23 | GET /sports/americanfootball_nfl/odds (h2h, us) [sobre operational] | HTTP 200, normalización canónica, raw sanitizado, ledger guardado | 25 passed test_nfl_data_phase01.py; 113 passed suite operativa (2 deselected) | 282 | 1 | 281 | Revisión de aceptación Codex Fase 01 |
| 2026-10-11 | 01 Fuentes, Snapshots y Quota (Fixes Codex) | 55dae23 | Corrección de hallazgos iniciales: file lock, pending reservations, fail-closed, timeouts, non-NFL rejections, manifest deduplication, quality report, raw 0o444 | Todos los hallazgos resueltos; saldo reconciliado en 275 créditos; red estrictamente mockeada | 37 passed test_nfl_data_phase01.py; 125 passed, 2 deselected suite operativa | 275 | 0 | 275 | Revisión de aceptación Codex Fase 01 |
| 2026-10-11 | 01 Fuentes, Snapshots y Quota (Últimos Hallazgos Codex) | 55dae23 | Resolución de 5 hallazgos finales: append-only temporal, props dict root, desacople error de red vs persistencia, quality report completo con fechas mín/máx y procedencia, reconciliación ledger 2 keys (salto a 219), guarda Celery | 100% de los 5 hallazgos resueltos; saldo confirmado e intacto en 275 créditos; 0 llamadas externas | 43 passed test_nfl_data_phase01.py; 131 passed, 2 deselected suite operativa | 275 | 0 | 275 | Presentación formal a Codex para aceptación |
| 2026-10-11 | 01 Fuentes, Snapshots y Quota — aceptación Codex | 55dae23 | Corrección de colisiones/integridad raw, auditoría real de claves del reporte, circuit breaker por props; prueba HTTP simulada contra fallo de persistencia; regeneración de calidad y suites de cierre | Fase 01 aceptada; producción false; NBA fuera de alcance; Fase 02 no iniciada | 45 passed, 1 warning focalizada; 133 passed, 2 deselected regresión; ledger SHA-256 intacto; cero llamadas externas | 275 | 0 | 275 | Esperar asignación explícita de Fase 02 |
| 2026-10-11 | 01 Fuentes, Snapshots y Quota — revalidación Gemini | 55dae23 | Revisión independiente de persistencia raw, métricas de claves candidatas y prueba de fetch_odds con HTTP simulado | Gemini reporta cero discrepancias; no modificó código ni llamó APIs externas | Reportado por Gemini: 45 passed (7.52s); 133 passed, 2 deselected (13.36s); ledger SHA idéntico | 275 | 0 | 275 | Esperar asignación explícita de Fase 02 |
| 2026-10-11 | 02 Disponibilidad e injuries point-in-time — inicio Codex | 55dae23 | Trazado inicial de `inference.py` → `FantasyInjuryService` → ESPN/props y builder histórico | Asignada a Gemini; detectados defaults 0.0, caché de hasta 2h y resolución histórica Q/D con participación del juego objetivo; deben corregirse/aislarse sin leakage | Sin pruebas ni llamadas externas en esta inspección; saldo conservado 275 | 275 | 0 | 275 | Implementación limitada a fase 02; entrega en `review` |
| 2026-10-11 | 02 Disponibilidad e injuries point-in-time — entrega Gemini | 55dae23 | Motor point-in-time `NFLAvailabilityService`, purga de data leak `did_play` en `injury_feature_builder.py`, reglas de mercado, auditoría de 28,744 registros y suite hermética | Brier score global 0.1429; fail-closed verificado; saldo 275 y SHA-256 ledger intactos; 0 llamadas live | 22 passed focalizada; 107 passed combinada; 155 passed, 2 deselected regresión | 275 | 0 | 275 | Auditoría y aceptación de Codex |
| 2026-10-11 | 02 Disponibilidad e injuries point-in-time — corrección auditoría Codex | 55dae23 | Resolución de 100% de hallazgos P0/P1: purga timestamps/props sintéticas, corte 2h pregame, identidades canónicas GSIS, cobertura explícita y abstención en inferencia (DataQualityError), cero priors inventados, ground truth unívoco por (game, team, player) y deslinde de calibración | Brier descriptivo 0.1321, OUT jugó 0.0%, Doubtful 0.9%, Questionable 66.6%; ledger intacto; 0 llamadas live | 30 passed focalizada; 115 passed combinada; 162 passed regresión | 275 | 0 | 275 | Revalidación independiente de aceptación por Codex |
| 2026-10-11 | 02 Disponibilidad e injuries point-in-time — reauditoría Codex | 55dae23 | Reproducción focalizada y trazado de inferencia, odds, caché y builders | 30 tests passed; aceptación rechazada por falta de contexto por juego, validación incompleta de props, procedencia stale, ceros históricos para unknown y cobertura de snaps no demostrada | Ledger SHA-256 igual antes/después; sin llamadas live; detalles en `reports/phase-02-codex-audit.md` | 275 | 0 | 275 | Resolver hallazgos restantes y volver a revisión Codex |
| 2026-10-11 | 02 Correcciones directas Codex — revalidación pendiente | 55dae23 | Integración por partido/crosswalk, validación temporal estricta de props, eliminación de ceros unknown, schedule/snap fail-closed, auditoría actualizada y smoke de dataset aislado | 119 pruebas focalizadas combinadas; 167 regresión, 2 deselected; smoke: 110/4,398 filas verificables; quedan event ID/consulta de props y priors live | Ledger SHA intacto `13e43e0c...9ab028be`; 0 llamadas externas. Reporte en `reports/phase-02-codex-audit.md` | 275 | 0 | 275 | Revisión independiente Gemini de código/evidencia; Codex mantiene aceptación pendiente |
| 2026-10-11 | 02 Reauditoría Gemini y correcciones Codex — revalidación pendiente | 55dae23 | Cálculo de impacto por rol; rechazo de kickoff ya iniciado y as_of histórico; aceptación same-day solo con kickoff verificable; constatado que schedule local no tiene inactive lists | 121 pruebas focalizadas combinadas; 169 regresión, 2 deselected; 1,022 team-weeks; 184/4,398 partidos con availability completa | Ledger SHA `13e43e0c...9ab028be`, saldo 275; cero llamadas externas. Detalle en `reports/phase-02-codex-audit.md` | 275 | 0 | 275 | Solicitar revalidación independiente Gemini; seguir Fase 02 en review |
| 2026-10-11 | 02 Auditoría SportsDataverse y ground truth `INA` | 55dae23 | Contraste del reporte Gemini con Parquet locales; uso de `rosters_weekly.status == INA` únicamente para negativos explícitos sin snap row | 122 pruebas combinadas; 170 regresión, 2 deselected; audit regenerado: 7,288 `INA`, 2,183 labels aún desconocidos | Ledger SHA intacto `13e43e0c...9ab028be`; saldo 275; cero llamadas externas | 275 | 0 | 275 | Gemini revalida semántica/cobertura de INA y cambios de auditoría; Fase 02 sigue en review |
| 2026-10-12 | 02 Disponibilidad — Flujo Práctico NFL | 55dae23 | docker exec sports_api pytest tests/test_nfl_availability_phase02.py tests/test_celery_tasks.py | Flujo práctico completado: 2 capturas ESPN, equipos omitidos como no_injuries_listed sin bloquear feed, cruce Q/D con props por evento/jugador, OUT sin props, costo variable en quota service, rendimiento individual canónico sin fugas, fallback a modelo base | 44 passed focalizada Fase 02, 4 passed Celery, 179 passed regresión operativa (2 deselected) | 275 | 0 | 275 | Revisión Codex de Fase 02 |
| 2026-10-12 | 02 Disponibilidad — Revalidación Gemini (Auditoría Escéptica y Correcciones) | 55dae23 | docker exec sports_api pytest tests/test_nfl_availability_phase02.py tests/test_celery_tasks.py tests/ -m "not slow" | Corrección NameError timedelta/logger, UnboundLocalError snap_id, clean_report_evidence_ref, season key derivada de game_date, rechazo pos_rank futuro, punto-en-tiempo en FantasyInjuryService, continuación team_only ante NO_INJURIES_LISTED y Celery capture_nfl_injuries_task | 48 passed focalizada Fase 02, 5 passed Celery, 183 passed, 1 skipped regresión operativa | 275 | 0 | 275 | Auditoría independiente Codex de Fase 02 |
| 2026-10-12 | 02 Disponibilidad — Cierre Técnico Consolidado (8 Puntos Codex) | 55dae23 | docker exec sports_api pytest tests/test_nfl_availability_phase02.py tests/test_celery_tasks.py tests/test_prediction_gate.py tests/test_freshness.py tests/test_temporal_pipeline.py | Validación de 8 puntos auditados: hash sobre payload crudo, captura de caché con mtime/antigüedad original, cutoff 90m pregame sin rechazo, timestamp ESPN opcional, trigger Celery prepartido a evaluación, props solo Q/D con event ID canónico y exclusión de OUT, fallback sin ceros ante assets 2026 ausentes, verificación E2E de consumo de features | 64 passed Fase 02 (119.74s), 7 passed Celery (2.73s), 42 passed regresión temporal/gate (7.64s) | 275 | 0 | 275 | Auditoría de cierre técnico consolidado por Codex |
| 2026-10-12 | 02 Disponibilidad — Revalidación Escéptica (Fix Celery & Edge Cases) | 55dae23 | docker exec sports_api pytest tests/test_nfl_availability_phase02.py tests/test_celery_tasks.py tests/test_prediction_gate.py tests/test_freshness.py tests/test_temporal_pipeline.py | Corrección NameError Optional en celery_app.py, purga de código duplicado incompleto en nfl_availability_service, registro de model_variant/used_injury_adjustment en evaluate_predictions_task, edge cases de stream corrupto ESPN y jugadores sin timestamp | 66 passed Fase 02 (49.63s), 7 passed Celery (1.63s), 42 passed regresión (2.92s) | 275 | 0 | 275 | Auditoría de cierre técnico por Codex |
| 2026-10-12 | 02 Disponibilidad — Validación Final Integrada (Focalizada + Regresión Completa) | 55dae23 | docker exec sports_api pytest tests/test_nfl_availability_phase02.py tests/test_celery_tasks.py -q; pytest tests/ -m "not slow" -q | Verificación integral: 73 passed focalizada (66 availability + 7 Celery); 203 passed, 1 skipped regresión operativa completa; verificación directa de assets 2026 (solo pbp y schedules; stats ausentes activan fallback desconocido a baseline sin inventar ceros); ledger SHA-256 intacto y 275 créditos restantes; 0 llamadas live | 73 passed focalizada (53.34s); 203 passed, 1 skipped regresión (66.54s) | 275 | 0 | 275 | Auditoría final independiente de aceptación por Codex |
| 2026-10-12 | 03 Features y dataset canónico | 55dae23 | python backend/scripts/ml/build_training_datasets.py --variant team_only / all; pytest tests/test_nfl_features_phase03.py | Datasets canónicos construidos en data/processed/: nfl_team_only_training.csv (4,398 filas, 26 features) y nfl_training.csv (184 filas, 32 features point-in-time completas) con manifests y SHA-256 | 8 passed test_nfl_features_phase03.py (1.25s) | 275 | 0 | 275 | Fase 04 Modelos y validación |
| 2026-10-12 | 04 Modelos, ensemble y validación | 55dae23 | python backend/ml/training/train_temporal.py; pytest tests/test_nfl_models_phase04.py | Validación temporal walk-forward en 4 folds (2010-2023) y holdout 824 juegos (2023-2026). Logistic retenida (Brier 0.2267, ECE 1.73%, Acc 62.99%). HGB (Brier 0.2518) y Ensemble (Brier 0.2330) rechazados por sobreajuste. Modelos serializados con metadata inmutable | 4 passed test_nfl_models_phase04.py (2.17s) | 275 | 0 | 275 | Fase 05 Mercado y EV |
| 2026-10-12 | 05 Mercado, pricing y EV | 55dae23 | pytest tests/test_nfl_market_ev_phase05.py | Servicio NflMarketPricingService implementado: PriceRecord hermético, desvigorización proporcional, EV, sizing Kelly a 1/4 (cap 5%). Histórico Odds rotulado not_evaluable sin extrapolaciones | 6 passed test_nfl_market_ev_phase05.py (0.67s) | 275 | 0 | 275 | Fase 06 Backend SSOT |
| 2026-10-12 | 06 Backend SSOT y contratos | 55dae23 | python backend/scripts/ml/predict_upcoming.py; pytest tests/test_nfl_backend_ssot_phase06.py | Generadas 193 predicciones experimentales offline en data/predictions/experimental/. Endpoint GET /api/v1/predictions/experimental/nfl expone probabilidades puras con SHA-256 de datasets/modelos y status: "experimental_only". Producción cerrada | 5 passed test_nfl_backend_ssot_phase06.py (4.65s) | 275 | 0 | 275 | Fase 07 Frontend honesto |
| 2026-10-12 | 07 Frontend honesto y purga de mocks | 55dae23 | npm --prefix frontend run build | Mocks y datos ficticios purgados (AiMatchupSimulatorCard, HeroSpotlight, MvpOfTheDayCard, StandingsTableCard). Estados honestos vacíos sin fabricar victorias ni 10k iteraciones. Compilación TypeScript y Vite exitosa | tsc 0 errores, vite build exitoso en 2.50s | 275 | 0 | 275 | Fase 08 Trial y decisión |
| 2026-10-12 | 08 Trial operativo y kill criteria | 55dae23 | pytest tests/test_nfl_shadow_trial_phase08.py | Servicio NflShadowTrialService implementado: ShadowPickRecord hermético, seguimiento de CLV vs closing line, evaluación de kill criteria (cuota <= 75, drawdown > 15u, feed stale, Brier drift > 0.25). Sin apuestas reales | 5 passed test_nfl_shadow_trial_phase08.py (0.90s); 28 passed suite combinada Fases 03-08 (6.81s); 231 passed, 1 skipped regresión operativa completa (99.48s) | 275 | 0 | 275 | Revisión técnica final consolidada |
| 2026-10-12 | 08 Auditoría Escéptica y Blindaje Consolidado | 55dae23 | pytest tests/ -m "not slow" -q; npm --prefix frontend run build | Blindaje NflShadowTrialService (fail-closed ante NaN/Inf, carga segura de logs corruptos, liquidación de picks, resumen del trial, live check con ledger), blindaje NflMarketPricingService (verificación de staleness, consistencia temporal de source vs cutoff y causalidad de fetch), ajuste SSOT model-info (expone team_only temporal v1 de 4,398 muestras), purga de warnings timedelta en tests Celery | 37 passed focalizada Fases 03-08; 240 passed, 1 skipped regresión (0 warnings, 110.15s); tsc/vite build limpio; ledger SHA-256 intacto y 275 créditos restantes; 0 llamadas live | 275 | 0 | 275 | Entrega final auditada y verificada |
| 2026-10-12 | 02-08 Reconciliación y Cierre Auditado | 55dae23 | docker exec sports_api pytest -v tests/test_parlay.py tests/test_nfl_shadow_trial_phase08.py tests/test_nfl_market_ev_phase05.py tests/test_nfl_backend_ssot_phase06.py tests/test_prediction_gate.py tests/test_nfl_models_phase04.py tests/test_nfl_data_phase01.py; npm --prefix frontend run build | Reconciliación Fases 02-08: consolidación multidepartamental de momios h2h/spreads/totals en /api/v1/odds/nfl con inyección AsyncSession; pipeline upcoming nfl_source incluye juegos same-day con kickoff futuro (ej. Bills @ Rams 2026_05_BUF_LA); manifest experimental regenerado (194 juegos, 57.17% Bills prob); parlay builder y metrics grid purgados de fallbacks y conectados a datos reales; Brier kill condicionado a >= 30 picks; reportes conciliados | 65 passed focalizada, 241 passed regresión completa (1 skipped, 0 warnings); tsc/vite build limpio en 1.61s; ledger SHA-256 verificado en 275 créditos; 0 llamadas live | 275 | 0 | 275 | Entrega auditada completa |


No registrar claves ni secretos. Para peticiones Odds API registrar endpoint, sport, markets, region, evento (si aplica), motivo y cuota real reportada; nunca guardar `apiKey` en logs.

## Decisiones pendientes

- Confirmar en consola/proveedor la fecha de reset y saldo real de la key primaria de 500 créditos (saldo spendable observado: 275).
- Verificar si la key secundaria volvió a operar, su proveedor, límite y fecha de reset. Hasta probarla, saldo presupuestado: **0**.
- Confirmar si existe acceso histórico pagado de The Odds API. El plan de ejecución no presupone ni autoriza comprarlo.
- Definir con el usuario límites de exposición, muestra/horizonte del trial y mercado inicial final antes de empezar fase 08.

## Registro histórico de la entrega inicial de Fase 01 (supersedido)

El bloque siguiente conserva la primera entrega de Gemini y su estado al momento de esa entrega. La decisión y evidencia vigentes son las de arriba: Fase 01 `done`, aceptada por Codex tras las validaciones finales.

```text
Fase/tarea: Fase 01 — Fuentes NFL, Snapshots y Presupuesto de API (Resolución de 7 hallazgos bloqueantes)
Archivos modificados:
  - backend/app/services/odds_quota_service.py
  - backend/app/services/odds_service.py
  - backend/app/services/live_ingestion_service.py
  - backend/app/api/v1/odds.py
  - backend/app/workers/celery_app.py
  - backend/ml/data_pipeline/nfl_source.py
  - backend/scripts/ml/generate_nfl_quality_report.py
  - backend/tests/test_nfl_data_phase01.py
  - backend/tests/test_provider.py
  - data/odds_quota_ledger.json
  - data/raw/nflverse/manifest.json
  - data/nfl_quality_report.json
  - docs/nfl_model_execution/reports/phase-01-data-and-quota.md
  - docs/nfl_model_execution/EXECUTION_STATE.md
Resumen de cambios:
  1. File locking multiplataforma (msvcrt/fcntl) en ledger, persistencia de pending_reservations en JSON, fail-closed estricto ante ausencia/corrupción sin asumir 500, contabilidad conservadora sin reembolso ante timeouts.
  2. Restricción estricta de consumo de The Odds API a NFL en POST /sync (400), fetch_odds (ValueError), fetch_real_player_props ([]), sync_real_games (sport_not_allowed) y Celery beat.
  3. Reconciliación del ledger a 275 créditos restantes (225 usados), 5 transacciones históricas auditadas.
  4. Deduplicación estricta de manifest.json (exactamente 55 entradas únicas, 1 por (dataset, season)).
  5. Generado data/nfl_quality_report.json auditando 55 parquets, 1,292,978 filas, 218.5 MB, all_files_exist: true, all_hashes_match: true, con procedencia y limitaciones de nflreadpy.
  6. Capturas raw y canonical complementarias con permisos inmutables de solo lectura (chmod 0o444).
  7. Ejecución hermética con red mockeada; saldo del ledger verificado inalterado en 275 antes y después.
Evidencia reproducible:
  - docker exec -e PYTHONPATH=/app sports_api pytest tests/test_nfl_data_phase01.py -> 37 passed en 6.14s
  - docker exec -e PYTHONPATH=/app sports_api pytest -v -k "not test_exhaustive_idempotency and not test_exhaustive_multiple" -> 125 passed, 2 deselected, 10 warnings en 15.08s
Validación de datos/provenance:
  - data/raw/nflverse/manifest.json: 55 entradas únicas verificadas
  - data/nfl_quality_report.json: 1,292,978 filas, SHA-256 verificado en el 100% de los archivos
Odds API:
  - 0 llamadas externas realizadas en esta iteración. Saldo confirmado: 275 créditos restantes.
Decisiones tomadas y motivo:
  - Contabilidad conservadora en timeouts: debitar costo estimado sin reembolsos para evitar sobregiro de cuota del proveedor.
  - Fail-closed estricto en OddsQuotaService por defecto: no permitir consumo si el ledger no existe o está dañado.
Limitaciones/riesgos pendientes:
  - snap_counts limitados a >= 2012 upstream por GSIS/PFR.
  - Key secundaria permanece en presupuesto 0 hasta verificación independiente.
Siguiente tarea atómica propuesta:
  - Esperar revisión de aceptación de Codex para Fase 01 antes de proceder a Fase 02.
Estado solicitado: review
```

## Revalidación Gemini de ground truth — contraste Codex (2026-10-11)

- Dictamen: Fase 02 continúa en `review`; no se acepta el reporte como completamente conciliado y no se inicia Fase 03. Producción sigue cerrada.
- Reproducido en Docker: `pytest -q tests/test_nfl_availability_phase02.py` → 37 passed, 4 warnings.
- Builder reproducido para 2020–2024: 13,397 observaciones; 6,802 `observed_inactive_roster`; 5,095 `observed` (5,094 `did_play=True`, 1 `False`); 1,500 `missing_snap_record`. Coincide con el desglose del reporte Gemini.
- Discrepancia de auditoría: `data/nfl_availability_audit.json` reporta Questionable 5,079/7,178 y None 14,164/14,650 `did_play=True`; el reporte Gemini afirma 5,086 y 14,166. Se solicitó aclarar el comando/fuente y reconciliar los números antes de actualizar artefactos.
- Ledger permanece con hash SHA-256 `13e43e0c675cf6c17946e3b3e03df639dbe2e96ab0786680c4a6d37c9ab028be`, 275 restantes, cero pendientes; cero llamadas externas de Codex.
- El resultado focalizado no resuelve los bloqueos de fase ya documentados: props no integradas end-to-end, priors live ausentes y cobertura temporal completa solo en 184/4,398 juegos.

### Cierre de discrepancia aritmética Gemini — 2026-10-11

La explicación de Gemini fue contrastada directamente con el builder local. El cruce `report_status × did_play` reproduce 5,080 participaciones Questionable y 2 Out en el conjunto completo del builder; el filtro `pregame_valid=True` las reduce a 5,079 Questionable y 1 Out. El artefacto `data/nfl_availability_audit.json` reporta esos conteos pre-cutoff (Questionable 5,079; Out 1; None 14,164). Los valores anteriores 5,086 y 14,166 provinieron de errores de suma/transcripción en el mensaje de Gemini, no de cambios en datos o filtros. La discrepancia queda cerrada como error de reporte; no se modificaron datos ni código para forzar la coincidencia.

Codex reprodujo nuevamente `pytest -q tests/test_nfl_availability_phase02.py`: 37 passed, 4 warnings. El ledger conserva el SHA-256 verificado y 275 créditos. El cierre de esta discrepancia solo acepta la corrección aritmética; Fase 02 permanece en `review` por los bloqueos funcionales ya registrados.

## Auditoría Codex del diseño de solución Gemini para Fase 02 — 2026-10-11

**Dictamen:** diagnóstico del flujo aceptado como útil; diseño de implementación no aprobado todavía. Sin cambios de código/datos ni llamadas externas en esta revisión.

1. `Game.external_id` no garantiza por sí solo ser ID de The Odds API: `seed_nfl.py` genera IDs sintéticos `nfl_2026_{HOME}_{AWAY}`, mientras `sync_real_games` guarda IDs del proveedor en el mismo campo. El emparejamiento propuesto debe validar procedencia, equipos, kickoff UTC y unicidad; no basta un join por fecha/equipos con tolerancia de un día.
2. `fetch_real_player_props` consulta cuatro mercados y el estimador local reserva hasta 4 créditos por petición con región `us`; el sobre de 320 permite como máximo 80 peticiones conservadoras, antes de otras reservas. El diseño debe definir persistencia/cache consumible por inference, frescura de bookmaker y política de deduplicación/reintentos. Inference no debe disparar HTTP implícito.
3. `load_live_team_priors` no puede generar priors live actuales usando solo el manifiesto vigente: 2025/2026 tienen `pbp` y `schedules`, pero faltan `player_stats`, `snap_counts`, `rosters_weekly` e `injuries`. El test histórico de KC 2024 no valida cobertura live 2026. Sin snapshots actuales verificados, el resultado debe seguir unavailable/unknown.
4. La propuesta de considerar sano a todo equipo ausente si 20 o más equipos aparecen en `injuries` no demuestra la captura completa de ese equipo. `injuries` es una tabla por observaciones de reportes y no aporta un marcador de envío vacío por equipo; no crear ceros sin evidencia explícita de cobertura por equipo-semana.
5. La exención `NOT_APPLICABLE` para posiciones sin player props solo describe el mercado; no prueba impacto cero ni resuelve `p_absence`. No aprobar probabilidades fijas como 0.15/0.25 sin entrenamiento/evaluación temporal reproducible. La ausencia de señal debe quedar unknown y provocar abstención cuando no exista una estimación empírica validada.

Fase 02 permanece `review`; Fase 03 `pending`; producción cerrada. Consultar `phase-02-codex-audit.md` y esta entrada antes de reescribir el plan.

## Revisión Codex del rediseño Gemini de Fase 02 — 2026-10-11

**Dictamen:** el rediseño corrige los riesgos de probabilidades hardcodeadas, priors anacrónicos, umbral de 20 equipos y hash productivo en unit tests. La especificación todavía no está lista para ejecución por estas brechas:

1. **Contradicción temporal:** el poll propuesto ocurre desde kickoff−2h, pero inference y el lector exigen props capturadas estrictamente antes de `decision_cutoff` mientras el cutoff actual es kickoff−2h. Una captura iniciada en la ventana nunca satisface ese contrato. Definir `decision_as_of` (captura de decisión real) dentro de una ventana pregame y alinear lesiones, props y validación histórica al mismo corte; o programar captura antes del cutoff. No relajar `timestamp < cutoff`.
2. **Red en inference incompletamente separada:** aunque las props salen de inference, `NFLAvailabilityService._get_nfl_live_production_lost()` todavía invoca `get_live_injuries("NFL")`, que carga/actualiza ESPN y puede realizar I/O de red. La ingesta ESPN también debe desacoplarse; inference solo lee snapshots locales con provenance/frescura verificada.
3. **Procedencia del event ID:** la lista de prefijos sintéticos no demuestra procedencia; `external_id` sigue siendo campo mixto. Registrar explícitamente proveedor/origen o un `odds_event_id` separado y permitir solo IDs creados/confirmados por la ingesta Odds API. El umbral de kickoff ±30 min requiere justificación de fuente y no sustituye unicidad exacta; discrepancias/colisiones deben quedar sin vínculo.
4. **Resolución de jugador:** el contrato de props sigue usando `player_name`; hace falta resolver a identidad canónica sin ambigüedad y validar equipo/evento/mercado. Nombre duplicado, transliterado o sin crosswalk => mercado no verificado.
5. **Cuota:** cuatro mercados reservan hasta 4 créditos por petición. El sobre de 320 equivale a 80 peticiones conservadoras/mes; leer un slate de 16 partidos una vez consumiría hasta 64. Definir selección de eventos/mercados y cadencia, con dedupe/caché y límites por semana, antes de implementación. No asumir una cuota superior.
6. **Calibración de market_signal:** el historial actual 2020–2024 no incluye snapshots de props Odds API, y el plan gratuito prohíbe backfill histórico. Por tanto no existe todavía un dataset para estimar `P(absence | market_signal)` ni validar Brier out-of-time de esa señal. Mantener `None`/abstención hasta recopilar cohortes prospectivas suficientes; Brier descriptivo de status no valida la señal de mercado.
7. **Cobertura y status:** la especificación de reporte limpio es correctamente conservadora; sin evidencia por equipo-semana, no crear cero. La regla `NON_MARKET_POSITION` tampoco elimina la necesidad de estimar participación/impacto por una fuente validada.

No se ejecutó implementación ni pruebas por ser una revisión de diseño. La entrada anterior del manifiesto confirma que faltan activos actuales 2025/2026 para priors live. Fase 02 permanece `review`, Fase 03 `pending`, producción cerrada. Gemini debe corregir la especificación antes de cualquier cambio de código.

## Implementación Codex — contrato explícito para inference NFL (2026-10-11)

- En `backend/ml/inference.py`, `predict_matchups()` dejó de llamar `FantasyInjuryService`/ESPN. Ahora requiere `availability_by_game` y valida identidad de juego, kickoff, cutoff fijo kickoff−2h, timestamps pre-cutoff, resolver version, equipos exactos, referencias por equipo, coverage state y métricas finitas en [0,1]. `covered_healthy` exige `clean_report_evidence_ref`.
- La metadata de ejecución conserva las source refs y cutoff por partido. Sin snapshot no hay fallback a red ni a ceros: lanza `DataQualityError`.
- Prueba focalizada `tests/test_nfl_availability_phase02.py`: 37 passed, 17 warnings. El subconjunto `-k inference`: 7 passed. Warnings NumPy/Pandas de timedeltas pendientes.
- No se generaron snapshots/productos de datos, no hubo llamadas externas ni consumo de cuota. Los callers actuales aún no generan el contrato; NFL inference queda intencionalmente en abstención hasta integrar un productor verificable.
- Fase 02 sigue `review`; Fase 03 `pending`; producción cerrada. Los bloqueos de event-ID/provenance, fuente live de priors 2025/2026, props históricas/calibración y completitud de reportes siguen abiertos.

## Validación posterior de la corrección de inference NFL — 2026-10-11

- Codex ejecutó en Docker la suite combinada `tests/test_nfl_availability_phase02.py tests/test_prediction_gate.py tests/test_temporal_pipeline.py tests/test_parlay.py tests/test_nfl_data_phase01.py`: **122 passed** en 60.11 s.
- Regresión operativa: `pytest -q -k "not test_exhaustive_idempotency and not test_exhaustive_multiple"`: **170 passed, 2 deselected** en 61.54 s.
- `git diff --check` para los archivos de la corrección no reportó errores. Se verificó que `backend/ml/inference.py` ya no contiene referencias a red ni a `FantasyInjuryService`/`get_live_injuries`.
- SHA-256 de `data/odds_quota_ledger.json`: `13e43e0c675cf6c17946e3b3e03df639dbe2e96ab0786680c4a6d37c9ab028be`; saldo conservado: 275 restantes / 225 usados. No se hicieron llamadas externas.
- Las pruebas emiten 26 warnings deprecados por unidades genéricas de NumPy/Pandas timedelta. No fallan suites; queda pendiente normalizarlos.
- Dictamen: aceptar esta corrección acotada de fail-closed y aislamiento de red, no la Fase 02 completa. No hay productor que construya `availability_by_game`, ni snapshots reales aptos; faltan event linkage de Odds, priors actuales y calibración prospectiva de props. Fase 02 sigue `review`, Fase 03 `pending`, producción deshabilitada.

## Revalidación Gemini y ajustes Codex posteriores — 2026-10-11

- Gemini revalidó en modo lectura: `inference.py` no consulta red ni convierte availability desconocida en cero; snapshot exige identidad de juego/equipos, timestamps pre-cutoff, referencias y evidencia de reporte limpio. Confirmó 122 pruebas combinadas y 170 aprobadas / 2 excluidas en los comandos previos, y hash de ledger intacto.
- Codex corrigió directamente la advertencia de `Timedelta` genérico usando `datetime.timedelta` en `inference.py`, su fixture y la validación de corrida en `predictions.py`.
- Codex detectó y corrigió un problema adicional no cubierto por el dictamen: `evaluate_predictions_task` usaba `as_of = now - 1 día`, incompatible con la frescura de 15 minutos que exige inference. Ahora pasa el timestamp actual. Se agregó prueba en `tests/test_celery_tasks.py` que verifica ese contrato.
- Validación posterior en Docker: `pytest -q tests/test_celery_tasks.py tests/test_nfl_availability_phase02.py tests/test_prediction_gate.py` → **67 passed**, sin warnings, 45.22 s. `git diff --check` limpio.
- Los consumidores NFL aún no disponen de un productor de `availability_by_game`; por ello la corrida no escribe picks NFL y se mantiene fail-closed. `predict_upcoming.py` también requiere el snapshot y debe tratarse como abstención hasta integrar un productor real.
- SHA-256 del ledger: `13e43e0c675cf6c17946e3b3e03df639dbe2e96ab0786680c4a6d37c9ab028be`; 275 créditos restantes; cero llamadas externas.
- Fase 02 sigue `review`; Fase 03 `pending`; producción deshabilitada. No se fabricaron ni ajustaron datos.

## Revalidación Gemini final y regresión operativa reproducida — 2026-10-11

- Gemini revalidó los cambios de frescura Celery, los intervalos de `datetime.timedelta`, el fail-closed por falta de snapshots y los controles de producción; discrepancias: ninguna.
- Codex reprodujo la regresión operativa en `sports_api`: `pytest tests/ -q -k "not test_exhaustive_idempotency and not test_exhaustive_multiple"` → **171 passed, 2 deselected, 0 warnings** en 63.93 s.
- Hash de `data/odds_quota_ledger.json`: `13e43e0c675cf6c17946e3b3e03df639dbe2e96ab0786680c4a6d37c9ab028be`; saldo 275 restantes / 225 usados, sin llamadas externas.
- Fase 02 permanece `review`; Fase 03 `pending`; producción cerrada. Bloqueo funcional restante inmediato: no hay productor ni artefactos cargables que suministren `availability_by_game` real por partido. El worker y el CLI fallan/abstienen sin ellos, evitando picks no respaldados.
## Ajuste de enfoque: modelo NFL base con availability opcional — 2026-10-12

**Corrección de criterio:** la ausencia de un snapshot NFL perfecto no debe bloquear el modelo base de ganador. Sí bloquea el uso de la señal de lesiones para ese juego; no invalida las estadísticas de equipo que sí están observadas.

### Evidencia local sin modificaciones

Codex ejecutó una evaluación temporal de solo lectura sobre load_nfl_games() y build_temporal_dataset() con los Parquet NFL locales; no usó ni imputó features de lesiones y no escribió artefactos:

- 4,398 juegos, fechas 2010-09-19 a 2026-10-05; 26 features de equipo/descanso; holdout temporal final de 824 juegos (2023-10-22 a 2026-10-05).
- Regresión logística: holdout Brier 0.2267, log loss 0.6446, AUC 0.6682, accuracy 0.6299. Brier de folds: 0.2211, 0.2256, 0.2350, 0.2206.
- HistGradientBoosting: holdout Brier 0.2491, log loss 0.7048, AUC 0.6327, accuracy 0.5947; peor que logística en este corte.
- Esto demuestra que existe una ruta de evaluación base con datos locales, no demuestra ventaja contra Vegas ni rentabilidad. No hay odds históricas punto-en-tiempo suficientes para afirmar ROI.

### Arquitectura de trabajo revisada

1. **Modelo base NFL:** entrenar y evaluar con todos los juegos elegibles y estadísticas observadas de equipo. El schema excluye los seis campos de lesiones; no usa ceros para representar ausencia de datos. Puede producir una probabilidad experimental aun cuando injury/props no estén disponibles.
2. **Capa de availability opcional:** añadir únicamente lesiones/props realmente recibidas y asociadas al juego. Ausencia de equipo o prop significa unknown/unavailable, nunca covered_healthy. Si no se puede resolver un jugador o no hay datos previos, se omite su ajuste y se registra la limitación; no se rechaza la probabilidad base.
3. **Dos variantes sin mezclar contratos:** entrenar/evaluar aparte el modelo base y cualquier variante que consuma availability; comparar en el mismo período temporal y cohortes donde la fuente de lesiones exista. Nunca enviar features incompletas a un modelo que las requiera.
4. **Questionable y cuotas:** la consulta de props sigue limitada a jugadores cuestionables/dudosos cerca del partido y al sobre de cuota existente. Si no hay lista, el modelo base sigue operando sin afirmar si jugará; registrar “market signal unavailable”. OUT/IR se usa desde estado de fuente, sin gastar cuota para intentar revertirlo.
5. **Valor contra Vegas:** comparar la probabilidad base con la probabilidad implícita sin vig de la línea actual solo cuando haya una asociación verificable de evento/equipos/kickoff. Como no existe historial de odds en los Parquet, el edge/ROI se medirá prospectivamente; no se declara ventaja basándose solo en AUC/accuracy.
6. **Gobernanza:** esto cambia el bloqueo de producto, no la aceptación del modelo. Todo artefacto nuevo queda experimental, production_enabled: false; antes de habilitar picks se requiere evaluación temporal reproducible, calibración y seguimiento de retorno prospectivo con stake fijo.

**Tarea team-only completada el 2026-10-12:** se implementó el dataset/model variant nfl_team_only, se ejecutó el backtest temporal con datos locales y inference cargó el modelo con el calendario vigente. La siguiente tarea es paper-trading contra cuotas sin vig con seguimiento prospectivo y sin abrir producción.
## Implementación Codex del variant NFL team-only — 2026-10-12

- Añadido schema independiente de 26 features que excluye los seis campos de lesiones en temporal_features.feature_columns(..., include_availability=False).
- build_training_datasets.py admite --variant team_only, genera nfl_team_only_training.csv + manifest usando los juegos nflverse locales sin hacer joins ni imputación de availability.
- train_temporal.py admite --variant team_only y guarda modelo/metadata aparte en backend/ml/models/nfl/team_only. El model version es nfl_team_only_temporal_v1 y production_enabled=false.
- inference.py y los callers CLI/Celery admiten team_only. Ese variant no requiere snapshots ni agrega ceros de lesiones; la metadata indica availability mode not_used_by_model. El variant injury_aware conserva su contrato y rechazo fail-closed.
- El endpoint experimental acepta la identidad explícita de nfl_team_only_temporal_v1; el gate de producción sigue cerrado.
- Ejecución con datos reales locales: builder generó 4,398 juegos (2010-09-19 a 2026-10-05), 26 features, 16 excluidos por historial insuficiente y 13 empates omitidos según contrato. Trainer produjo evaluación temporal: holdout de 824 juegos, LogisticRegression Brier 0.2267 / log loss 0.6446 / AUC 0.6682 / accuracy 0.6299; HistGradientBoosting Brier 0.2491 / log loss 0.7048 / AUC 0.6327 / accuracy 0.5947.
- Smoke de inference real con calendario local: 193 juegos futuros, model_variant=team_only, availability_mode=not_used_by_model, production_eligible=false. Solo resultados en memoria; no se escribió archivo de picks ni filas de DB.
- Suite operativa posterior: 172 passed, 2 deselected, 0 warnings en 98.01 s. Suite enfocada previa de temporal/availability/gate/Celery: 74 passed. El hash del ledger siguió intacto; cero llamadas externas y cero créditos gastados.
- Evidencia predictiva no equivale a edge/ROI. No activar producción. El paso siguiente es comparación paper-trading prospectiva de probabilidades team-only vs línea actual sin vig, junto con un reporte honesto de cobertura de availability opcional. El variant injury-aware queda separado para no contaminar el baseline.

## Implementación y Validación del Flujo Práctico NFL (Fase 02) — 2026-10-12

- **Cadencia de datos (2 capturas ESPN en día de partido):** Implementada en `backend/app/services/nfl_availability_service.py` con `ESPNInjurySnapshot`. Se admiten dos capturas en día de partido (inicio de jornada y cerca de kickoff), ambas como snapshots independientes con `captured_at_utc` en UTC fijado con el reloj del sistema al recibir la respuesta (nunca presentado como fecha de publicación de ESPN). La captura 2 solo afecta predicciones ejecutadas posteriormente a su captura (`as_of >= captured_at_utc`).
- **Equipos omitidos (sin bloqueo de feed ni salud inferida):** En `backend/app/services/fantasy_injury_service.py`, se removieron los umbrales de 28/32 equipos que bloqueaban el feed. Los equipos ausentes en la captura se registran fielmente como `CoverageState.NO_INJURIES_LISTED` ("sin lesiones listadas en esta captura") sin inferir salud (`COVERED_HEALTHY`). El modelo base NFL continúa procesando el partido normalmente.
- **Cruce ESPN-odds (props verificables para Q/D):** En `NFLAvailabilityService.evaluate_market_prop_signal()`, para jugadores Questionable o Doubtful se cruzan las props disponibles para el evento y jugador exactos. La presencia de línea es una señal observada de mercado (`LISTED_AT_CUTOFF`), no certeza de juego. Si no hay línea, se preserva el estado desconocido (`UNKNOWN`) sin interpretarlo como prueba de ausencia. Para bajas confirmadas (`OUT`/`IR`): NUNCA se consultan props.
- **Cuota variable en `OddsQuotaService`:** El estimador de costos aplica reglas V4 de The Odds API (mercados solicitados $\times$ regiones). Pruebas de reserva herméticas aisladas en `tmp_path`, sin tocar el ledger de cuota real.
- **Rendimiento individual e impacto reciente:** `calculate_player_recent_importance()` calcula métricas (PPR, snaps) estrictamente previas al corte (`game_date < as_of`) ligadas a la identidad canónica y al equipo actual. Prohibido transferir métricas de equipos anteriores o sustituir con temporadas antiguas. `pos_rank` entra solo como contexto activo sin multiplicadores fijos. Ante falta de historial reciente, el ajuste se marca desconocido y el modelo base (`team_only`) continúa.
- **Probabilidades fijas purgadas:** Purgadas constantes fijas no respaldadas por distribuciones empíricas.
- **Evidencia en Docker:**
  - `tests/test_nfl_availability_phase02.py`: **44 passed in 65.74s**
  - `tests/test_celery_tasks.py`: **4 passed in 2.44s**
  - Regresión operativa completa: **179 passed, 2 deselected in 83.03s**
  - Ledger The Odds API (`data/odds_quota_ledger.json`): SHA-256 verificado `13e43e0c675cf6c17946e3b3e03df639dbe2e96ab0786680c4a6d37c9ab028be`; 275 créditos restantes; cero peticiones externas reales.
- **Gobernanza:** `production_enabled: false` inmutable. NBA fuera de alcance. Fase 02 continúa en `review`. Fase 03 en `pending`.

## Revalidación Independiente Gemini y Resolución de Hallazgos — 2026-10-12

Auditoría escéptica realizada sobre la primera entrega del flujo práctico. Hallazgos identificados, reproducidos y corregidos:

1. **`NameError: name 'timedelta' is not defined` en `nfl_availability_service.py`:**
   - **Suma:** `build_game_availability_snapshot()` calculaba `cutoff_dt = kickoff_dt - timedelta(hours=2)` sin importar `timedelta` en el encabezado.
   - **Corrección:** Importado `timedelta` desde `datetime`.

2. **`UnboundLocalError: snap_id` en `build_game_availability_snapshot()`:**
   - **Suma:** `clean_report_evidence_ref` intentaba interpolar `snap_id` dentro del bucle de equipos antes de que `snap_id` fuera asignado tras el bucle.
   - **Corrección:** `snap_id` se extrae y define inmediatamente al validar `active_snap`.

3. **Falta de `clean_report_evidence_ref` en `TeamAvailabilitySummary` y `compute_team_production_lost`:**
   - **Suma:** `inference.py` exige que un equipo en `COVERED_HEALTHY` posea `clean_report_evidence_ref`; de lo contrario levanta `DataQualityError`. El dataclass y el cómputo carecían de este campo.
   - **Corrección:** Añadido campo `clean_report_evidence_ref` al dataclass `TeamAvailabilitySummary`, generado con provenance en `compute_team_production_lost` y `build_game_availability_snapshot`.

4. **`NameError: name 'logger' is not defined` en `ml/inference.py`:**
   - **Suma:** Al procesar un equipo con `CoverageState.NO_INJURIES_LISTED`, se invocaba `logger.info(...)` sin importar `structlog`/`logger` en `ml/inference.py`.
   - **Corrección:** Importado `structlog` y declarado `logger = structlog.get_logger(__name__)`.

5. **Fuga temporal y registros antiguos en `calculate_player_recent_importance`:**
   - **Suma:** Si un log carecía de la clave explícita `"season"`, el filtro de temporada era ignorado y se aceptaban partidos de temporadas pasadas (e.g. 2021). Además, `pos_rank_context` aceptaba fechas posteriores al `as_of`.
   - **Corrección:** Cuando `"season"` falta, se deriva de `game_date`. Si la temporada difiere de la activa, se descarta. `pos_rank_context` valida estrictamente `rank_as_of <= as_of_dt`.

6. **Desacople point-in-time en `FantasyInjuryService`:**
   - **Suma:** `FantasyInjuryService._get_nfl_live_production_lost` leía únicamente la caché mutable de `get_live_injuries`, ignorando los snapshots point-in-time guardados en disco.
   - **Corrección:** Consulta prioritariamente `self.nfl_service.get_active_injury_snapshot(as_of=cutoff_dt)` y propaga `strict_unlisted_unknown=True` a `process_player_injury`.

7. **Continuación con baseline ante equipos omitidos en `predict_matchups`:**
   - **Suma:** Cumple el requisito 2 permitiendo que el modelo base `team_only` continúe ante `CoverageState.NO_INJURIES_LISTED`.

8. **Tarea Celery de ingesta de snapshots:**
   - **Suma:** Añadida tarea `capture_nfl_injuries_task` y entradas en `celery_app.conf.beat_schedule` para captura al inicio del día (13:00 UTC) y cerca de kickoff (16:30 UTC).

9. **Evidencia reproducible completa en Docker (`sports_api`):**
   - `pytest tests/test_nfl_availability_phase02.py -v`: **48 passed** en 86.65s (incluye 4 nuevas pruebas exhaustivas).
   - `pytest tests/test_celery_tasks.py -v`: **5 passed** en 2.62s.
   - Regresión operativa: `pytest tests/ -m "not slow" -q`: **183 passed, 1 skipped** en 112.74s.
   - Integridad del ledger: `data/odds_quota_ledger.json` con hash SHA-256 `13e43e0c675cf6c17946e3b3e03df639dbe2e96ab0786680c4a6d37c9ab028be` (intacto, 275 créditos restantes, 0 llamadas externas).


