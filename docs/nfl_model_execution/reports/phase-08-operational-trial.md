# Reporte de Ejecución — Fase 08: Trial Operacional, Evaluación Prospectiva y Criterios de Parada (Kill Criteria)

**Fecha de ejecución:** 2026-10-12  
**Alcance:** Exclusivamente NFL.  
**Estado de la fase:** `review` (entregado para auditoría).  
**Entorno:** Docker container `sports_api` (Python 3.12.15). Host: Windows 11.

---

## 1. Marco de Shadow Trial y Congelamiento Operacional

Conforme a `docs/nfl_model_execution/08-operational-trial.md`, se estableció el marco de evaluación prospectiva en la sombra (*shadow trial*) implementado en `app.services.nfl_shadow_trial_service.py`:

1. **Parámetros Pre-registrados Congelados:**
   - **Versión de Modelo:** `nfl_team_only_temporal_v1` (Regresión Logística temporal sobre 26 features de equipo).
   - **Mercado Autorizado:** Exclusivamente moneyline (`h2h`).
   - **Horizonte Operativo Pre-registrado:** 100 selecciones prospectivas como hito de monitoreo y revisión de control (identificado como horizonte operativo pre-registrado, no como regla estadística universal de poder de muestra, ya que detectar ventajas sutiles de mercado requiere muestras sustancialmente mayores).
   - **Estado de Ejecución del Trial:** Trial prospectivo formalmente no iniciado en producción (log limpio con 0 picks artificiales; listo para operar prospectivamente conforme se capturen momios y se liquiden juegos reales observados).
   - **Umbral de Ventaja Mínima:** $\ge 3.0\%$ sobre la cuota justa sin vig.
   - **Pérdida Máxima de Parada (Drawdown Cap):** 15.0 unidades planas.
   - **Reserva Protegida de Cuota:** 75 créditos. Si el saldo restante llega a $\le 75$, el trial se detiene inmediatamente.
   - **Apuestas Automatizadas:** **ESTRICTAMENTE PROHIBIDAS**. La plataforma no se conecta a bookmakers ni envía apuestas automáticas. Samuel conserva el 100% del control sobre cualquier apuesta manual.
   - **Gate de Producción:** Se mantiene cerrado (`production_enabled: false`).

2. **Registro Prospectivo de Selecciones (`ShadowPickRecord`):**
   - Cada recomendación registrada en `data/trial/nfl_shadow_trial_log.json` almacena:
     - `server_pick_id` (hash SHA-256 de evento + selección + versión + timestamp).
     - `presented_at_utc`, `event_id`, equipos local/visitante y selección.
     - `model_prob` y `fair_market_prob`.
     - `bookmaker`, `presented_odds_decimal`, `presented_odds_american`.
     - `ev_per_dollar`, `edge_pct`, `quarter_kelly_stake`.
     - `status: "shadow_logged"`.
     - `closing_odds_decimal` y `clv_pct` ($CLV = (\frac{\text{odds}_{\text{presentada}}}{\text{odds}_{\text{cierre}}} - 1) \times 100$).

---

## 2. Automatización y Verificación de Criterios de Parada (Kill Criteria)

Se implementó el evaluador `evaluate_kill_criteria()` y su integración en vivo `evaluate_live_kill_criteria()` que detienen de inmediato el trial (`kill_immediately`) ante cualquiera de las siguientes condiciones:
1. **Brecha de Cuota:** Saldo de The Odds API $\le 75$ créditos restantes (o archivo de ledger ausente/corrupto).
2. **Pérdida Crítica:** Drawdown acumulado $\le -15.0$ unidades.
3. **Falla de Fuentes:** Ingesta de calendario o resultados caída o corrupta.
4. **Drift de Modelo (con Muestra Mínima $\ge 30$ y Distinción Poblacional):** Brier score móvil superior al baseline mayoritario ($0.2469$), evaluado **únicamente cuando la muestra de picks liquidados alcanza al menos 30 observaciones** (`settled_picks >= 30`) para evitar paradas prematuras por ruido muestral. Se documenta que los picks con EV+ seleccionados proceden de una distribución condicional distinta a la población global incondicional de todos los juegos de la liga; el baseline de 0.2469 opera como cota operacional máxima tras muestra representativa, no como prueba de poblaciones idénticas.
5. **Métricas No Finitas (NaN/Inf):** Ante cualquier métrica corrupta o NaN, evaluación fail-closed inmediata a parada de emergencia.

Además se implementaron:
- `settle_pick()`: Liquidación trazable de recomendaciones con resultado real (1/0), PnL en unidades y recálculo de CLV contra la línea de cierre oficial.
- `compute_trial_summary()`: Resumen ejecutivo del trial (picks totales, pendientes, liquidados, win rate, PnL acumulado, CLV promedio, Brier móvil, progreso vs horizonte de 100 picks).
- `_load_log_entries()`: Recuperación tolerante a fallos si el archivo JSON de disco no contiene un array raíz.

---

## 3. Evidencia de Validación

```bash
docker exec -e PYTHONPATH=/app sports_api pytest -v tests/test_nfl_shadow_trial_phase08.py
```
**Resultado:** 10 passed en 0.85s (cero fallos, cero warnings).
Suite completa Fases 03-08: 37 passed en 8.23s.
Suite de regresión operativa completa: 240 passed, 1 skipped en 110.15s (0 warnings).
Ledger SHA-256 verificado: `13E43E0C675CF6C17946E3B3E03DF639DBE2E96AB0786680C4A6D37C9AB028BE` (275 créditos restantes, 0 llamadas live).
