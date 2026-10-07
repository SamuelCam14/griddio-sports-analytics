# P4: ML & Feature Engineering Pipeline Design

## 1. Definición del Target
- **Target:** Ganador del partido (Local = 1, Visitante = 0).
- **Restricciones de Features:** No se incluirán cuotas (odds) como variables de entrada (features) para prevenir data leakage y asegurar que el modelo evalúe la probabilidad puramente por mérito deportivo. El ROI y el EV quedan excluidos del alcance de esta fase.

## 2. Baseline y Comparación
- **Baseline:** Tasa base histórica (Win Rate general del equipo local en la liga).
- **Regla de comparación:** El modelo debe superar consistentemente el Brier score y el log loss del baseline en cada temporada evaluada.

## 3. Validación y Holdout
- **Folds:** Validación cruzada cronológica (Time Series Split) particionada estrictamente por temporada para evitar que el modelo aprenda del futuro.
- **Holdout:** Se reservará una temporada entera (ej. 2023-2024 o un porcentaje cronológico final) como conjunto de prueba (holdout) *intacto*. Solo se evaluará una vez al final del proceso. 

## 4. Métricas a Reportar
Se generará un reporte exhaustivo por temporada incluyendo:
- **AUC-ROC:** Capacidad de discriminación.
- **Brier Score:** Error cuadrático de la predicción de probabilidad.
- **Log Loss:** Penalización severa para probabilidades erróneas muy confiadas.
- **Calibración:** Curva de calibración (Expected Calibration Error).
- **Cobertura y Volumen:** Cuántos partidos son predecibles bajo los umbrales definidos.

## 5. Congelación y Reproducibilidad
- Las versiones de los datasets, features extraídos, código del pipeline y el artefacto resultante se congelarán con identificadores SHA256.
- No se permitirá el ajuste de hiperparámetros ni umbrales de decisión observando los resultados del holdout.
