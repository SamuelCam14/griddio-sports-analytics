# Griddio AI Factory Backlog

## INSTRUCCIONES DEL USUARIO (OBJETIVO ACTUAL):
Auditar y cerrar secuencialmente (una por una) las 6 tareas del proyecto. Primero auditar el lote actual de cambios de Antigravity con `git status` y `git diff`, verificar que pasen todos los tests relevantes, y no avanzar a la siguiente tarea hasta que la actual esté 100% aprobada y con commit en git.

---

## Convenciones de Estado (Regla estricta: máximo 1 tarea en `[~]` a la vez)
- `[ ] Pendiente`: En cola.
- `[~] En Progreso`: Tarea activa en este turno.
- `[x] Completada`: Auditada, aprobada al 100% y con commit en git.

---

## Tareas

### Fase 1: Calidad de Datos & APIs
- [x] **TAREA-1:** Validar ingesta y endpoints de datos para calendarios y resultados de NFL y NBA. *(Aprobada y confirmada en commit `3a8b5a7`; loader NBA filtra por kickoff UTC y standings completa cobertura parcial con fuente verificada.)*
- [x] **TAREA-2:** Verificar manejo de cuotas, spreads y totales en la capa de datos. *(Auditada y confirmada en commit `2cf342a`; validación fail-closed, procedencia por lado y benchmark no-vig con atribución persistida, prueba E2E PostgreSQL/API.)*

### Fase 2: Modelos de Machine Learning
- [x] **TAREA-3:** Auditar pipeline de entrenamiento del modelo Random Forest y modelos temporales (`train_temporal.py`, `train_models.py`, `temporal_features.py`) garantizando validación temporal estricta sin data leakage. *(Aprobada tras corregir el tratamiento de `datetime`/`pd.Timestamp` a medianoche; suite integrada independiente 51 passed; commit `AI-Factory: Completed TAREA-3`.)*
- [x] **TAREA-4:** Evaluar métricas de rendimiento y calibración de probabilidades en los 3 modelos predictivos (`inference.py`, `ml_engine.py`, reportes de calidad). *(Aprobada tras auditar métricas OOF/calibración, cohortes e inferencia concurrente; 9 tests de auditoría y 81 de regresión pasaron; commit `AI-Factory: Completed TAREA-4`.)*

### Fase 3: Integración de API & Frontend
- [x] **TAREA-5:** Comprobar contratos de endpoints de predicciones en FastAPI (`backend/app/api/v1/predictions.py`, `games.py`, `odds.py`, `parlay.py`) incluyendo manejo de errores y estados vacíos. *(Aprobada tras eliminar timestamps/casas sintéticos, validar procedencia por lado y rechazar timestamps naive; suite focalizada independiente 103 passed; commit `AI-Factory: Completed TAREA-5`.)*
- [x] **TAREA-6:** Validar renderizado de métricas, manejo de valores nulos y contratos de pantalla en React / TypeScript (`frontend/src/`) verificando que `npm run lint` y `npm run build` pasen con 0 errores. *(Aprobada tras corregir el formateo EV no finito, completar `OddsRemainingResponse` y probar utilidades; commit `783f68a`.)*
