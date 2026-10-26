# Estudio de Ablación de Margen y Spread NFL — Punto 6 del Plan de Corrección

**Fecha:** 25 de octubre de 2026  
**Protocolo:** Plan de Corrección, Punto 6: *Eliminar y medir el posible doble conteo en spread*  
**Estado:** Completado y Verificado  
**Artefactos:**
- Predicciones de ablación: [`data/predictions/evaluation/nfl_spread_ablation_predictions.csv`](file:///home/samuelc/Documents/Progreso/griddio-sports-analytics/data/predictions/evaluation/nfl_spread_ablation_predictions.csv) (SHA-256: `bd5e07ec89cd9ccbb5dbe778f8c9f3bd4effbf94f58ad606443650668ac39ce2`)
- Manifiesto de ejecución: [`data/predictions/evaluation/nfl_spread_ablation_manifest.json`](file:///home/samuelc/Documents/Progreso/griddio-sports-analytics/data/predictions/evaluation/nfl_spread_ablation_manifest.json)
- Script de evaluación: [`backend/ml/training/evaluate_spread_ablation.py`](file:///home/samuelc/Documents/Progreso/griddio-sports-analytics/backend/ml/training/evaluate_spread_ablation.py)
- Suite de pruebas: [`backend/tests/test_nfl_spread_ablation.py`](file:///home/samuelc/Documents/Progreso/griddio-sports-analytics/backend/tests/test_nfl_spread_ablation.py)

---

## 1. Contexto y Pregunta de Investigación

En las implementaciones iniciales del pipeline NFL existía un riesgo estructural de **doble penalización** (doble conteo) en las predicciones de margen y spread:
1. El clasificador `injury_aware` incorporaba variables de disponibilidad ofensiva (`ppr_lost_pct`, `off_snaps_lost_pct`). Cuando un QB titular causaba baja, estas variables aumentaban drásticamente, reduciendo la probabilidad de victoria predicha $p_{\text{home}}$.
2. De esa probabilidad se derivaba el margen base de puntos ($\Phi^{-1}(p) \times 13.5$).
3. Posteriormente, en `sports_analytics_service.py`, se restaba una deducción aditiva adicional por QB (originalmente una constante rígida de 4.5 puntos VORP, sustituida en el Punto 5 por el estimador bayesiano `qb_lost_expected_epa` pregame).

El **Punto 6 del Plan de Corrección** estipuló:
> *"Separaré tres variantes: lesiones generales en el clasificador, ajuste QB adicional al margen, y ambas juntas. Las compararé con idénticos folds y métricas de margen/cover cuando haya targets y cuotas adecuados. Mantendré el ajuste QB adicional solo si aporta mejora fuera de muestra sobre el modelo que ya usa features de lesiones; si no, quitaré esa segunda aplicación. La probabilidad de moneyline y las proyecciones de margen también quedarán claramente distinguidas."*

---

## 2. Definición Formal de las Tres Variantes

Para cada partido $i$ en los 4 folds cronológicos out-of-fold (188 partidos evaluados fuera de muestra):
- **Margen Real:** $M_i = \text{home\_score}_i - \text{away\_score}_i = \text{result}_i$.
- **Línea Oficial de Spread Vegas:** $S_i = \text{spread\_line}_i$ (convención oficial nflverse: $S_i > 0$ cuando el equipo local es favorito).
- **Target de Cover Local:** $Y_{\text{cover}, i} = 1$ si $M_i > S_i$, $0$ si $M_i < S_i$. Partidos empatados con spread ($M_i = S_i$, $N=8$, tasa 4.25%) se registran como *pushes* y se excluyen del cálculo de Brier/LogLoss de cover conforme al estándar de la industria.
- **Diferencial de EPA de QB Pregame ($\Delta_{\text{QB}, i}$):**
  $$\Delta_{\text{QB}, i} = \text{qb\_lost\_expected\_epa}_{\text{home}, i} - \text{qb\_lost\_expected\_epa}_{\text{away}, i}$$
  Calculado estrictamente con información previa al corte (`date_modified < kickoff - 2h` y semanas anteriores $t-1$), usando contracción empírica bayesiana con 50 oportunidades hacia el percentil 25 de la liga (`qb_epa_replacement_p25_shrink50_v1`).

### Variantes Evaluadas:
1. **Variante A (`spread_injury_aware_no_qb_adj` — Lesiones generales en el clasificador):**
   - Probabilidad de victoria: $p = p_{\text{injury\_aware}}$ (del modelo congelado BoundedLogisticRegression).
   - Margen proyectado: $\text{proj\_margin}_A = \Phi^{-1}(p) \times 13.5$.
   - Ajuste QB adicional: $0$ (sin ajuste aditivo al margen).
   - Probabilidad de cover: $P(\text{home cover})_A = \Phi\left(\frac{\text{proj\_margin}_A - S_i}{13.5}\right)$.

2. **Variante B (`spread_team_only_with_qb_adj` — Ajuste QB sobre modelo macro):**
   - Probabilidad de victoria: $p = p_{\text{team\_only}}$ (clasificador sin variables de lesiones).
   - Margen proyectado: $\text{proj\_margin}_B = \Phi^{-1}(p_{\text{team\_only}}) \times 13.5 - \Delta_{\text{QB}, i}$.
   - Probabilidad de cover: $P(\text{home cover})_B = \Phi\left(\frac{\text{proj\_margin}_B - S_i}{13.5}\right)$.

3. **Variante C (`spread_dual_penalty` — Doble Penalización / Ambas Juntas):**
   - Probabilidad de victoria: $p = p_{\text{injury\_aware}}$ (el clasificador ya penaliza la probabilidad por las bajas).
   - Margen proyectado: $\text{proj\_margin}_C = \Phi^{-1}(p_{\text{injury\_aware}}) \times 13.5 - \Delta_{\text{QB}, i}$.
   - Probabilidad de cover: $P(\text{home cover})_C = \Phi\left(\frac{\text{proj\_margin}_C - S_i}{13.5}\right)$.

4. **Comparadores de Referencia:**
   - **Línea de Spread Vegas (Mercado):** Margen proyectado implícito del mercado = $S_i$.
   - **Ensamble Injury-Aware:** Diagnóstico usando el promedio de los 3 modelos sin ajuste QB.

---

## 3. Resultados Empíricos Fuera de Muestra (OOF)

### 3.1. Evaluación Global sobre los 188 Partidos OOF

| Variante | Margen MAE | Margen RMSE | Sesgo Medio (Bias) | Cover Brier | Cover Accuracy | Cover LogLoss |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **Variante A (Injury-Aware, sin ajuste QB)** | **9.901** | **12.825** | **+1.617** | **0.2574** | 0.511 | **0.7102** |
| **Variante B (Team-Only + ajuste QB)** | 9.990 | 12.904 | +1.680 | 0.2633 | **0.528** | 0.7241 |
| **Variante C (Doble Penalización: Ambas)** | 10.044 | 12.910 | +1.669 | 0.2627 | 0.506 | 0.7231 |
| *Diagnóstico: Ensamble sin ajuste QB* | 9.722 | 12.626 | +1.682 | 0.2545 | 0.511 | 0.7039 |
| *Línea de Spread Vegas (Mercado)* | 9.394 | 12.241 | +1.271 | — | — | — |

*Nota sobre Cover:* Evaluado en los 180 partidos sin push.

---

### 3.2. Evaluación en la Subpoblación Sensible con Bajas de QB ($\Delta_{\text{QB}} \ne 0$, $N = 33$)

En este subconjunto de 33 partidos donde existió reporte pregame verificado de lesión en mariscal titular (incluyendo bajas de Sam Darnold, Mitchell Trubisky, Drew Brees, Cam Newton, Ben Roethlisberger, Tyrod Taylor, Jimmy Garoppolo, Joe Flacco, Dak Prescott, etc.), el impacto del diseño es directo:

| Variante | Margen MAE | Margen RMSE | Sesgo Medio (Bias) | Cover Brier | Cover Accuracy | Cover LogLoss |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **Variante A (Injury-Aware, sin ajuste QB)** | **10.701** | **13.732** | +2.674 | **0.2735** | **0.545** | **0.7489** |
| **Variante B (Team-Only + ajuste QB)** | 11.603 | 14.150 | **+2.450** | 0.3035 | 0.515 | 0.8205 |
| **Variante C (Doble Penalización: Ambas)** | 11.517 | 14.181 | +2.970 | 0.3026 | 0.515 | 0.8194 |
| *Diagnóstico: Ensamble sin ajuste QB* | 11.051 | 13.644 | +2.887 | 0.2683 | 0.515 | 0.7352 |
| *Línea de Spread Vegas (Mercado)* | 9.894 | 12.488 | +2.530 | — | — | — |

---

### 3.3. Desglose Fold por Fold (Consistencia Temporal)

| Fold | Filas | Métrica | Var A (Sin ajuste QB) | Var B (Team + QB) | Var C (Doble penalización) | Ganador del Fold |
| :---: | :---: | :--- | :---: | :---: | :---: | :---: |
| **Fold 1** | 49 | **MAE**<br>**Cover Brier** | 9.143<br>**0.2535** | **9.069**<br>0.2570 | 9.208<br>0.2585 | **Var A en Brier** (Var B en MAE por 0.07 pts) |
| **Fold 2** | 45 | **MAE**<br>**Cover Brier** | **8.900**<br>**0.2707** | 9.253<br>0.2870 | 9.234<br>0.2854 | **Var A domina** (MAE mejor por >0.33 pts, Brier mejor por >0.014) |
| **Fold 3** | 45 | **MAE**<br>**Cover Brier** | **10.313**<br>**0.2270** | 10.656<br>0.2343 | 10.469<br>0.2274 | **Var A domina** (menor MAE y mejor Brier) |
| **Fold 4** | 49 | **MAE**<br>**Cover Brier** | 11.199<br>0.2777 | **10.978**<br>**0.2758** | 11.233<br>0.2801 | Var B |

*Observación Clave:* En 3 de los 4 folds (Folds 1, 2 y 3), **Variante A supera a la doble penalización (Variante C)** en Brier score de cover. En Folds 2 y 3, Variante A supera a Variante C en MAE por margen sustancial. En ningún fold la doble penalización (Variante C) es la mejor opción.

---

## 4. Análisis y Hallazgos Clave

1. **La Doble Penalización Degrada el Rendimiento Predictivo:**
   Restar el EPA de QB adicional sobre un margen que ya derivó de un clasificador `injury_aware` infla el error absoluto medio (MAE aumenta de 10.701 a 11.517, un incremento de **+0.816 puntos**) y degrada severamente la calibración de cover (el Brier empeora de 0.2735 a 0.3026). El modelo sobre-reacciona a la baja del mariscal, penalizando al equipo dos veces consecutivas.
2. **El Clasificador `injury_aware` Absorbe Eficientemente el Impacto de las Bajas:**
   Al incorporar `ppr_lost_pct` y `off_snaps_lost_pct`, el clasificador ya recibe la pérdida de producción del titular de forma proporcional a su rol previo. Derivar el margen directamente de la distribución Gaussiana estándar ($\Phi^{-1}(p) \times 13.5$) produce una proyección de margen más parsimoniosa y robusta que cualquier ajuste heurístico aditivo posterior.
3. **El Mercado de Spread Supera a los Modelos Estadísticos en Margen:**
   La línea oficial de Vegas tiene un MAE de 9.394 frente a 9.901 de Variante A (diferencia de 0.507 puntos a favor del mercado). Esto ratifica que los márgenes proyectados no deben presentarse como estimaciones infalibles del resultado, sino como herramientas probabilísticas descriptivas.

---

## 5. Decisión Ejecutiva bajo el Protocolo del Punto 6

Conforme a la regla prerregistrada:
> *"Mantendré el ajuste QB adicional solo si aporta mejora fuera de muestra sobre el modelo que ya usa features de lesiones; si no, quitaré esa segunda aplicación."*

**DECISIÓN FORMAL:**  
- **Se retira definitivamente el ajuste aditivo de QB al margen proyectado.**
- El margen y los scores proyectados en `sports_analytics_service.py` derivan **exclusivamente** de la probabilidad calibrada del clasificador (`proj_margin_raw = proj_margin_base = norm.ppf(p) * 13.5`).
- El estimador de EPA de QB pregame (`qb_lost_expected_epa`) permanece **estrictamente como métrica descriptiva y diagnóstica** en el contexto de disponibilidad (`nfl_availability_service.py` y `TeamMetricsPanel.tsx`), sin impacto aritmético en el margen ni en las probabilidades de spread/moneyline.

---

## 6. Aislamiento en la Cohorte Prospectiva (Punto 2 y Punto 6)

Para prevenir cualquier contaminación en el registro prospectivo inmutable (`NFLProspectiveCohortService`), se han configurado tres tags de variante independientes:
1. `variant_tag="baseline_logistic_c672f6a3"` (Variante A oficial: clasificador candidato congelado sin ajuste aditivo de QB).
2. `variant_tag="spread_team_only_with_qb_adj"` (Variante B: seguimiento prospectivo alternativo).
3. `variant_tag="spread_dual_penalty"` (Variante C: seguimiento prospectivo de doble penalización).

Cada variante acumula predicciones en `data/prospective_cohort/nfl_cohort_registry.jsonl` con aislamiento estricto por `variant_tag`, garantizando que la evaluación prospectiva de 1,000 partidos refleje exclusivamente el modelo seleccionado.
