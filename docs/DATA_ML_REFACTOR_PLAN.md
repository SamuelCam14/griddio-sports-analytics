# Plan de refactorización: datos reales y ciclo de vida ML

**Estado:** pipeline base de datos y entrenamiento implementado; inferencia de producción, persistencia canónica en BD y evaluación de apuestas siguen pendientes.
**Alcance:** NBA y NFL dentro de `sports_analytics`; la app heredada `Griddio_python` y `kronz_bet_server` quedan como fuentes a auditar/migrar, no como runtimes paralelos.

## Objetivo y reglas no negociables

Construir una ruta verificable desde el proveedor hasta la predicción y evaluación del modelo. Cada dato conserva su fuente, momento de observación y calidad. Los campos no recibidos permanecen `NULL`/vacíos; no se rellenan con promedios, ceros, defaults plausibles ni datos de otros momentos. Un dato faltante invalida solamente las features o tareas que lo requieren, con un estado explícito; nunca se convierte en señal deportiva.

- Separar adquisición, normalización, persistencia, features, entrenamiento, inferencia y presentación (SRP).
- Mantener adaptadores por proveedor detrás de contratos internos estables.
- Las features de un partido solo pueden usar información disponible antes de su hora de inicio/corte.
- Distinguir dato desconocido, no aplicable, consulta fallida y valor real cero.
- No publicar métricas, picks ni ROI guardados como si fueran el resultado de una corrida actual.
- Un registro bruto es inmutable y auditable; las transformaciones son reproducibles y versionadas.

## Arquitectura objetivo

`Proveedor → adaptador → respuesta cruda con provenance → normalizador/validador → almacenamiento canónico → constructor temporal de features → dataset versionado → entrenamiento/evaluación temporal → artefacto versionado → inferencia → API/UI`

Componentes SRP sugeridos, bajo `backend/app/` y `backend/ml/` siguiendo la arquitectura actual:

1. `data_sources/clients/`: un cliente por API (NBA Stats, ESPN si aplica, nflreadpy, The Odds API); HTTP, límites, timeout y errores únicamente.
2. `data_sources/ingestors/`: coordinación de endpoints/tareas; persiste respuestas y registra ejecuciones. No calcula features.
3. `data_sources/schemas/` y `normalization/`: contratos canónicos por deporte, mapeo de IDs y validación; no inventa valores.
4. `models/` y migraciones: eventos, estadísticas observadas, cuotas con bookmaker/mercado/captura, estado de ingesta y provenance. Campos opcionales aceptan `NULL`.
5. `ml/features/`: funciones puras por dominio y deportes (forma reciente, ratings, descanso, lesiones, cuotas). Todas reciben `as_of`; rechazan datos posteriores.
6. `ml/datasets/`: ensamblado reproducible por fecha, control de nulos, selección de target y manifiesto de fuentes/versiones.
7. `ml/training/`: entrenamiento y validación walk-forward por fecha/temporada, calibración y métricas frente a baseline; no contiene ingestion/UI.
8. `ml/registry/` o equivalente: artefacto + schema de features + versión de código/dataset + período + métricas. La inferencia rechaza artefactos incompatibles.
9. `services/prediction` y routers: solo consumen features completas y artefactos validados; retornan `unavailable` con causas si falta información crítica.
10. Observabilidad: logs estructurados, contadores de llamadas, cuota, antigüedad, filas válidas/nulas, errores y última ejecución correcta por fuente.

## Fases, dependencias y entregables

### Fase 0 — Inventario y congelación de afirmaciones
- Mapear todos los entrypoints, jobs, tablas, semillas, CSV, modelos y consumidores de NBA/NFL; identificar código duplicado en los tres proyectos.
- Clasificar cada campo como observado por API, derivado, manual, estimado o placeholder; bloquear placeholders del camino de producción.
- Guardar evidencia de versiones, endpoints y muestras, sin tratar los resultados de una prueba puntual como SLA.
- Entregable: matriz proveedor→campo→consumidor y decisión de qué aplicación es la fuente canónica.

### Fase 1 — Contratos y persistencia con provenance
- Definir schemas canónicos para equipos, partidos, estadísticas, lesiones y cuotas; IDs externos y tiempos UTC.
- Añadir tablas/columnas y migraciones para `source`, `source_record_id`, `fetched_at`, `observed_at`, `valid_from`/captura de cuota, `raw_payload` o referencia, estado y error.
- Diferenciar resultados finales de valores prepartido. No sobreescribir snapshots de cuotas o reportes anteriores.
- Entregable: contratos versionados y almacenamiento que admite ausencia sin defaults falsos.

### Fase 2 — Adaptadores e ingesta vertical por deporte
- NBA: encapsular `nba_api`; aislar rutas frágiles, configurar timeouts, headers, backoff limitado y tratamiento explícito de HTML/403/429. ESPN solo para campos confirmados y con provenance.
- NFL: integrar `nflreadpy` de forma real en el backend canónico; calendar, resultados y play-by-play/estadísticas solo cuando la fuente los publica.
- Cuotas: aislar The Odds API; persistir snapshots con hora y bookmaker. No afirmar cuotas históricas donde no existen.
- Hacer la ingesta idempotente, paginada y reanudable; guardar estado parcial/error por endpoint sin convertir fallo en dataset vacío exitoso.
- Entregable: comando/job por fuente que produce registros rastreables y reporte de calidad.

### Fase 3 — Normalización y calidad
- Normalizar equipos, fechas, temporadas, estados, mercados y signos de líneas con reglas documentadas y validables.
- Validar IDs duplicados, cruces de marcador, valores imposibles, frescura y cobertura. Las reglas rechazan o ponen en cuarentena; no corrigen inventando.
- Representar explícitamente `missing_reason` o estados equivalentes donde la ausencia tiene significado.
- Entregable: dataset canónico con resumen de nulos, duplicados, cobertura temporal y origen por campo.

### Fase 4 — Features temporales sin fuga
- Implementar un `FeatureContext(as_of)` y funciones puras reutilizables, una responsabilidad por módulo.
- Calcular forma/ratings solo con juegos anteriores; lesiones/cuotas solo con snapshots anteriores al cutoff. Datos finales del propio partido solo son targets.
- Ningún `fillna(0)`, fallback neutral ni `dict.get(..., 0)` para feature predictiva. Para ausencia opcional, preservar nulo y usar pipeline que soporte nulos o excluir la fila/feature con razón registrada.
- Entregable: features reproducibles, lineage por feature y validación automática de que no existe timestamp posterior al corte.

### Fase 5 — Dataset y objetivos de entrenamiento
- Crear datasets independientes NBA/NFL y por mercado/target; documentar definición de cada target y población elegible.
- Excluir solo filas cuya target o features obligatorias no estén disponibles, reportando conteos y motivos; no sustituir cuotas desconocidas por cuotas ficticias.
- Identificar pushes, cancelaciones, juegos incompletos y mercados sin línea según reglas explícitas.
- Versionar el manifiesto del dataset (rango, filtros, columnas, fuentes, fecha de extracción y hash).
- Entregable: dataset de entrenamiento reproducible y reporte de cobertura/selección.

### Fase 6 — Entrenamiento, validación y registro
- Baseline simple primero; luego modelos por deporte/target. Split estrictamente temporal, walk-forward con ventanas repetidas y evaluación de calibración, log loss/Brier, ranking y ROI solo si cuotas reales y timestamps están presentes.
- Medir contra probabilidad de mercado/baseline y registrar intervalos/volumen; separar entrenamiento, validación y prueba final. Sin optimizar umbrales mirando el test final.
- Retirar del contexto/documentación métricas históricas no reproducibles; ninguna cifra queda como vigente sin comando, dataset, período y artefacto que la genere.
- Entregable: artefactos versionados y reporte reproducible; registrar modelo solo si supera criterios predefinidos.

### Fase 7 — Inferencia y producto
- Orquestar `fetch → freshness/coverage check → feature builder → model → calibración → EV` con contratos tipados.
- Si faltan features críticas, el endpoint responde estado sin predicción y lista de causas; la UI no presenta un pick ni EV basado en datos incompletos.
- Incluir `model_version`, `feature_version`, `data_as_of`, fuentes y estado en cada predicción.
- Entregable: camino de una predicción trazable desde proveedor hasta respuesta.

### Fase 8 — Retiro de legado y operación
- Migrar consumidores validados y retirar scripts duplicados, semillas de dominio deportivo y CSV antiguos o marcarlos como archivos no entrenables.
- Agregar health checks por fuente, alertas de frescura/cobertura, límites de reintento y procedimientos de recuperación.
- Mantener manual operativo y catálogo de campos actualizado con cada cambio de contrato.

## Criterios de aceptación global

- Ninguna fila usada para entrenar o inferir contiene valores de relleno sin fuente.
- Cada valor no nulo tiene fuente y momento de observación; cada ausente conserva motivo/estado cuando se conoce.
- Una prueba temporal demuestra que las features de cada juego excluyen información posterior al corte.
- Repetir una construcción con el mismo snapshot/versiones produce el mismo dataset/manifiesto.
- Un fallo o bloqueo de un proveedor queda visible como fallo, nunca como una ingesta exitosa con cero filas.
- Métricas presentadas se regeneran desde el artefacto y el dataset identificados.
- Ingesta, normalización, features, entrenamiento, inferencia y API tienen límites de módulo claros SRP.

## Orden recomendado

Resolver primero instalación/configuración de dependencias y contratos; después completar un flujo vertical real de un deporte (NBA regular-season moneyline, por ejemplo) hasta una evaluación reproducible. Incorporar NFL como segundo flujo una vez compartidos los contratos genéricos. No activar picks de producción mientras los criterios de aceptación de calidad temporal y provenance no estén satisfechos.
# Estado de ejecución (2026-10-01)

Se implementaron snapshots de fuente con hashes, validadores, constructor point-in-time y evaluación temporal. NBA usa Parquet públicos SportsDataverse/ESPN de calendario y boxscore; NFL usa nflverse vía `nflreadpy`. Se descargaron NBA 2021–22 a 2025–26 y NFL 2010–2026. Los datasets incluyen 6,137 partidos NBA y 4,382 NFL; los registros NBA tienen temporada, equipos, marcador y estadísticas observadas por equipo. Los folds usan fechas cronológicas y ningún campo faltante se imputa. La cobertura NBA sigue terminando el 2026-04-12, así que su forma al inicio de la siguiente temporada procede del cierre anterior; no se debe describir como forma actual.

También se conectó el cálculo de features de entrenamiento e inferencia con la misma función; `as_of` representa la última fecha completa de resultados y los partidos del mismo día no entran al target. El calendario NBA 2026–27 y los schedules/PBP NFL 2026 se descargaron. Con el cutoff 2026-10-01 se generaron archivos de scoring experimental para 1,200 juegos NBA y 223 NFL; seis juegos NBA con equipos aún sin anunciar se excluyeron con motivo. Cada corrida registra SHA-256 de snapshots, dataset, artefacto y archivo resultante. El scoring se hizo explícitamente con `--allow-experimental` y no habilitó predicciones en la API.

Los modelos resultantes permanecen con `production_enabled: false`; métricas de walk-forward no certifican inferencia ni rentabilidad. Las cifras redondeadas en esta sección son históricas; para la metadata comprobada el 2026-10-01, el HistGradientBoosting NFL sí supera baseline mayoritario Brier en todos los folds, aunque su media queda por debajo de Logistic. Ver las métricas precisas y la evaluación de sus límites en el handoff. No se identificó una fuente gratuita verificable de cuotas históricas suficiente para ROI.

Quedan pendientes de las fases originales: almacenamiento de ejecuciones/filas canónicas en BD, integración de inferencia prepartido con inputs actuales, calibración/umbrales y revisión de artefactos, captura histórica de cuotas, monitorización y retirada formal de todos los generadores/seeds legacy que no participan en esta ruta.

Comandos una vez instaladas las dependencias:

```bash
PYTHONPATH=backend python backend/scripts/sync_training_sources.py --sport nba --seasons 2022 2023 2024 2025 2026
PYTHONPATH=backend python backend/scripts/sync_training_sources.py --sport nfl --seasons 2010 2011 2012 2013 2014 2015 2016 2017 2018 2019 2020 2021 2022 2023 2024 2025
PYTHONPATH=backend python backend/scripts/build_training_datasets.py --sport all
PYTHONPATH=backend python backend/ml/training/train_models.py --sport all
PYTHONPATH=backend python backend/scripts/sync_upcoming_schedules.py --sport all
PYTHONPATH=backend python backend/scripts/predict_upcoming.py --sport all --allow-experimental
```

`predict_upcoming.py` es una salida CSV offline de probabilidades, sin cuotas ni picks. La opción `--allow-experimental` no certifica precisión y no cambia el gate de producción. Para NFL, vuelve a sincronizar la temporada en curso mediante `sync_training_sources.py --sport nfl --seasons 2026` después de cada jornada; reconstruye y reentrena antes de volver a generar scoring. Para NBA, refresca el schedule al anunciar cambios; las features seguirán usando boxscores finales ya ingeridos. Comprueba `--help` si el CLI cambia.

La API también expone `GET /api/v1/predictions/experimental/{nba|nfl}?lookahead_days=14`. Verifica el hash del último CSV y devuelve solo probabilidades junto con `as_of`, modelo y referencias de source/schedule. No calcula EV, tiers ni cuotas; `/today` y `/game/{id}` siguen cerrados mientras no se apruebe el artefacto y se integre la persistencia canónica.

## Traspaso de implementación y estado verificable

El documento inicial contiene diseño y fases, pero no toda la evidencia operacional. Para métricas exactas, cifras por fold, último scoring con hash, comandos usando el virtualenv, validaciones realizadas, limitaciones de cobertura/licencias, tareas priorizadas y criterios de aceptación detallados, continuar desde [`CONTINUATION_HANDOFF_2026-10-01.md`](CONTINUATION_HANDOFF_2026-10-01.md). La evidencia fechada no se considera actual después de cambios posteriores.

**Corrección de métricas:** al verificar metadata actual el 2026-10-01, HistGradientBoosting NFL sí figura superando baseline mayoritario Brier en todos los folds (`beats_majority_brier_in_all_folds: true`). Su media sigue por debajo de Logistic y ninguna de estas métricas aprueba producción. Ver cifras completas en el handoff.
