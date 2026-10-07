# Traspaso de continuidad: datos reales y modelos NBA/NFL

**Fecha de corte:** 2026-10-01 (UTC)  
**Repositorio de aplicación:** `/home/taikami/Documents/Progreso/taikami_sports/sports_analytics`  
**Objetivo permanente:** construir un flujo reproducible desde fuente pública hasta predicción deportiva, con procedencia verificable, features point-in-time, cero valores inventados, entrenamiento cronológico y separación estricta entre resultados experimentales y producción.

Este documento es el traspaso operativo para que otro agente continúe sin depender de la conversación. Describe evidencia del estado observado en la fecha indicada; los datos y calendarios externos cambian. No interpreta métricas como rentabilidad ni declara terminado el sistema.

## 1. Resumen de estado

| Área | Estado observado | Evidencia / límite |
|---|---|---|
| NBA histórico | Implementado | Snapshots SportsDataverse/ESPN: temporadas fin 2022–2026; dataset 6,137 filas utilizables, fechas 2021-10-22 a 2026-04-12. La temporada más reciente termina en abril: al arrancar 2026–27 la forma disponible aún es del curso anterior. |
| NFL histórico | Implementado | nflverse por `nflreadpy`: temporadas 2010–2026; dataset 4,382 filas utilizables, fechas 2010-09-19 a 2026-09-28. Se excluyen 13 empates del target binario; los empates sí pueden estar en el historial como resultado 0.5. |
| Snapshots/provenance | Implementado | Manifiestos guardan archivos relativos, fecha de descarga, SHA-256 y referencia fuente; datasets y outputs tienen hash. Los archivos bajo `data/` están ignorados por Git y no deben confundirse con artefactos versionados. |
| Features temporales | Implementado, necesita auditoría independiente | Builder compartido por entrenamiento/inferencia; cutoff `as_of`, partidos del mismo día no contaminan el entrenamiento. Falta una revisión exhaustiva de cada campo/caso límite (UTC, días dobles, playoffs/temporadas). |
| Entrenamiento | Implementado experimental | Regresión logística y HistGradientBoosting, cuatro folds temporales. Métricas están en `model_metadata.json`; producción sigue desactivada. No existe evidencia de ROI porque no se conectaron cuotas históricas timestamped. |
| Scoring futuro | Ejecutado | Corrida `20261001T210143Z`: NBA 1,200 juegos, NFL 223 juegos. Seis NBA Cup TBD excluidos por equipos no anunciados. Hash CSV coincide con manifiesto. Artefactos expresamente `production_eligible=false`. |
| API experimental | Implementada | Endpoint distinto de picks lee el último CSV/manifiesto, verifica SHA y frescura, y entrega probabilidades sin odds/EV/tier. Revisar manualmente frescura y expiración ante cada despliegue. |
| API de producción | Cerrada por diseño | `/today` no ofrece picks con `production_enabled=false`; `/game/{id}` y `/simulate` devuelven no disponible/503 bajo ese gate. Persistencia canónica de predicciones no completada. |
| UI | Ajustada | Ya no debe comunicar las afirmaciones heredadas de ROI/PREMIUM como reales; mostrar etiqueta experimental y métricas de validación. |
| Legacy/semillas | No se ha certificado todo el repositorio | Hay reglas/documentación que prohíben simular; aún se requiere inventario global y prueba de que ningún job/endpoint paralelo usa datos sintéticos o CSV legacy. |
| Licencia | No resuelta para producto comercial | Acceso gratuito/público no concede por sí mismo derechos comerciales. Revisar licencia de cada dataset, condiciones de redistribución y uso de datos ESPN antes de publicación comercial. |

**Evaluación:** el flujo de datos y modelo temporal está conectado a fuentes reales y se ejecuta, pero no es todavía una máquina de predicción validada para uso operativo. No activar `production_enabled`. “No hardcode” aplica a la ruta nueva; todavía falta probarlo en todas las rutas históricas/producto.

## 2. Fuentes y alcance reales

### NBA

- Adaptador: `backend/ml/data_pipeline/nba_source.py`.
- Fuente consultada: ficheros públicos de SportsDataverse que distribuyen schedule y boxscore de equipo de ESPN; no es una promesa de SLA de ESPN ni un acceso oficial garantizado.
- Calendario futuro en flujo separado: `data/raw/nba_espn_upcoming/manifest.json`.
- Histórico descargado: temporadas con año de cierre 2022, 2023, 2024, 2025 y 2026 (temporadas 2021–22 a 2025–26).
- Campos relevantes: marcador/equipos/fecha y estadísticas de equipo observadas; features actuales usan FG%, rebotes ofensivos, rebotes totales, pérdidas y forma de ventana de 10 partidos, junto con resultados/diferencial, descanso y back-to-back según el contrato del builder.
- Schedule 2026–27 observado con 1,206 entradas originales; 6 juegos con IDs/equipos TBD (`teams_not_announced_by_source`) no son puntuables. El CSV incluye 1,200.
- Límite de cobertura: última fecha histórica 2026-04-12. Aunque la fuente responda, esto no proporciona forma actual de una temporada que aún no empezó.

### NFL

- Adaptador: `backend/ml/data_pipeline/nfl_source.py`.
- Fuente: schedules y play-by-play NFL de nflverse, biblioteca `nflreadpy`; fuente upstream gratuita sujeta a licencias y cambios.
- Dependencia que se agregó para el build: `nflreadpy>=0.1.5` y su uso necesita `polars>=1.0.0` (confirmar pins y lock antes de producción).
- Temporadas descargadas: 2010–2026 inclusive. El sync de temporada nueva debe fusionar con el manifiesto existente, no sobreescribir años previos.
- Features: EPA/play y tasa de éxito ofensiva; EPA/tasa permitidos defensivamente; forma de últimos 10. Schedules y PBP deben enlazarse por equipos normalizados y claves de partido.
- Aliases de franquicias históricas observadas: OAK→LV, SD→LAC y STL→LA, para alinear nombres/códigos en el join. Revisar si existen otros cambios necesarios antes de ampliar datos.
- Temporada 2026 observada hasta 2026-09-28; el schedule produjo 223 partidos aún por jugar para la salida futura al corte.

### Lo que no está conectado

- No hay fuente histórica gratuita de cuotas con capturas prepartido timestamped suficientemente auditada; no hay evaluación real de ROI, EV, CLV ni picks.
- No se consumen lesiones, alineaciones confirmadas, métricas individuales ni cuotas como features del modelo actual. No mostrarlas como explicación usada por el algoritmo.
- NBA API del paquete `nba_api` no forma parte de la nueva ruta canónica. No reinstalarlo sin evaluar compatibilidad de dependencias y disponibilidad de endpoints: la fuente activa NBA es SportsDataverse/ESPN.
- No hay calibración post-hoc validada ni umbral de decisión aprobado para producción.
- No asumir que endpoint público vivo en una prueba única garantiza disponibilidad futura. Registrar fecha, URL/referencia, status, payload y hash en cada sincronización.

## 3. Integridad, ausencia y reglas que deben preservarse

1. No fabricar scores, equipos, jugadores, lesiones, odds, estadísticas, fechas ni probabilidades fallback.
2. Campo no recibido: conservar `null`/ausente o rechazar la fila si es obligatorio; registrar causa y conteo. Nunca convertir ausencia a cero, media de liga, neutral, valor de casa o simulación.
3. No declarar sync exitoso con dataset vacío después de error HTTP, excepción de proveedor o schema desconocido.
4. Snapshots raw son inmutables; la descarga nueva produce archivo/manifiesto trazable, no reescritura silenciosa. Validar SHA antes de construir.
5. Fechas de observación distintas de fecha de evento. Una feature para partido objetivo sólo puede usar información publicada antes del cutoff. Para entrenamiento, partidos de la misma fecha se agregan al historial después de crear features para todos los objetivos de ese día.
6. Una estadística final del juego puede ser target o dato histórico posterior, nunca feature pregame del mismo juego.
7. Empates NFL no se deben etiquetar arbitrariamente como victoria/derrota. Política actual: excluidos de clasificación binaria; conservar y documentar el conteo.
8. Dataset/modelo/output identifican hashes y features. Si hashes, clases, schema, fuente, frescura o metadata no coinciden, fallo cerrado.
9. Predicción experimental no equivale a consejo ni pick. No construir EV sin cuotas reales y capturadas antes del partido.
10. Antes de modificar valores nulos, aliases o elegibilidad, agregar regla documentada y prueba que pruebe su origen, no heurística silenciosa.

## 4. Estado del modelo y números reproducibles

Artefactos: `backend/ml/models/{nba,nfl}/`; metadatos: `model_metadata.json`. El dataset y metadata son dependientes de la corrida y deben recalcularse tras cada sync/build. Números observados el 2026-10-01:

| Deporte/modelo | Accuracy media | AUC media | Brier medio | Log loss medio | Brier mejor que baseline en todos los folds |
|---|---:|---:|---:|---:|---|
| NBA Logistic | 0.655532 | 0.704934 | 0.216820 | 0.624902 | Sí |
| NBA HistGradientBoosting | 0.636740 | 0.677236 | 0.228003 | 0.653676 | Sí |
| NFL Logistic | 0.630074 | 0.673767 | 0.227086 | 0.646025 | Sí |
| NFL HistGradientBoosting | 0.597009 | 0.648583 | 0.242646 | 0.688386 | Sí según metadata actual |

Estos son promedios de 4 folds de validación walk-forward, no holdout final independiente, no valor esperado de apuestas y no evidencia de estabilidad fuera de muestra. Diferentes folds cubren distintas temporadas/volúmenes. La decisión “mejor que majority Brier” es baseline limitado; antes de aceptar modelo, comparar también con probabilidad de mercado sin vig si se obtiene captura histórica apropiada, calibración, intervalos y estabilidad por temporada. Aunque metadata marca NFL HGB mejora Brier en folds, logistic presenta mejores promedios; no declarar “modelo ganador” sin regla preespecificada.

Los tests no certifican veracidad de la fuente externa; certifican contratos y lógica local.

## 5. Última corrida y hashes

Corrida experimental terminada, `as_of=2026-10-01T21:01:43.632226+00:00`, modelo `nba_temporal_v1` / `nfl_temporal_v1`, ventana de forma 10, regla: juegos completos anteriores a la fecha objetivo. Se verificó que SHA256 del CSV local coincide con `output_sha256` del manifiesto.

| Salida | Juegos | Hash SHA-256 CSV | Elegible producción |
|---|---:|---|---|
| `data/predictions/experimental/nba_predictions_20261001T210143Z.csv` | 1,200 | `71f135828a3d77094d983ac353964524020cfd835f162641baaeb6587716a59f` | No |
| `data/predictions/experimental/nfl_predictions_20261001T210143Z.csv` | 223 | `e41066faad3e5e08bafec880c897347d948b3e168792721ef58fb41fa36fcd75` | No |

Manifiestos del mismo nombre `.manifest.json` registran hash de dataset y de manifiesto, artefacto, schedule, fuentes históricas, schedule_retrieved_at, as_of, conteo de incompletos y `production_eligible:false`. Los datos bajo `data/` están excluidos de control de versiones: para que otro equipo reproduzca los hashes tiene que ejecutar sync o recibir los snapshots por un canal aprobado; no suponer que `git clone` incluye los parquets/CSVs.

## 6. Archivos de la ruta canónica

| Archivo | Responsabilidad actual |
|---|---|
| `backend/ml/data_pipeline/common.py` | Paths portables host/Docker, manifests, SHA/verificación compartida. |
| `backend/ml/data_pipeline/nba_source.py` | Sync y lectura de snapshots NBA schedule/boxscore, incluyendo schedule futuro. |
| `backend/ml/data_pipeline/nfl_source.py` | Sync/read de nflverse schedules/PBP y normalización de franquicias. |
| `backend/ml/data_pipeline/temporal_features.py` | Construcción compartida de features históricas y matchup con cutoff. |
| `backend/scripts/sync_training_sources.py` | Descargas raw NBA/NFL. |
| `backend/scripts/build_training_datasets.py` | Validación, targets/features y dataset más manifest. |
| `backend/ml/training/train_temporal.py` | Walk-forward, modelos y metadata reproducible. |
| `backend/ml/training/train_models.py` | Wrapper de compatibilidad al entrenamiento temporal. |
| `backend/scripts/sync_upcoming_schedules.py` | Refresco de calendarios no finalizados. |
| `backend/scripts/predict_upcoming.py` | Scoring offline experimental y manifests; requiere flag explícito. |
| `backend/ml/inference.py` | Comprueba artefacto, clases, features, dataset/raw provenance; rechaza metadata no aprobada salvo flag experimental. |
| `backend/app/api/v1/predictions.py` | Gate de endpoints antiguos y salida separada experimental hash/freshness checked. |
| `frontend/src/...` | Etiquetado UI experimental; revisar archivos reales si se cambia el producto. |
| `backend/tests/` | Contratos/test unitarios del pipeline y APIs; ver límites en §7. |

En cada archivo de la tabla, confirmar ruta/estado en el checkout antes de editar: el repositorio de aplicación, y no este documento, es autoridad del código.

## 7. Validaciones ejecutadas al corte

Comandos ejecutados desde la raíz del proyecto (Python del venv del backend):

```bash
PYTHONPATH=backend backend/.venv/bin/pytest -q backend/tests -p no:cacheprovider
PYTHONPYCACHEPREFIX=/tmp/sports-pycache PYTHONPATH=backend backend/.venv/bin/python -m compileall -q backend/ml backend/scripts backend/app/api/v1/predictions.py
```

Resultado observado: `34 passed in 1.32s`; `compileall` terminó código 0. Se verificaron los dos hash de CSV de esta corrida contra sus manifests. Build frontend `npm run build` había terminado correctamente en una corrida anterior tras el cambio de UI, con advertencia de chunk grande (Vite ~728 kB, gzip ~209 kB); repetir desde `frontend/` después de cambios. La prueba y compilación son evidencia del checkout al corte, no garantía para cambios posteriores.

Manual anterior a la última corrida: API experimental devolvió juegos para ambos deportes y `get_model_info` señaló `predictions_enabled=false`. Debe repetirse API smoke test con manifiestos frescos antes de desplegar o considerar esto estado actual de disponibilidad; pytest cubrió firma/hash, pero no sustituye una ejecución del servidor completo ni prueba HTTP con configuración de despliegue.

## 8. Cómo reproducir, en orden seguro

Desde raíz del repositorio. Revisa `--help` de cada script antes de copiar comandos; son los invocados/documentados en el plan y el CLI puede cambiar.

```bash
# Confirmar origen / entorno
cd /home/taikami/Documents/Progreso/taikami_sports/sports_analytics
PYTHONPATH=backend backend/.venv/bin/python backend/scripts/sync_training_sources.py --help

# Sync histórico NBA y NFL (snapshots y manifests)
PYTHONPATH=backend backend/.venv/bin/python backend/scripts/sync_training_sources.py --sport nba --seasons 2022 2023 2024 2025 2026
PYTHONPATH=backend backend/.venv/bin/python backend/scripts/sync_training_sources.py --sport nfl --seasons 2010 2011 2012 2013 2014 2015 2016 2017 2018 2019 2020 2021 2022 2023 2024 2025 2026

# Construcción y evaluación
PYTHONPATH=backend backend/.venv/bin/python backend/scripts/build_training_datasets.py --sport all
PYTHONPATH=backend backend/.venv/bin/python backend/ml/training/train_models.py --sport all

# Schedule separado y scoring sólo explícitamente experimental
PYTHONPATH=backend backend/.venv/bin/python backend/scripts/sync_upcoming_schedules.py --sport all
PYTHONPATH=backend backend/.venv/bin/python backend/scripts/predict_upcoming.py --sport all --allow-experimental

# Guardas locales
PYTHONPATH=backend backend/.venv/bin/pytest -q backend/tests -p no:cacheprovider
PYTHONPYCACHEPREFIX=/tmp/sports-pycache PYTHONPATH=backend backend/.venv/bin/python -m compileall -q backend/ml backend/scripts backend/app/api/v1/predictions.py
```

Nota: `--seasons` corresponde al año final de temporada NBA; en nflverse significa temporada NFL. A partir de cada jornada NFL, sync temporada 2026 y luego reconstruir/reentrenar antes de scoring. La salida de schedule no produce boxscore NBA nuevo: cuando comience temporada, incorporar boxscores finales de fuente histórica y reconstruir. No usar el calendario como indicio de que las características ya están actualizadas.

Durante reproducción, conservar logs y comprobar que los manifests listan los años solicitados; comparar conteos contra temporada/fuente, inspeccionar nulos/duplicados, validar checksum y confirmar fechas máximas. Si descarga da cero filas o estructura distinta, detenerse y registrar fallo en vez de seguir al entrenamiento.

## 9. Roadmap restante con dependencias y aceptación

### P0 — Revisión de seguridad del modelo antes de cualquier exposición

- **Estado:** Completado, con evidencias acotadas (2026-10-01).
- **Evidencia:** 
  - Se modificó y probó el gate `_production_model_enabled` en `backend/app/api/v1/predictions.py`.
  - Se añadieron y pasaron (`13 passed`) pruebas exhaustivas en `backend/tests/test_prediction_gate.py`.
  - Las pruebas ahora validan el rechazo activo (HTTP 404 o 503) de: frescura (`as_of` y `schedule_retrieved_at`), checksum incorrecto, modelo desconocido (`model_version` no admitida), manifiesto ausente, y un CSV "truncado" (que carezca de columnas obligatorias críticas como `home_win_probability`).
  - `/today` y `/game/{id}` rechazan servir predicciones; `/simulate` está comprobado unitariamente para fallar cerrado (HTTP 503) cuando la producción no está explícitamente aprobada.
- **Aceptación / Límite:** Los endpoints expuestos son seguros frente a los casos límite comprobados; no hay predicción como "vacía exitosa" y la producción queda desactivada. Sin embargo, "modelo seguro" asume que estas guardas permanecerán activas; se necesita revisión continua si cambian las rutas.

### P1 — Auditoría independiente temporal y de calidad

- **Estado:** Completado, con evidencias acotadas (2026-10-01).
- **Evidencia:** 
  - La lógica en `backend/ml/data_pipeline/temporal_features.py` procesa por bloque de `game_date` (usando la fecha estricta, descartando la hora) y actualiza el historial *posteriormente*. Esto evita fugas entre juegos compartiendo la *misma fecha declarada* (e.g. doubleheaders bajo la misma fecha).
  - Pruebas lógicas (`test_temporal_pipeline.py`) comprueban este límite y el rechazo a la imputación de nulos.
  - El reporte actualizado `data/quality_report.json` vía `generate_quality_report.py` verifica independientemente los hashes:
    - **NBA:** 6,137 filas, 0 nulos, el SHA-256 local coincide con su manifiesto; `feature_max_observed_game_date_valid: true`.
    - **NFL:** 4,382 filas, 0 nulos, el SHA-256 local coincide con su manifiesto; `feature_max_observed_game_date_valid: true`.
- **Aceptación / Límite:** Cero leak comprobado *a nivel de la granularidad de la fecha del dataset (`game_date`)*. Se debe advertir que esto no garantiza corrección si la fuente reportara incorrectamente un partido transatlántico o de huso horario cruzado en la fecha "equivocada"; asume que el agrupamiento por fecha fuente es cronológicamente monótono. La ausencia de imputación es demostrada por revisión de código (uso de `errors="coerce"` sin relleno), no detectada por el reporte JSON, que solo muestra cero nulos resultantes.

### P2 — Contratos canónicos y persistencia

- **Estado:** Estructura completada y mitigada; pendiente validación final en PostgreSQL y de la integración de inputs (2026-10-01).
- **Evidencia:** 
  - Modelos actualizados en `backend/app/models/game.py`: `Game` añade variables de rastreo (`source_ref`, `observed_at`, etc.).
  - Para `Prediction`, se asignó la **clave determinista estricta por corrida**: `UniqueConstraint("game_id", "model_version", "artifact_sha256", "inference_input_sha256")`. La nueva variable `inference_input_sha256` aísla los cambios dinámicos del input.
  - Funciones de persistencia en `backend/app/services/persistence.py`: valida la presencia incondicional de toda la clave. Utiliza `ON CONFLICT DO NOTHING`.
  - El DDL de migración (`backend/scripts/migrate_p2_fields.py`) superó los huecos lógicos previos:
    1. **Archivo Legacy Consistente**: Emplea una tabla única por migración (`predictions_legacy_p2_migration`) aislando el historial del riesgo de esquemas evolutivos dispares en re-ejecuciones.
    2. **Validación Exacta de Constraints**: Consulta el catálogo exhaustivamente (`pg_constraint`, filtrado por `pg_namespace = current_schema()`), previniendo choques por nombres cruzados. Además, elimina el constraint heredado (`uq_prediction_game_model`) en todos los caminos.
  - Pruebas unitarias (`test_persistence.py` - 5 passed) reafirman las aserciones de fallos y validan la cláusula AST `DO NOTHING`.
- **Aceptación / Límite:** 
  - **Límite DDL:** Debido a la carencia de acceso local al contenedor de la base de datos PostgreSQL, la confirmación empírica final del DDL sobre el esquema de integración del usuario queda abierta y es obligatoria antes de aplicarse definitivamente.
  - **Límite Productor de Inferencia:** Si bien `inference_input_sha256` está protegido y asertado en persistencia y pruebas, aún no existe un productor que lo calcule activamente en base a las features dinámicas; temporalmente, se debe inyectar manualmente. Esto será integrado conforme las fuentes subyacentes maduren en P3/P4.
  - Mantenemos `production_enabled=false`.

### P3 — Operación de fuentes y frescura

- Manejar timeout, HTTP status/429, retry acotado/backoff, schema drift, error parcial y logging estructurado; health/status por fuente y última descarga correcta.
- Automatizar temporada NFL activa y calendario NBA. Schedule y resultados finales son sincronizaciones separadas. Configurar alertas por atraso y fallo persistente.
- Aceptación: fallo de proveedor explícito, no cero filas engañoso; reintento no duplica; refresh deja hashes/fecha/conteos observables.

### P4 — Evaluación de modelo seria

- Fijar una regla de selección/criterios antes de evaluar test final. Comparar logística y boosting contra baseline de tasa y, sólo con snapshots adecuados, línea de mercado sin vig.
- Separar train, validación walk-forward y test final temporal intacto; comparar por temporada/segmento; Brier, log loss, AUC, calibración/reliability y rangos de incertidumbre. Considerar dataset shift y cobertura baja.
- Calibrar sólo sobre partición separada; versionar calibrador y comprobar con datos posteriores. No mover a producción por superar majority baseline.
- Aceptación: reporte reproducible y aprobado por criterio preacordado, volumen mínimo explícito, sin test leakage. No prometer ninguna métrica antes de la corrida real.

### P5 — Cuotas y mercados (sólo si el producto requiere EV)

- Investigar proveedor de cuota gratuito con autorización/licencia adecuada y capturas históricas timestamped. Registrar books, market, line, odds, fetched/observed time y cierre; evitar reconstruir cuotas históricas desde resultados.
- Si no se obtiene fuente apropiada, retirar EV/ROI del producto y mantener sólo probabilidad clasificada. No usar The Odds API gratis como fuente histórica si el plan no ofrece archivo histórico suficiente.
- Aceptación: muestras prepartido verificables por evento, mercado y bookmaker; reglas para pushes/voids; benchmark de closing line y ROI sólo en holdout temporal con política de staking explícita.

### P6 — Inferencia y persistencia operacional

- Crear job con `fetch/check freshness → features(as_of) → inference → persist result+provenance`; deduplicar y registrar motivo de skip. No hacer fetch bloqueante no controlado dentro de request de API.
- Validar request/respuesta y model version, feature schema, raw refs, dataset/artifact/output hashes. No cargar automáticamente artefactos experimentales como producción.
- Aceptación: reinicio/duplicado idempotente, trazabilidad completa, unavailable si inputs/model stale y prueba de extremo a extremo con snapshots reales.

### P7 — Aprobación controlada / UI

- Sólo después de P0–P6 decidir si existe modelo operativo. `production_enabled` requiere gate explícito y evidencia revisada. Añadir límites de antigüedad y botón de rollback a disabled.
- UI nunca llamar “premium”, “apuesta segura” o ROI demostrado a probabilidad experimental. Exponer vigencia/fuentes y error sin ocultarlos.
- Aceptación: despliegue canary con rollback, auditoría de respuesta API/UI, cero campos fabricados, decisión documentada.

### P8 — Retiro del legado y documentación

- Buscar todos los seeds, fixtures, datasets, `fillna`, defaults de números, “ROI”, “PREMIUM”, endpoints y tareas Celery que manejan sports. Separar fixtures de tests de código ejecutable/producción.
- Archivar/renombrar CSV/modelos viejos para impedir entrenamiento accidental; corregir Docker, dependencias y guías de onboarding.
- Aceptación: inventario de consumidores/entrypoints y búsqueda automatizada; todas las rutas apuntan al pipeline vigente o se eliminan/cierran.

## 10. Checklist para el siguiente agente

1. Confirmar checkout/branch/diff; leer `AGENTS.md` si aparece y los documentos de contexto/arquitectura. No sobrescribir cambios del usuario.
2. Leer primero `docs/CONTINUATION_HANDOFF_2026-10-01.md`, `docs/DATA_ML_REFACTOR_PLAN.md`, `docs/ARCHITECTURE.md` y `AI_CONTEXT_PROTOCOL.md`.
3. Volver a comprobar los archivos y no confiar en métricas/hashes fechados aquí como actuales.
4. Revisar el estado de fuente/nuevos datos; no descargar o escribir fuera del repositorio sin autorización/contexto de entorno. Los raws están ignorados y son grandes (~200 MB según observación previa).
5. Mantener `production_enabled=false` hasta que haya criterios medibles y revisión de P0–P6. La tarea inmediata recomendada: P0 + P1 en un PR pequeño, después quality report y persistencia.
6. Añadir o ejecutar tests solicitados por el usuario/plan; documentar comandos exactos y resultados. Esta instrucción de traspaso no sustituye reglas vigentes de ejecución de tests.
7. Informar explícitamente: qué evidencia verificó, qué es inferencia, qué proveedor falló, campos ausentes y bloqueos comerciales/externos. No presentar “API viva” como garantía de datos completos.
8. Actualizar este handoff con fecha nueva en vez de editar silenciosamente la evidencia histórica. Actualizar al mismo tiempo contexto IA y plan principal si la arquitectura cambia.

## 11. Referencias upstream

- [SportsDataverse data](https://github.com/sportsdataverse/sportsdataverse-data) — catálogo/proyecto de distribución de datos deportivos.
- [hoopR: loaders NBA](https://github.com/sportsdataverse/hoopR/blob/main/R/load_nba.R) — referencia de loader NBA de SportsDataverse.
- [nflreadpy repository](https://github.com/nflverse/nflreadpy) — cliente Python.
- [nflreadpy load functions](https://nflreadpy.nflverse.com/api/load_functions/) — APIs de carga documentadas.

Estos enlaces describen proyectos y documentación upstream; no garantizan que un objeto concreto siga disponible, ni definen por sí solos todos los derechos de reutilización de los datos servidos. Al continuar, comprobar la documentación/licencia aplicable al archivo específico y registrar fecha/versión.
