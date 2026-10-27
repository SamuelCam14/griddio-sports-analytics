# Griddio AI Factory Backlog - Fase 4 (Cierre Integral NFL y Separación de Arquitectura NBA)

## INSTRUCCIONES DEL USUARIO (OBJETIVO ACTUAL):
Ejecutar y auditar secuencialmente (máximo 1 tarea en `[~]` a la vez) las siguientes prioridades:
1. Restaurar la visibilidad de los Picks ("Early Leans" / Previews entre semana) en el Dashboard y Matchup View sin romper las garantías estrictas del registro prospectivo (`NFLProspectiveCohortService`).
2. Implementar el pipeline completo de predicción de **Totals (Over/Under)** para NFL en backend y frontend.
3. Separar arquitectónicamente NBA de NFL (auditar fuentes vivas gratuitas/estables para NBA y diseñar su propio pipeline de features diarias sin clonar las variables semanales de NFL).
4. Respetar estrictamente el bloqueo de `data/odds_quota_ledger.json` (cero llamadas en vivo a The Odds API).

---

## Convenciones de Estado (Regla estricta: máximo 1 tarea en `[~]` a la vez)
- `[ ] Pendiente`: En cola.
- `[~] En Progreso`: Tarea activa en este turno.
- `[x] Completada`: Auditada, aprobada al 100% y con commit en git.

---

## Tareas

### Prioridad 1: Higiene de Git y Restauración de Picks de NFL en el Frontend
- [x] **TAREA-7 (Limpieza Git):** Confirmar que los artefactos temporales (`.agents/`, `backend/catboost_info/`, `scratch/`, `data/odds_quota_ledger.lock`, `.ai-factory/panes.env`) no estén rastreados y permanezcan ignorados. Los cinco objetivos están ignorados y fuera del índice. Tras el incidente de consumo documentado, el responsable autorizó adoptar el ledger fiel actual (303 usados / 197 restantes) como nuevo baseline; DIRECTIVES, MEMORY y los seis tests adversariales quedaron sincronizados. Auditoría completa: 589 passed, 1 skipped; Beat detenido, sin llamadas durante la validación. Cierre registrado en STATE.
- [~] **TAREA-8 (Fix de Picks Desaparecidos - Modo Preview vs Modo Cohorte):** Auditar `predictions.py`, `odds_service.py`, `ev_engine.py`, `AiPredictionList.tsx` y `PickCard.tsx`. Actualmente el endurecimiento fail-closed devuelve `market_odds=None` o bloquea picks cuando faltan días para el partido o las cuotas en caché superan el TTL, dejando el frontend sin picks. Solución:
  1. Mantener el bloqueo estricto fail-closed (`captured_at_utc` verificado y ventana pre-kickoff) **exclusivamente** para el registro en `NFLProspectiveCohortService` (`is_cohort_eligible`).
  2. Para los endpoints del Dashboard/UI, permitir que el sistema calcule y muestre **Picks Preliminares ("Early Lean / Preview")** usando la última cuota válida almacenada en `GameModel` aunque esté fuera de la ventana de 2h o en caché, adjuntando los metadatos `pick_status: "preview" | "official_cohort"` y `status_reason` para que el Frontend muestre los picks hoy mismo con un badge claro ("Early Lean" vs "Pick Oficial Confirmado"). Crear tests de regresión.

### Prioridad 2: Pronósticos de Totals (Over/Under) para dejar NFL al 100%
- [ ] **TAREA-9 (Modelo y Inferencia de Totals NFL):** Extender el entrenamiento temporal de NFL (`train_temporal.py`, `evaluate_common_folds.py`, `inference.py`, `ml_engine.py`) para entrenar y calibrar formalmente la predicción de **Puntos Totales (`total_points = home_score + away_score`)** y la probabilidad de **Over/Under** contra la línea de mercado (`total_line`), guardando sus métricas (MAE de total, Brier de Over/Under) en `model_metadata.json` sin data leakage.
- [ ] **TAREA-10 (Integración de Totals NFL en API y Frontend):** Conectar las predicciones y el cálculo de EV de Totals en `predictions.py` y `MatchupTotalsTab.tsx` / `PickCard.tsx` para que la interfaz de NFL ofrezca pronósticos completos en las 3 pestañas: Moneyline, Spread y Totals. Validar con `pytest` y `npm run build`.

### Prioridad 3: Desacoplamiento Arquitectónico de NBA (Deporte Independiente)
- [ ] **TAREA-11 (Auditoría de Fuentes NBA y Pipeline Propio):** Desacoplar la lógica de NBA de la de NFL:
  1. Auditar `nba_ingestion.py`, `nba_schedule_service.py` y `nba_source.py` para verificar qué endpoints siguen vivos en la temporada actual sin requerir APIs de pago bloqueadas, consolidando el calendario/resultados sobre fuentes estables (como ESPN API / conectores verificados) con manejo estricto de errores.
  2. Diseñar en `temporal_features.py` y `nba_source.py` un conjunto de features exclusivo para la dinámica diaria de la NBA (fatiga de calendario: Back-to-Back, días de descanso, densidad de juegos 3-en-4, Pace, Off/Def Rating rodante con `.shift(1)`), independiente de las métricas de NFL.
