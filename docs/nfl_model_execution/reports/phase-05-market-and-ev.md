# Reporte de Ejecución — Fase 05: Mercado, Pricing y Valor Esperado (EV)

**Fecha de ejecución:** 2026-10-12  
**Alcance:** Exclusivamente NFL.  
**Estado de la fase:** `review` (entregado para auditoría).  
**Entorno:** Docker container `sports_api` (Python 3.12.15). Host: Windows 11.

---

## 1. Contrato de Precio y De-vigging

Conforme a `docs/nfl_model_execution/05-market-and-ev.md`, se implementó el motor `NFLMarketPricingService` (`app.services.nfl_market_pricing_service.py`), formalizando:
1. **Contrato de Precio Inmutable (`PriceRecord`):**
   - Requiere `event_id`, sportsbook/bookmaker, `market_key`, `home_team`, `away_team`, `home_odds_american`, `away_odds_american`, `source_timestamp_utc`, `fetched_timestamp_utc`, `decision_cutoff_utc`, `market_status`, y `mapping_confidence`.
2. **Eliminación de Vig (De-vigging):**
   - Remoción proporcional del sobreprecio de las casas de apuestas para obtener probabilidades justas de mercado:
     $$p_{\text{fair}} = \frac{p_{\text{raw}}}{p_{\text{home\_raw}} + p_{\text{away\_raw}}}$$
   - Se demostró matemáticamente y mediante pruebas que $p_{\text{home\_fair}} + p_{\text{away\_fair}} = 1.0$ de manera exacta.
3. **Cálculo de Valor Esperado (EV):**
   - $EV = p_{\text{model}} \cdot (\text{odds}_{\text{decimal}} - 1) - (1 - p_{\text{model}})$ por dólar arriesgado.
   - Umbral de elegibilidad: solo selecciones con $EV > 0$ y ventaja $\ge 3.0\%$ sobre la probabilidad justa de mercado son consideradas elegibles.
4. **Dimensionamiento Conservador (Quarter-Kelly):**
   - Se aplica un factor fraccional de Kelly del 25% ($0.25 \times f^*$) con tope estricto del 5% del bankroll ($0.05$). Las apuestas con ventaja negativa reciben un stake exacto de 0.

---

## 2. Política de Datos Históricos vs Prospectivos

En estricto cumplimiento con la directriz de Codex y el plan:
> *"Sin histórico verificable suficiente, iniciar archivo prospectivo ahora y dejar ROI como `not_evaluable`; no reconstruir cuotas desde resultados ni completar datos de mercado por interpolación."*

- **Estado de ROI Histórico:** Marcado explícitamente como `not_evaluable` en todas las evaluaciones.
- **Naturaleza de Pricing y Trial:** Operan formalmente como utilidades prospectivas independientes. El trial prospectivo se encuentra formalmente **no iniciado** (0 selecciones simuladas/artificiales; registro listo para operar prospectivamente conforme se capturen momios y se liquiden juegos reales observados).
- **Cero Llamadas a APIs de Pago:** No se consumieron créditos ni se consultó The Odds API histórico. El ledger permanece inalterado en 275 créditos restantes con hash SHA-256 verificado.
- **Cero Datos Sintéticos:** No se fabricaron selecciones retrospectivas ni se interpolaron líneas pasadas para simular un pipeline completo. La inferencia y el pricing en vivo se conectan exclusivamente a momios observados en la base de datos local y predicciones experimentales reales del modelo (p. ej. Bills @ Rams con momios observados de DraftKings y FanDuel).
- **Fail-Closed Temporal:** Se implementó el rechazo estricto de momios capturados después del corte de decisión (`odds_post_cutoff`), mercados suspendidos (`market_suspended`) o mapeos con baja confianza (`low_mapping_confidence`).

---

## 3. Evidencia de Validación

```bash
docker exec -e PYTHONPATH=/app sports_api pytest -v tests/test_nfl_market_ev_phase05.py
```
**Resultado:** 6 passed en 0.67s.
