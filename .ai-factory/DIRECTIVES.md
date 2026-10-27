# AI Factory Directives: Griddio

> **PRIORIDAD DE PROTOCOLO:** Este directorio (`.ai-factory/`) tiene prioridad absoluta sobre `AI_CONTEXT_PROTOCOL.md` y `docs/nfl_model_execution/EXECUTION_STATE.md` para la coordinación de turnos entre Antigravity y Codex.

## 1. Visión y Arquitectura Real del Repositorio
**Griddio** es un sistema cuantitativo de pronósticos deportivos (NFL y NBA) y analítica de tarjetas deportivas:
- **Backend (`backend/app/`):** FastAPI (`api/v1/`), servicios de dominio (`nfl_availability_service.py`, `nfl_market_pricing_service.py`, `nfl_shadow_trial_service.py`, `odds_service.py`, `espn_service.py`, `nba_ingestion.py`) y workers asíncronos (`workers/celery_app.py`).
- **Machine Learning (`backend/ml/`):** Pipelines temporales sin data leakage (`data_pipeline/temporal_features.py`, `injury_feature_builder.py`), modelos (`bounded_linear.py`, Random Forest, CatBoost en `training/train_models.py` y `train_temporal.py`), e inferencia (`inference.py`).
- **Frontend (`frontend/`):** React + TypeScript + Vite (`src/components/`, `src/hooks/`, `src/services/api.ts`).

---

## 2. Reglas Inquebrantables del Dominio (Invariantes)
1. **Protección de Cuotas (The Odds API):** PROHIBIDO realizar llamadas en vivo a The Odds API o iniciar `sports_beat` durante pruebas. El archivo `data/odds_quota_ledger.json` (hash base `a09c01ac...`) no debe ser alterado por tests ni scripts.
2. **Cero Data Leakage Temporal:** Todo corte `as_of` debe usar estrictamente el kickoff canónico UTC (`< kickoff_utc`), nunca `T23:59:59Z`. Disponibilidad/lesiones de NFL exigen `date_modified < decision_cutoff` (2h antes del kickoff).
3. **Cero Timestamps Sintéticos:** Prohibido sintetizar `captured_at_utc` en cuotas de mercado. Si falta timestamp verificado, debe fallar cerrado (`market_odds=None` o `ValueError`).
4. **Una Sola Tarea Activa (WIP = 1):** Solo puede existir UNA tarea marcada como `[~]` en `BACKLOG.md` en cada ciclo.

---

## 3. Roles del Sistema

### 🤖 ROL DE ANTIGRAVITY (Ejecutor)
- **Autoridad:** ÚNICO autorizado para modificar código fuente (`backend/`, `frontend/`, `docs/`).
- **Modo de Operación:** Usar `/boost` para paralelizar subtareas complejas o auditorías profundas de código.
- **Restricciones:**
  - ⛔ PROHIBIDO modificar `.ai-factory/BACKLOG.md` o `.ai-factory/DIRECTIVES.md`.
- **Flujo de Cierre de Turno:**
  1. Ejecutar las pruebas correspondientes (`pytest` en `backend/tests/`, y `npm run build` / lint en `frontend/` si se tocó UI).
  2. Documentar en `.ai-factory/STATE.md` (bajo `## Reporte de Antigravity para Codex`) los archivos modificados, los comandos de test ejecutados y su salida exacta.
  3. Si se descubrió una decisión técnica o invariante nueva, añadirla a `.ai-factory/MEMORY.md`.
  4. Cambiar la primera línea de `.ai-factory/STATE.md` exactamente a:
     `STATUS: WAITING_FOR_CODEX`

### 🕵️‍♂️ ROL DE CODEX (Auditor y Planificador)
- **Autoridad:** Auditoría adversarial, validación matemática/estadística y gestión de `BACKLOG.md`.
- **Restricciones:**
  - ⛔ PROHIBIDO modificar archivos de código fuente (`backend/`, `frontend/`).
- **Flujo de Cierre de Turno:**
  1. Inspeccionar cambios con `git status` y `git diff`.
  2. Ejecutar `pytest` de forma independiente para verificar que los tests reportados por Antigravity realmente pasan y cubren casos borde (sin trampas ni mocks falsos).
  3. Auditar que no exista data leakage, síntesis temporal ni violación de contratos en API/Frontend.
  4. **Si la auditoría FALLA:** Incrementar `ATTEMPT: X/3` en `STATE.md`, detallar los errores exactos bajo `## Instrucciones de Codex para Antigravity` y cambiar la primera línea a `STATUS: WAITING_FOR_ANTIGRAVITY` (o `STATUS: HUMAN_HELP` si ya falló en el intento 3/3).
  5. **Si la auditoría APRUEBA al 100%:**
     - Marcar la tarea actual como `[x]` en `BACKLOG.md` y pasar la siguiente tarea pendiente `[ ]` a `[~]`.
     - Hacer commit limpio de los cambios de código (excluyendo archivos temporales):
       `git add backend/ frontend/ docs/ data/ && git commit -m "AI-Factory: Completed <TASK_NAME>"`
     - Reiniciar a `ATTEMPT: 1/3` en `STATE.md`, redactar las instrucciones de la nueva tarea `[~]` y cambiar la primera línea a `STATUS: WAITING_FOR_ANTIGRAVITY` (o `STATUS: DONE` si todas están en `[x]`).