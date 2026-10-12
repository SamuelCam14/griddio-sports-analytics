# Reporte de Línea Base — Fase 00: Gobernanza, Gate y Baseline Reproducible

**Fecha de ejecución:** 2026-10-11  
**Alcance:** Exclusivamente NFL (`nfl_temporal_v1`). NBA fuera de esta ejecución.  
**Commit base:** `8f158ce450e2e37c25b09f0ee8505ce27a7263a0` (branch: `main`)  
**Entorno de ejecución:** Docker container `sports_api` (Python 3.12.15, pytest 9.1.1, scikit-learn 1.6.1, pandas 2.2.3, polars 1.23.0). Host: Windows 11.

---

## 1. Estado y Verificación del Gate de Producción

El gate de producción para NFL se encontraba indebidamente abierto (`production_enabled: true` en `backend/ml/models/nfl/model_metadata.json`). Se procedió a su cierre operacional inmediato (`production_enabled: false`) y a blindar el código de entrenamiento (`train_temporal.py:189`) para que por defecto siempre emita `False`.

Adicionalmente, se reforzó `backend/app/api/v1/predictions.py` en `get_game_prediction` para verificar el estado de producción específico del deporte (`pred_sport`) asociado a la predicción consultada, garantizando que si el modelo de ese deporte no está aprobado, la solicitud falle cerrado con HTTP 503.

### Inventario de Endpoints y Consumidores NFL

| Endpoint / Consumer | Archivo de origen | Mecanismo de Gate | Estado verificado | Respuesta con Gate Cerrado |
|---|---|---|---|---|
| `GET /api/v1/predictions/today?sport=NFL` | `predictions.py:40-67` | `_production_model_enabled("NFL")` | **CERRADO** (`False`) | `total_picks: 0`, `picks: []`, `predictions_enabled: false` |
| `GET /api/v1/predictions/top?sport=NFL` | `predictions.py:321-340` | Invoca `get_today_picks(sport="NFL")` | **CERRADO** (`False`) | `top_picks: []`, `mensaje: "Mostrando 0 de 0 picks..."` |
| `GET /api/v1/predictions/game/{game_id}` (NFL) | `predictions.py:343-369` | `_production_model_enabled(pred_sport)` | **CERRADO** (`False`) | HTTP 503 (`Predicciones deshabilitadas para NFL...`) |
| `POST /api/v1/predictions/simulate` | `predictions.py:448-454` | Incondicional fail-closed | **CERRADO** (Siempre 503) | HTTP 503 (`Predicción deshabilitada: faltan features...`) |
| `GET /api/v1/predictions/experimental/nfl` | `predictions.py:228-318` | Requiere manifest y CSV con hash coincidente y timestamps < 24h | **EXPERIMENTAL ONLY** | Probabilidades puras; `predictions_enabled: false`, `production_eligible: false`. Sin EV/tiers/picks. |
| `GET /api/v1/predictions/model-info` | `predictions.py:410-445` | Lee metadata de cada deporte | **DIAGNÓSTICO** | Reporta `production_enabled: false` para NFL |
| `GET /api/v1/parlay/recommended` | `parlay.py:34-64`, `parlay_service.py:114-175` | `_production_model_enabled(sport)` + gate check por partido | **CERRADO / SANITIZADO** | Fallback ficticio eliminado. Con gate NFL cerrado o <2 candidatos devuelve `status: "unavailable"` (si `sport=NFL`) o lista vacía `total: 0, parlays: []`. Cero datos inventados. |
| Celery task `evaluate_predictions_task("NFL")` | `celery_app.py:118-205` | `meta.get("production_eligible")` derivado de `production_enabled` | **CERRADO** | `production_eligible: False`. Tarea registra advertencia y descarta inserción en PostgreSQL |

### 1.1 Corrección Bloqueante: Eliminación de Fallback Ficticio, Alcance Exclusivo NFL y Validación de Versión Exacta de Modelo

En las revisiones de Codex se identificaron dos bloqueos en el Parlay Service y su ruta asociada:
1. `backend/app/services/parlay_service.py` contenía un fallback con 4 candidatos ficticios hardcodeados (Boston Celtics, Denver Nuggets, etc.) cuando había menos de 2 candidatos en DB. Además, `GET /api/v1/parlay/recommended` no forzaba deporte por defecto, permitiendo que llamadas sin parámetro `sport` sirvieran recomendaciones de NBA (cuyo gate estaba abierto en `nba/model_metadata.json`).
2. El filtrado de candidatos en base de datos solo revisaba si el gate del deporte estaba abierto, permitiendo que predicciones con versiones legacy o arbitrarias (`catboost_v1`, `xgboost_v1`, etc.) se convirtieran en candidatos si el gate estuviese abierto.

**Acciones de saneamiento aplicadas:**
1. **Eliminación total del mock fallback:** Se eliminó incondicionalmente la inyección de candidatos ficticios en `generate_recommended_parlays`. Si `len(candidates) < 2`, la función retorna incondicionalmente `[]`.
2. **Alcance exclusivo a NFL por defecto en ruta y servicio:**
   - La ruta `GET /api/v1/parlay/recommended` ahora define `sport: str = Query(default="NFL")`. Si se consulta sin parámetro (como hace el frontend actual), opera exclusivamente sobre NFL.
   - Cualquier solicitud explícita con deporte distinto a NFL (e.g. `sport=NBA`) es rechazada con `status: "unavailable"` y `reason` explícito.
   - `generate_recommended_parlays` valida `target_sport == "NFL"` como precondición; si se solicita otro deporte retorna `[]`.
3. **Validación estricta y blindaje fail-closed de metadata (`get_approved_model_version`):**
   - Se implementó y blindó `get_approved_model_version(sport: str) -> Optional[str]` en `backend/app/api/v1/predictions.py`:
     - Acepta únicamente deportes permitidos (`ALLOWED_SPORTS = {"NFL", "NBA"}`) antes de construir cualquier ruta a disco, rechazando inputs vacíos, no-string, desconocidos o con secuencias de alteración de ruta (`../../`, etc.).
     - Engloba incondicionalmente la resolución de ruta (`Path(__file__).resolve()`), la comprobación de existencia (`metadata_path.is_file()`), la lectura de disco (`read_text`) y el parseo JSON (`json.loads`) dentro del bloque `try ... except Exception: return None`, impidiendo que fallos de filesystem, permisos o `OSError` se propaguen al endpoint.
     - Verifica que el JSON raíz sea estrictamente un objeto/dict (rechazando listas, escalares, o null).
     - Comprueba que el campo `sport` en la metadata coincida exactamente con el deporte solicitado.
     - Exige `production_enabled is True`.
     - Exige que `model_version` sea string y no quede vacío tras `strip()`.
     - Cualquier fallo de validación o I/O retorna `None`, garantizando que `_production_model_enabled()` retorne `False` sin filtrar excepciones a los endpoints.
   - `generate_recommended_parlays` obtiene la versión aprobada y filtra estrictamente en SQL: `.where(Game.sport == target_sport).where(Prediction.model_version == approved_version)`.
   - Se incluye validación de seguridad en el procesamiento de registros (`getattr(game, "sport", None) != target_sport or pred.model_version != approved_version: continue`). Registros legacy o no autorizados son descartados incondicionalmente.
4. **Suite de pruebas de parlay y metadata (`backend/tests/test_parlay.py` y `test_prediction_gate.py`):**
   - 8 pruebas automatizadas en `test_parlay.py` que cubren:
     - Precondición de gate NFL cerrado y versión aprobada nula.
     - Retorno de lista vacía en escenarios con 0 o 1 candidato (regresión del mock anterior).
     - Rechazo de candidatos cuando el gate de NFL está cerrado.
     - Rechazo estricto de predicciones legacy (`catboost_legacy_v1`, `xgboost_v1`) aun cuando el gate esté simuladamente abierto, y aceptación exclusiva de `nfl_temporal_v1`.
     - Exclusión total de partidos NBA.
     - Comportamiento de la ruta sin parámetro: adopta NFL por defecto y responde sin picks con gate cerrado.
     - Rechazo explícito de `sport=NBA` en la ruta.
     - Validación de piernas mínimas en evaluador de parlay personalizado.
   - 7 pruebas adicionales en `test_prediction_gate.py` que validan el blindaje fail-closed de metadata y filesystem:
     - Rechazo de deportes no permitidos, inputs vacíos y path traversal.
     - Rechazo de JSON raíz que no sea dict (listas, escalares, booleanos, null).
     - Rechazo de JSON malformado, `OSError` de lectura y errores de decodificación UTF-8 sin fuga de excepciones.
     - Rechazo de `OSError` en `is_file()` y en `Path.resolve()` sin permitir propagación de excepciones.
     - Rechazo de discrepancias en el campo `sport` de la metadata.
     - Rechazo de `model_version` vacío, con solo espacios, no-string o faltante.
     - Aceptación limpia y stripped únicamente ante metadata válida con `production_enabled=True`.


---

## 2. Línea Base de Datos y Artefactos NFL

### Identificación de Dataset y Provenance

- **Dataset canónico actual:** `data/processed/nfl_training.csv`
  - **SHA-256 verificado:** `00680e3cc98ca3aae869534ada33be6b055ac6643f1b210d583948c2866c4ac6`
  - **Filas utilizables:** 4,398 partidos completados (2010-09-19 a 2026-10-05)
  - **Manifiesto:** `data/processed/nfl_training.manifest.json`
  - **Fuente subyacente:** nflverse vía `nflreadpy` (snapshots parquet en `data/raw/nflverse/`, hash `3f8315e73885980578ba4fc3a74d633527238e3e4fdf067e542a5676bb68fbab`)
  - **Filas descartadas en build:** 13 empates (target binario), 16 sin historial suficiente (< 1 partido previo)
  - **Features:** 32 variables continuas calculadas point-in-time (`feature_columns` con ventana rolling 10 partidos).

### Explicación de Divergencias entre Reportes

1. **`data/quality_report.json` (Generado el 2026-10-01T21:23:02Z):**
   - Filas: 4,382
   - Rango: 2010-09-19 a 2026-09-28
   - SHA-256: `8316ecab5c2638443a989ada682031f4e6f26935322d5df970646502109a511c`
   - *Causa de la discrepancia:* Corresponde a la primera corrida del 1 de octubre. No incluye los 16 partidos completados en la Semana 4 entre el 29 de septiembre y el 5 de octubre de 2026.
2. **`nfl_training.manifest.json` y `model_metadata.json` (Generados el 2026-10-08T22:44:45Z):**
   - Filas: 4,398 (+16 partidos)
   - Rango: 2010-09-19 a 2026-10-05
   - SHA-256: `00680e3cc98ca3aae869534ada33be6b055ac6643f1b210d583948c2866c4ac6`
   - *Conclusión:* El dataset vigente en disco es el del 8 de octubre (`4,398` filas, hash `00680e3...`). El `quality_report.json` histórico del 1 de octubre quedó desactualizado respecto a la última reconstrucción de dataset.

### Artefactos de Modelo NFL Existentes

Ubicación: `backend/ml/models/nfl/`
- `logistic.joblib`: SHA-256 `FB3000854BCB331B1F399725F135544741C3D99C96E8F64F19F023A9CC33606F`
- `hist_gradient_boosting.joblib`: SHA-256 `AA2573A919CC4D37F74F9EECD8D5A9E5690973F9DFC6062B19651C919ACCDC08`
- `model_metadata.json`: Versión `nfl_temporal_v1` (dataset `00680e3...`, gate cerrado `production_enabled: false`).

---

## 3. Separación Cronológica y Corrección del Temporal Splitter

### Hallazgo Crítico en `chronological_folds()` (Bloqueo #8 Resuelto)

En la implementación anterior de `chronological_folds()`:
```python
test_mask = all_dates >= cv_dates[start]
if end < len(cv_dates):
    test_mask &= all_dates < cv_dates[end]
```
Cuando `end == len(cv_dates)` (último fold de validación cruzada), la condición `end < len(cv_dates)` resultaba falsa. En consecuencia, el `test_mask` del Fold 4 no tenía límite superior y abarcaba todas las fechas posteriores, **incluyendo la totalidad de los 824 registros del lockbox/holdout final**.

### Corrección Implementada

Se estableció explícitamente el límite superior del último fold como `dates[holdout_start]`:
```python
end_date = cv_dates[end] if end < len(cv_dates) else dates[holdout_start]
test_mask = (all_dates >= cv_dates[start]) & (all_dates < end_date)
```
Y se añadieron aserciones estrictas que levantan `DataQualityError` si ocurre cualquiera de los siguientes fallos:
1. Intersección entre `train_idx` y `test_idx` de cualquier fold (`len(intersect) > 0`).
2. Intersección entre `test_idx` de cualquier fold y `holdout_indices` (`len(intersect) > 0`).
3. Fecha máxima de entrenamiento mayor o igual a la fecha mínima de prueba (`max(train_date) >= min(test_date)`).
4. Fecha máxima de validación mayor o igual a la fecha mínima del holdout (`max(cv_test_date) >= min(holdout_date)`).

### Particionado Cronológico Verificado sobre el Dataset Actual (4,398 Filas)

| Segmento | N° Filas | Fecha Inicio (`min`) | Fecha Término (`max`) | Solapamiento con Holdout | Precedencia Temporal |
|---|---|---|---|---|---|
| **Fold 1 Train** | 2,191 | 2010-09-19 | 2018-10-08 | 0 | `train < test` (Estricto) |
| **Fold 1 Test** | 350 | 2018-10-11 | 2019-11-18 | 0 | `test < Fold 2` |
| **Fold 2 Train** | 2,541 | 2010-09-19 | 2019-11-18 | 0 | `train < test` (Estricto) |
| **Fold 2 Test** | 360 | 2019-11-21 | 2021-01-03 | 0 | `test < Fold 3` |
| **Fold 3 Train** | 2,901 | 2010-09-19 | 2021-01-03 | 0 | `train < test` (Estricto) |
| **Fold 3 Test** | 328 | 2021-01-09 | 2022-09-19 | 0 | `test < Fold 4` |
| **Fold 4 Train** | 3,229 | 2010-09-19 | 2022-09-19 | 0 | `train < test` (Estricto) |
| **Fold 4 Test (Corregido)** | **345** | 2022-09-22 | 2023-10-19 | **0** | `max(2023-10-19) < holdout(2023-10-22)` |
| **Lockbox / Holdout Final** | **824** | **2023-10-22** | **2026-10-05** | **0** | **Totalmente intacto e independiente** |

*Nota de impacto:* En la corrida registrada en `model_metadata.json` del 8 de octubre, el Fold 4 reportaba 1,169 filas de test porque incluía las 824 filas del holdout. Con la corrección matemática aplicada, el Fold 4 contiene exactamente 345 filas de test, finalizando el 2023-10-19 sin tocar el holdout que arranca el 2023-10-22.

---

## 4. Métricas de la Línea Base Existente (Octubre 8, 2026)

> [!WARNING]
> Estas métricas corresponden a la evaluación temporal previa sobre el dataset de 4,398 filas. No representan ROI, no involucran cuotas de mercado, y el Fold 4 en dicha corrida contenía el solapamiento con el holdout descrito en la sección anterior. Se presentan únicamente como registro de la fotografía actual.

### Modelo: Regresión Logística (`logistic`)
- **Accuracy promedio (folds):** 62.83%
- **AUC-ROC promedio (folds):** 0.6778
- **Brier Score promedio (folds):** 0.2267 (Supera baseline mayoritario 0.2501 en todos los folds)
- **Log Loss promedio (folds):** 0.6447
- **Evaluación en Holdout (824 filas):**
  - Accuracy: 62.99%
  - AUC-ROC: 0.6682
  - Brier Score: 0.2267 (vs baseline mayoritario: 0.2469)
  - Log Loss: 0.6446 (vs baseline mayoritario: 0.6869)

### Modelo: HistGradientBoosting (`hist_gradient_boosting`)
- **Accuracy promedio (folds):** 58.76%
- **AUC-ROC promedio (folds):** 0.6381
- **Brier Score promedio (folds):** 0.2499 (`beats_majority_brier_in_all_folds: false`)
- **Log Loss promedio (folds):** 0.7053
- **Evaluación en Holdout (824 filas):**
  - Accuracy: 58.25%
  - AUC-ROC: 0.6203
  - Brier Score: 0.2518 (Peor que baseline mayoritario: 0.2469)
  - Log Loss: 0.7073

---

## 5. Inventario de Modificaciones Preexistentes en Frontend

Se verificó el estado de trabajo del repositorio. Los 19 archivos modificados en `frontend/` corresponden exclusivamente a rediseño de interfaz de usuario (adopción del sistema visual macOS/Apple Dark, clases `macos-card`, `macos-window-shell`, `apple-blue`, ajustes de paleta Tailwind y espaciado):

1. `frontend/tailwind.config.js`: Extensiones de tema macOS y paleta de colores.
2. `frontend/src/index.css`: Clases utilitarias (`macos-card`, `macos-metric-tile`, botones y scrollbars).
3. `frontend/src/pages/DashboardPage.tsx`: Estructura del shell y transiciones.
4. `frontend/src/components/dashboard/DashboardHeader.tsx`: Toolbar superior estilo macOS.
5. `frontend/src/components/dashboard/DashboardModalManager.tsx`: Modales unificados.
6. `frontend/src/components/dashboard/AiMatchupSimulatorCard.tsx`: Restilizado a tarjeta macOS.
7. `frontend/src/components/dashboard/AiPredictionList.tsx`: Tablas y badges visuales.
8. `frontend/src/components/dashboard/HeroSpotlight.tsx`: Hero banner con gradientes y tipografía macOS.
9. `frontend/src/components/dashboard/LiveMatchDetailCard.tsx`: Tarjetas de marcador en vivo.
10. `frontend/src/components/dashboard/MetricsGrid.tsx`: Mosaico de métricas macOS.
11. `frontend/src/components/dashboard/MvpOfTheDayCard.tsx`: Tarjeta destacada.
12. `frontend/src/components/dashboard/StandingsTableCard.tsx`: Clasificación tabular.
13. `frontend/src/components/ui/Badge.tsx`: Badges de estado.
14. `frontend/src/components/ui/Button.tsx`: Botones macOS primarios/secundarios.
15. `frontend/src/components/ui/Card.tsx`: Componente contenedor.
16. `frontend/src/components/ui/Modal.tsx`: Diálogo modal.
17. `frontend/src/components/ui/PillTabs.tsx`: Pestañas tipo pastilla.
18. `frontend/src/components/ui/Sparkline.tsx`: Gráficos sparkline SVG.
19. `frontend/src/components/ui/TeamLogo.tsx`: Renderizado de emblemas de equipo.

**Acción tomada:** Se preservaron el 100% de estos cambios sin reversiones, reescrituras ni formateos ajenos.

---

## 6. Pruebas Automatizadas Ejecutadas

Ejecutadas dentro del contenedor Docker oficial (`sports_api`):
```bash
docker exec -e PYTHONPATH=/app sports_api pytest -v tests/test_prediction_gate.py tests/test_temporal_pipeline.py tests/test_parlay.py
```
**Resultado:** `40 passed in 4.58s` (incluye 8 pruebas de parlay service y rutas, 26 pruebas de prediction gate/metadata/filesystem y 6 pruebas de pipeline temporal).

Validación completa de la suite del sistema:
```bash
docker exec -e PYTHONPATH=/app sports_api pytest -v -k "not test_exhaustive_idempotency and not test_exhaustive_multiple"
```
**Resultado:** `88 passed, 2 deselected, 9 warnings in 7.64s` (suite completa operativa; 2 tests omitidos por requerir driver opcional `aiosqlite` no instalado en la imagen base).
