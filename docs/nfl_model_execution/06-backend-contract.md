# Fase 06 — Backend como fuente única de verdad

## Objetivo

Ofrecer predicciones NFL, valor de mercado, disponibilidad e historial de forma trazable; el request de usuario no dispara un fetch caro/no controlado.

## Arquitectura de ejecución

1. Scheduler/worker: `refresh schedule & source freshness → capture odds/injuries within allowed window → validate snapshots → build point-in-time features → model/calibrator → market/EV → persist output`.
2. API lee predicciones persistidas y su freshness; responde estado y referencias. Evitar llamada externa en cada `GET` de dashboard.
3. Tareas son idempotentes por `event_id + model_version + feature_version + decision_cutoff + odds snapshot hash`.
4. Retries limitados, con backoff, classification de 429 vs 5xx vs auth/quota; no repetir hasta agotar créditos.

## Persistencia requerida

Guardar por prediction: sport, event IDs, target/market/selection, model/calibration/feature versions, artifact/dataset/source manifest hashes, `as_of`, feature vector hash y refs, model probability, market raw/no-vig probability, exact bookmaker/price/line, EV, tier (si se conserva), generated_at, freshness statuses, reasons/skip, settlement/outcome. Player prop snapshots tienen entidad de outcomes por participante/line/side; no encajarlas como una sola columna por juego.

## Respuesta API

Cada respuesta contiene `status` (`available`, `unavailable`, `stale`, `experimental`), `reason_codes`, `generated_at`, `as_of`, fuentes/timestamps, versión del modelo y mercado. Para picks añade precio/bookmaker/market, probabilidad, vig/no-vig benchmark y EV calculados por backend. Si el modelo es experimental o el input obligatorio falta, no devolver pick listo para apuesta.

## Reglas fail-closed

- Production metadata y gate deben ser false por defecto.
- El modelo rechaza feature schema distinto, artefacto/hash no verificable, input after cutoff y probabilidad no finita.
- No usar zero/league-average/default odds para representar input ausente.
- Odds stale, fuente down, book mismatch, player mapping ambiguity o saldo API agotado: persistir motivo y abstenerse.
- Una API secundaria se puede usar solo a través de adapter con misma validación y provenance, nunca combinando values sin identificación.
- `debug=False` fuera de desarrollo. No exponer stack traces, keys, vendor payloads con PII o configuración.

## Frescura y scheduler

- Configurar ventanas en settings, no hardcodear offset UTC−4 ni mantener modelos con snapshots viejos.
- Definir ventana de injuries/props alrededor de decisión (~2h inicial) y recordar timestamp real que devuelve proveedor.
- Persistir historial antes/después para medir actualización.
- Una respuesta vieja puede mostrarse como histórico, pero no como precio utilizable/EV actual.
- Evitar solicitar actualidad durante offseason; mostrar `no_upcoming_events` o `provider_no_data`, no placeholders.

## Aceptación

- E2E con provider fixtures autenticados/sanitizados y storage local demuestra provenance del source al JSON.
- API no consulta provider en cada dashboard call.
- Un fallo de fuente produce estado/skip y no predicción cero.
- Restart/job duplicado no duplica prediction ni cuota.
- Logs de cuota y traces no exponen secretos.

