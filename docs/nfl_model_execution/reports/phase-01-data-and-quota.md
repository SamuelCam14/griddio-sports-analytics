# Reporte de Ejecución: Fase 01 — Fuentes NFL, Snapshots y Presupuesto de API

**Fecha:** 2026-10-11  
**Autor:** Gemini (Ejecutor)  
**Revisor de Aceptación:** Codex (Orquestador)  
**Autoridad de Desembolso y Producción:** Samuel  
**Estado de Fase 01:** `done` (revisada, corregida y aceptada por Codex; Fase 02 no iniciada)

---

## Cierre de auditoría Codex — 2026-10-11

La auditoría final encontró y corrigió tres brechas antes de aceptar la fase:

1. La prueba previa de aislamiento entre red y persistencia solo ejercitaba el ledger. Se sustituyó por una prueba del recorrido `fetch_odds()` con cliente HTTP simulado y persistencia forzada a fallar. Se verifica una transacción exitosa, cero reservas pendientes y ningún débito adicional de timeout.
2. Las capturas raw podían colisionar entre endpoints/mercados distintos durante el mismo segundo con el mismo payload, y la ruta idempotente validaba solo un hash guardado. El nombre ahora incorpora identidad de endpoint/parámetros y digest del payload. Un lock compartido serializa escritores, las escrituras usan temporales únicos y la repetición valida el digest recalculado y el contenido canónico. Las capturas corruptas o parciales fallan cerradas sin sobrescribir evidencia existente.
3. El informe solo contaba duplicados exactos, aunque declaraba claves candidatas, y podía sugerir cobertura temporal completa. Ahora valida columnas de clave, cuenta duplicados entre filas con clave completa y expone filas con componentes nulos. Las temporadas se informan como observadas; el reporte no afirma porcentaje de cobertura ni semanas faltantes.

La auditoría regenerada reporta 55 archivos, 1,292,978 filas, 218,535,361 bytes, 107,858,227 celdas nulas, cero duplicados exactos y cero duplicados entre claves completas; 180 filas tienen al menos un componente de clave nulo. Todas las claves candidatas están configuradas, todos los archivos existen y sus hashes coinciden. `dropped_rows: 0` describe la capa raw sin filtrado del lector, no la completitud del upstream.

**Evidencia final reproducida:** suite Fase 01: **45 passed, 1 warning**. Regresión operativa: **133 passed, 2 deselected, 10 warnings**. Los dos casos omitidos requieren el driver opcional `aiosqlite`. El SHA-256 del ledger antes y después de ambas corridas fue idéntico (`13e43e0c675cf6c17946e3b3e03df639dbe2e96ab0786680c4a6d37c9ab028be`); saldo: 275 restantes, 225 usados, 5 transacciones y cero pendientes. No hubo llamadas externas durante la auditoría.

**Aceptación:** Fase 01 completada. `production_enabled` permanece `false`; NBA queda fuera de alcance; la Fase 02 continúa pendiente.

### Revalidación independiente de Gemini

Gemini revisó el código, el reporte y el estado sin modificar archivos ni realizar consultas externas. Reportó cero discrepancias en persistencia append-only, auditoría de claves y prueba integral de `fetch_odds()` ante fallo de disco. Informó 45 pruebas focalizadas (7.52s) y 133 pruebas de regresión, 2 omitidas (13.36s), con el SHA-256 del ledger sin cambios. Esta evidencia es el resultado comunicado por Gemini y se registra separada de la reproducción de Codex.

---

## 1. Resumen Ejecutivo y Resolución de Hallazgos Finales de Codex

En esta iteración de corrección de la Fase 01, se implementaron rigurosamente los 5 requerimientos de aceptación sin realizar llamadas externas a The Odds API ni a fuentes de pago:

1. **Capturas Raw y Canónicas Append-Only, Atómicas e Idempotentes:**
   - **Nomenclatura Append-Only:** El nombre combina timestamp UTC, huella de endpoint/parámetros y digest del payload; solicitudes distintas en el mismo segundo no colisionan.
   - **Escritura Atómica:** Un lock compartido por directorio serializa escritores; se usan temporales únicos y reemplazo atómico de cada archivo.
   - **Idempotencia y Manejo de Permisos:** La repetición recalcula el hash raw y valida los registros canónicos. Capturas existentes corruptas o parciales fallan sin sobrescribir evidencia (`chmod 0o444`).
   - **Pruebas Automatizadas:** Cubierto con pruebas unitarias de retención temporal, idempotencia y permisos en `backend/tests/test_nfl_data_phase01.py`.

2. **Normalización Canónica de Player Props y Desacoplamiento de Errores de Persistencia:**
   - **Estructura Dict Raíz con Bookmakers:** `normalize_odds_response` ahora acepta tanto listas de partidos (`main_lines`) como objetos/dict de partido único devueltos por el endpoint `/events/{eventId}/odds`. Extrae exhaustivamente todos los mercados (`player_pass_yds`, etc.) y resultados (`outcomes`), confirmando que el archivo canónico no queda vacío (`records_count > 0`).
   - **Desacoplamiento Estricto Red vs Persistencia:** En `fetch_odds` y `fetch_real_player_props`, una vez que la respuesta HTTP 200 es recibida, la reserva se concilia inmediatamente (`record_transaction`) y se limpia la variable `reservation = None`.
   - **Aislamiento de Fallos de Disco:** La persistencia raw se encuentra en un bloque `try-except` independiente. Si ocurre un fallo de E/S o permisos al guardar en disco, el error se registra como advertencia pero NO interrumpe la entrega de datos ni vuelve a contabilizar la reserva como timeout incierto.

3. **Reporte Formal de Calidad NFL Completo (`data/nfl_quality_report.json`):**
   - **Métricas Temporales y Cobertura:** 
     - `schedules`: Fechas calendario mínimas y máximas calculadas directamente de `gameday` (`2010-09-09` a `2027-01-10`), cubriendo 17 temporadas (2010–2026).
     - `pbp`: Fechas calendario calculadas directamente de `game_date` (`2010-09-09` a `2026-10-05`), cubriendo 17 temporadas.
     - `players_crosswalk`: Declarado explícitamente como `"not_applicable_atemporal_dimension"` (dimensión global de mapeo de IDs multiplataforma).
     - `player_stats` y `snap_counts`: Declarados como `"weekly_aggregation"` (estadísticas agrupadas a nivel temporada/semana; nflreadpy no expone fecha de calendario individual en estas tablas).
     - `injuries`: Declarado como `"weekly_official_report"` (reportes oficiales de lesiones por semana y temporada).
     - `rosters_weekly`: Declarado como `"weekly_roster_snapshot"` (instantáneas semanales de plantillas por semana y temporada).
   - **Conteo de Celdas Nulas, Duplicados y Filas Omitidas:** Auditados 55 archivos Parquet (1,292,978 filas, 218.5 MB):
     - Total de celdas nulas auditadas: 107,858,227.
     - Total de filas duplicadas exactas: **0**.
     - Total de filas omitidas en capa raw: **0** (`dropped_rows: 0` en todos los archivos).
     - Claves candidatas: cero grupos duplicados entre filas con clave completa; 180 filas tienen al menos un componente de clave nulo y su identidad no se certifica con esa clave.
     - Las temporadas son temporadas observadas; no se informa porcentaje de cobertura ni semanas faltantes.
   - **Procedencia por Asset:** Cada dataset y archivo registra explícitamente su función de librería (`nflreadpy.load_*`), y marca formalmente su URL upstream como `"not_exposed_by_library"` dado que la API pública de `nflreadpy` encapsula internamente las descargas de releases de GitHub.

4. **Reconciliación del Ledger Distinguiendo las Dos API Keys:**
   - **Estructura Reconciliada:** `data/odds_quota_ledger.json` incorpora la sección `reconciliation` preservada en `OddsQuotaService._load_ledger` y `_save_ledger`.
   - **Desglose de Claves:**
     - `key_sha_b5a2c962`: 4 transacciones registradas (Tx 1, 2, 4, 5) con un costo local acumulado de 12 créditos.
     - `key_sha_de093685`: 1 transacción registrada (Tx 3, `f5a11f02f257`) con un costo local de 1 crédito.
   - **Explicación del Salto 6 → 219 en `requests_used`:** Se documenta que la clave `key_sha_de093685` fue introducida con una deuda previa de 218 créditos consumidos externamente en la cuenta del proveedor The Odds API antes de su primera invocación en este ledger. Al ejecutarse la Tx 3, los headers del proveedor reportaron `x-requests-used: 219` y `x-requests-remaining: 281`, sincronizando el saldo acumulado.
   - **Saldo Provisional:** Se conservan intactos los **275 créditos restantes** y **225 créditos usados** como saldo provisional hermético sin consultar al proveedor.

5. **Guarda Temprana en Celery (`ingest_odds_task`):**
   - Al inicio exacto de la tarea en `backend/app/workers/celery_app.py`, se evalúa:
     `if (sport or "").strip().upper() != "NFL": return {"status": "skipped", "sport": sport, "error_type": "sport_not_allowed", "detail": "..."}`
   - Ocurre de forma síncrona antes de importar `AsyncSessionLocal`, antes de instanciar sesiones de base de datos y antes de crear event loops.
   - Verificado con prueba unitaria offline directa en `test_celery_ingest_odds_task_early_guard_rejects_non_nfl`.

---

## 2. Auditoría Técnica: `nflreadpy` vs `sportsdataverse-py`

Se contrastaron las capacidades del paquete instalado `nflreadpy` (v0.1.5) frente a `sportsdataverse-py`:

| Dataset Requerido | `nflreadpy` (v0.1.5) | `sportsdataverse-py` | Veredicto |
|---|---|---|---|
| Schedules | `load_schedules()` | `load_nfl_schedule()` | Idéntica cobertura nflverse |
| Play-by-Play | `load_pbp()` | `load_nfl_pbp()` | Idéntica cobertura nflverse |
| Player Stats (semanal) | `load_player_stats(summary_level="week")` | `load_nfl_player_stats()` | Idéntica cobertura nflverse |
| Snap Counts | `load_snap_counts()` | `load_nfl_snap_counts()` | Disponible desde 2012 en ambos |
| Injuries | `load_injuries()` | `load_nfl_injuries()` | Idéntica cobertura nflverse |
| Rosters Weekly | `load_rosters_weekly()` | `load_nfl_rosters()` | Idéntica cobertura nflverse |
| Player Crosswalk | `load_players()` | `load_nfl_players()` | Contiene GSIS, PFR, ESPN, NFL, Sleeper IDs |

**Conclusión:** `nflreadpy` satisface el 100% de los requerimientos de datos. No se requiere instalar librerías adicionales.

---

## 3. Arquitectura de Presupuesto y Cuota (The Odds API)

Archivo: `backend/app/services/odds_quota_service.py`  
Ledger: `data/odds_quota_ledger.json` (saldo spendable confirmado: 275 créditos restantes)

| Sobre | Límite Máximo | Regla de Activación / Protección |
|---|---:|---|
| **Reserva Protegida** | 75 créditos | Inviolable: si `saldo_efectivo - costo < 75`, la petición es rechazada de inmediato. |
| **Cuotas Principales** | 45 créditos | Hasta 3 ventanas/semana × 3 mercados × 5 semanas. |
| **Props Dirigidas** | 320 créditos | Solo activable bajo scheduler de partido cuando haya jugador Q/D y decisión cercana. |
| **Operación / Reintento** | 60 créditos | Validación, reconciliación y contingencias. |
| **Presupuesto Total** | 500 créditos | Saldo inicial: 500. Saldo provisional actual confirmado: 275 créditos. |

### Circuit Breaker y Reglas Fail-Closed
- Se dispara automáticamente si:
  1. `requests_remaining <= 75` (reserva protegida).
  2. `requests_remaining <= 20%` (saldo <= 100 créditos).
  3. HTTP 429 (`Too Many Requests`) devuelto por el proveedor.
  4. Sobre de props consumido en su totalidad.
- Si el ledger no existe o está corrupto, falla cerrado inmediatamente con `QuotaExceededError`.
- Si una petición sufre timeout tras despacho HTTP, se aplica contabilidad conservadora debitando el costo estimado (`status: "uncertain_timeout"`).

---

## 4. Validación y Evidencia de Pruebas

### 4.1 Suite Focalizada de Fase 01
Comando: `docker exec -e PYTHONPATH=/app sports_api pytest -q tests/test_nfl_data_phase01.py`  
Resultado final reproducido: **45 passed, 1 warning en 10.06s**

Casos cubiertos:
1. Kickoff UTC: EST estándar (UTC-5), EDT verano (UTC-4), partidos prime time (cruce de medianoche UTC), 7 variantes inválidas fallan cerrado con `DataQualityError`.
2. Snapshots: Carga validada con hash SHA-256, crosswalk con IDs canónicos, falla cerrado ante dataset ausente, detección de alteración de bytes, cartelera futura sin `NaT`.
3. Lesiones: Pipeline histórico 100% offline desde snapshots locales, falla cerrado en temporadas no sincronizadas.
4. Presupuesto y Estimación: Costos exactos (/sports=0, 1m/1r=1, 3m/1r=3, 3m/2r=6, historical=bloqueado).
5. Ciclo de Reserva y Ledger: Reserva atómica, deducción por headers oficiales, persistencia en ledger sanitizado.
6. Reservas Protegidas y Sobres: Rechazo al violar reserva de 75 créditos y límite de sobre operativo.
7. Circuit Breaker: Disparo ante saldo bajo, HTTP 429 y consumo completo del sobre de props.
8. Sanitización y Normalizador Canónico: Sanitización de claves en persistencia, esquema estándar para líneas principales, spreads y totals.
9. Fail-Closed de Ledger: Falla cerrado cuando el ledger no existe, falla cerrado con JSON corrupto o campos faltantes.
10. Concurrencia y File Lock: 5 hilos concurrentes reservan cuota simultáneamente sin corrupción ni colisiones.
11. Reinicio y Reservas Pendientes: Las reservas pendientes persisten en disco y se recuperan tras reiniciar.
12. Contabilidad Conservadora ante Timeout: Débito de costo estimado sin reembolso indebido ante timeouts.
13. Rechazo Estricto de Deportes No NFL: `POST /sync` rechaza NBA con HTTP 400; `fetch_odds` rechaza NBA con `ValueError`; `fetch_real_player_props` retorna `[]`; `sync_real_games` retorna `sport_not_allowed`.
14. Idempotencia de Manifiesto: Sincronizaciones repetidas preservan exactamente 1 entrada por dataset/temporada.
15. Reporte de Calidad NFL: Validación de esquema, 55 archivos auditados, fechas mín/máx, nulos, duplicados y limitaciones documentadas.
16. Inmutabilidad de Archivos Raw: Verificación de permisos de solo lectura (`0o444`) en archivos raw y canonical.
17. Retención e identidad append-only: Capturas distintas coexisten aun con el mismo segundo; una repetición exacta es idempotente y datos existentes dañados fallan cerrados sin sobrescritura.
18. Normalización de Player Props con Raíz Dict: Payload de partido único con `bookmakers` normalizado en registros canónicos (`records_count > 0`).
19. Separación de Error de Red vs Persistencia: Recorrido `fetch_odds()` con HTTP simulado y fallo de disco; no duplica débitos ni marca timeout.
20. Unicidad de claves candidatas: Verifica columnas configuradas, duplicados entre claves completas y componentes nulos.
21. Sobre de props agotado: El circuit breaker rechaza llamadas posteriores.
22. Reconciliación del Ledger por Clave: Preservación de metadatos de las 2 API keys y saldo provisional de 275.
23. Guarda Temprana en Celery: `ingest_odds_task(sport="NBA")` retorna `status: "skipped"` sin llamadas a base de datos ni red.
24. Aislamiento Hermético: Verificación de que el ledger de producción no fue alterado por las pruebas (saldo permanece en 275).

### 4.2 Suite Operativa Completa
Comando: `docker exec -e PYTHONPATH=/app sports_api pytest -q -k "not test_exhaustive_idempotency and not test_exhaustive_multiple"`  
Resultado final reproducido: **133 passed, 2 deselected, 10 warnings en 18.49s**

*(Los 2 tests omitidos corresponden al driver opcional `aiosqlite` no instalado en la imagen base del contenedor).*

---

## 5. Estado del Repositorio y Entrega

- La Fase 01 se encuentra terminada y verificada contra todos los hallazgos de aceptación de Codex.
- El repositorio mantiene la habilitación de producción estrictamente cerrada (`production_enabled: false`).
- Los 19 archivos frontend preexistentes del usuario se conservaron 100% intactos.
- `EXECUTION_STATE.md` registra la aceptación `done` de Fase 01. No se ha avanzado a Fase 02.
