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
- [x] **TAREA-8 (Fix de Picks Desaparecidos - Modo Preview vs Modo Cohorte):** Restaurada la visibilidad de previews en Dashboard/Matchup, manteniendo la promoción oficial restringida por elegibilidad y correlación exacta con el registro prospectivo. Auditoría Codex aprobada tras endurecer modelo/cutoff/snapshot, `is_stale is False` estricto, y validación de moneylines enteras/finitas/válidas sin redondeo. Ver STATE. Pruebas independientes: 47 focalizadas; suite backend 622 passed, 1 skipped; frontend lint/build con código 0. Ledger intacto, Beat detenido. Commit de cierre: se registra al finalizar la auditoría.

### Prioridad 2: Pronósticos de Totals (Over/Under) para dejar NFL al 100%
- [x] **TAREA-9 (Modelo y Inferencia de Totals NFL):** Implementados el entrenamiento/evaluación temporal e inferencia de `total_points` y Over/Under, con Totals restringido a NFL, sigma OOF por splits expansivos, validación finita fail-closed y estado experimental/no producción mientras OOF no supere baseline. Auditoría Codex aprobada. Suite completa independiente: 665 passed, 1 skipped; `git diff --check` limpio. Ledger y modelo ganador intactos. Ver STATE; commit al cerrar esta auditoría.
- [x] **TAREA-10 (Integración de Totals NFL en API y Frontend):** Integración auditada y aprobada: migración idempotente de `predictions.projected_total` para PostgreSQL/SQLite, prueba PostgreSQL aislada que preserva los datos compartidos, sigma solo OOF/folds de desarrollo, API/UI con Totals experimental y sin EV si faltan cuotas observadas. Suite backend completa independiente previamente: 737 passed, 2 skipped; revalidación focalizada actual: 54 passed, 1 skipped; frontend: 29 passed, lint/build correctos (aviso Vite por chunk de 918.46 kB); higiene de diff limpia. Ledger y modelo oficial intactos, Beat detenido. Ver STATE; commit de cierre registrado al completar el ciclo.

### Prioridad 3: Desacoplamiento Arquitectónico de NBA (Deporte Independiente)
- [~] **TAREA-11 (Auditoría de Fuentes NBA y Pipeline Propio):** La primera entrega no se aprueba. Las pruebas focalizadas pasan (23 passed), pero la auditoría detectó rolling features que cruzan límites de equipo, métricas suplidas por constantes ante datos incompletos, un supuesto fallback ESPN que solo lee datos locales y una prueba que dejó un partido fixture persistido en la BD compartida (`live_espn_99901`). Corregir los hallazgos y revalidar; ver instrucciones exactas en STATE. Sin commit hasta aprobar.
  1. Auditar `nba_ingestion.py`, `nba_schedule_service.py` y `nba_source.py` para verificar endpoints gratuitos, procedencia, límites temporales y errores. Distinguir el endpoint ESPN Scoreboard (actualización diaria; sin contrato/SLA publicado) de los snapshots históricos SportsDataverse verificados; no atribuir disponibilidad en vivo a una función que solo lee parquets.
  2. Diseñar en `temporal_features.py` y `nba_source.py` features diarias NBA (fatiga, Back-to-Back, 3-en-4, Pace y ratings) con agregaciones rodantes estrictamente agrupadas por equipo, `.shift(1)` y datos fuente completos/finitos. Alinear entrenamiento e inferencia y mantenerlas independientes de NFL.