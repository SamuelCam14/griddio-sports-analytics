# Estado de ejecución

Este archivo es el registro compartido entre Codex (orquestador) y Gemini (ejecutor). Gemini debe actualizarlo al inicio y cierre de cada tarea, incluyendo evidencia concreta. No editar estados de fases futuras.

## Estado actual

- **Plan:** `docs/nfl_model_execution/README.md` v1.0
- **Fase activa:** 00 — gobernanza, gate y fotografía reproducible
- **Estado:** `review`
- **Último revisor:** Codex — revisión no aceptada; requiere corrección y nueva entrega
- **Última actualización:** 2026-10-11
- **Checkout/branch:** main
- **Commit base/hash:** 8f158ce450e2e37c25b09f0ee8505ce27a7263a0
- **Cambios preexistentes del usuario:** sí (19 archivos modificados en frontend/; preservados sin tocar)
- **Código cambiado por este plan:** `backend/ml/models/nfl/model_metadata.json`, `backend/ml/training/train_temporal.py`, `backend/app/api/v1/predictions.py`, `backend/tests/test_prediction_gate.py`, `backend/tests/test_temporal_pipeline.py`, `docs/nfl_model_execution/reports/phase-00-baseline.md`
- **Pruebas ejecutadas:** 25 passed en gate y temporal pipeline; 69 passed en suite general
- **Producción:** cerrada (`production_enabled: false` verificado en NFL)

## Revisión Codex de Fase 00

- **Resultado:** `review` — no aceptada todavía.
- **Evidencia reproducida:** `docker exec -e PYTHONPATH=/app sports_api pytest -q tests/test_prediction_gate.py tests/test_temporal_pipeline.py` → 25 passed, 9 warnings.
- **Bloqueo de aceptación:** `backend/app/services/parlay_service.py` mantiene un fallback con cuatro equipos NBA, probabilidades y cuotas codificadas cuando hay menos de dos candidatos reales. `GET /api/v1/parlay/recommended` llama ese servicio sin verificar el gate de producción NFL. Con la base sin candidatos suficientes, la ruta puede generar parlays aparentemente viables a partir de datos ficticios; contradice la regla de no servir picks con el gate cerrado.
- **Siguiente acción requerida:** Gemini debe cerrar este camino con comportamiento fail-closed y agregar cobertura de prueba de servicio/ruta para la ausencia de candidatos y el gate cerrado. No avanzar a Fase 01 hasta que Codex revise la nueva evidencia.
- **Otros cambios de esta revisión:** no se hicieron modificaciones al código de aplicación. La aceptación sigue pendiente.

## Tablero de fases

| Fase | Estado | Evidencia/reporte | Revisión Codex |
|---|---|---|---|
| 00 Gobernanza, gate y baseline | `review` | docs/nfl_model_execution/reports/phase-00-baseline.md | pendiente |
| 01 Fuentes NFL, provenance y quota | `pending` | — | pendiente |
| 02 Lesiones point-in-time | `pending` | — | pendiente |
| 03 Features y dataset | `pending` | — | pendiente |
| 04 Modelos y validación | `pending` | — | pendiente |
| 05 Mercado y EV | `pending` | — | pendiente |
| 06 Backend SSOT | `pending` | — | pendiente |
| 07 Frontend honesto | `pending` | — | pendiente |
| 08 Trial y decisión | `pending` | — | pendiente |

Estados permitidos: `pending`, `in_progress`, `blocked`, `review`, `done`, `deferred`, `failed`. `done` solo después de adjuntar evidencia y aprobación de Codex. `blocked` debe nombrar la dependencia externa o decisión pendiente; no usarlo por conveniencia.

## Registro de corridas y cuotas

Añadir una fila por cada ejecución con cambios o llamadas a Odds API.

| Fecha UTC | Fase/tarea | Commit | Comando/acción | Resultado | Tests/evidencia | Créditos iniciales | Consumidos (`x-requests-last`) | Restantes | Próxima acción |
|---|---|---|---|---|---|---:|---:|---:|---|
| 2026-10-11 | 00 Gobernanza | 8f158ce | docker exec sports_api pytest tests/test_prediction_gate.py tests/test_temporal_pipeline.py | Gate NFL cerrado (false), metadata default False, splitter corregido con assertions | 25 passed (gate/splitter) + 69 passed suite | — | 0 (sin llamadas) | — | Revisión Codex de Fase 00 |

No registrar claves ni secretos. Para peticiones Odds API registrar endpoint, sport, markets, region, evento (si aplica), motivo y cuota real reportada; nunca guardar `apiKey` en logs.

## Decisiones pendientes

- Confirmar en consola/proveedor la fecha de reset y saldo real de la key primaria de 500 créditos.
- Verificar si la key secundaria volvió a operar, su proveedor, límite y fecha de reset. Hasta probarla, saldo presupuestado: **0**.
- Confirmar si existe acceso histórico pagado de The Odds API. El plan de ejecución no presupone ni autoriza comprarlo.
- Definir con el usuario límites de exposición, muestra/horizonte del trial y mercado inicial final antes de empezar fase 08.

## Formato de cierre por sesión

```text
Fase/tarea:
Archivos modificados:
Resumen de cambios:
Evidencia reproducible (comandos y salidas/resumen):
Pruebas ejecutadas y resultado:
Validación de datos/provenance:
Odds API: requests, coste medido y saldo posterior (o “sin llamadas”):
Decisiones tomadas y motivo:
Limitaciones/riesgos pendientes:
Siguiente tarea atómica propuesta:
Estado solicitado: review | blocked | failed
```

