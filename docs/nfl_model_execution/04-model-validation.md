# Fase 04 — Baselines, modelos, ensemble y validación

## Objetivo

Construir probabilidades NFL calibradas y reproducibles; decidir si una familia/ensemble añade señal fuera de muestra. No usar “multimodelo” como sinónimo de ventaja.

## Mercados y targets

1. Fase primaria: dinero-line (ganador del partido, ties resueltos explícitamente según reglas de mercado).
2. Spread y totals requieren target/settlement y modelos separados; no transformar win probability a cover probability con sigma escogida manualmente.
3. Player props son mercados/targets por jugador y línea, con push/void y set de participantes separado. No se hereda el clasificador de ganador como predictor de props.

## Baselines obligatorios

- Baseline de mercado sin vig en el mismo `decision_cutoff` y cobertura de bookmaker fijada.
- Baseline simple de rating/elo computado point-in-time.
- Logistic Regression temporal como baseline estadístico.
- HistGradientBoosting actual como candidato a conservar/eliminar; revisar métricas almacenadas antes de repetir.
- Añadir CatBoost/XGBoost/otros solo si las dependencias/versiones se fijan y CV muestra una mejora estable. No restaurar pesos históricos CatBoost/XGBoost/Logistic por defecto.

## Diseño de splits/calibración

1. Usar fechas completas, grupos por fecha y límites cronológicos inclusivos/exclusivos registrados.
2. Corregir el último fold para que termine antes del lockbox. Assertion: ninguna fecha/ID test en fold pisa el lockbox; ningún train tiene fecha igual/posterior al test.
3. Definir ventana expandida o rolling, cantidad de folds y mínimo de temporadas antes de correr experimentos.
4. Congelar una temporada/intervalo más reciente como lockbox; no inspeccionar resultados durante tuning/feature selection.
5. Hyperparameter search, selección de features y pesos de ensemble solo dentro de folds/OOF cronológico.
6. Calibrar con predicciones OOF cronológicas o tramo de calibración temporal separado. No usar `CalibratedClassifierCV(cv=5)` aleatorio por defecto para serie temporal.
7. Recalcular métricas sobre lockbox una sola vez, documentando si posteriormente se abrió. Si se consume, crear un nuevo período prospectivo para confirmación.

## Ensemble

- Entrenar base learners independientes en el mismo split y persistir sus predicciones OOF.
- Usar stacking/metamodel solo con OOF cronológico; calibrar la probabilidad final aparte.
- Comparar ensemble con cada modelo individual, baseline de mercado y rating model.
- Aprobar ensemble solo si añade valor en múltiples períodos (Brier/log loss, calibración y métricas de decisión); accuracy/AUC aisladas no bastan.
- Si ensemble no supera de forma consistente al mejor modelo, mantener el modelo simple.

## Métricas

Por fold/lockbox y segmentos: log loss, Brier, AUC, accuracy, calibration intercept/slope, curva de confiabilidad, cobertura/disponibilidad, baseline de mercado no-vig, subgrupos underdog/favorite y divergencia modelo-mercado. Las métricas de EV/ROI se calculan en Fase 05 con cuotas históricas ejecutables.

## Pasos

1. Validar dataset, schema, sample counts, fecha mínima/máxima, target distribution y group coverage.
2. Corregir split y añadir tests de no-overlap/leakage de fecha.
3. Correr baseline market, rating, Logistic, HistGradientBoosting; producir predictions OOF con refs.
4. Registrar calibración y comparar contra holdout locked.
5. Ejecutar experimentos adicionales solo en CV. Seleccionar por criterio preregistrado, no por mejor cifra aislada.
6. Generar un único reporte por candidate con dataset hash, git commit, feature version, split, params, random seed y artifact hash.
7. Documentar ganador experimental y sus limitaciones; artefactos se conservan como `production_enabled=false`.

## Aceptación

- El pipeline produce métricas sin solapamiento de folds/lockbox.
- Calibrador es temporal y reproduce su fit data/cutoff.
- Todos los modelos comparan el mismo target, dataset, split y feature contract.
- La API no llama a los artifacts como producción.
- Ninguna cifra se describe como ventaja frente a Vegas hasta completar Fase 05.

