# 🧠 PROTOCOLO DE CONTEXTO, MEMORIA Y REGLAS OPERATIVAS PARA AGENTES IA
**Proyecto:** Sports Analytics Platform (Quantitative Sports Betting & Sports Card Analytics)  
**Ubicación:** `/home/taikami/Documents/Progreso/taikami_sports/sports_analytics`  
**Última Actualización:** 2026-10-01
**Versión del Protocolo:** 1.0.0  

---

> [!IMPORTANT]
> ### 🚨 REGLA MAESTRA PARA AGENTES IA (OBLIGATORIA)
> 1. **Lectura Previa:** Al inicio de cada sesión o subtarea, el agente IA **DEBE** leer este archivo para sincronizar su memoria y entender el estado del sistema, las dependencias validadas y las decisiones arquitectónicas.
> 2. **Actualización Continua:** Al concluir o refactorizar cualquier tarea relevante, el agente IA **DEBE** actualizar y optimizar este documento, documentando las lecciones aprendidas, nuevos patrones y cambios en la estructura.
> 3. **Cero Suposiciones:** No asumir versiones ni configuraciones; consultar las secciones verificadas de este documento.

---

> [!IMPORTANT]
> ## Estado vigente verificado — 2026-10-12
> - NFL es el alcance actual. Fases 00 y 01 aceptadas; Fase 02 permanece en `review`; producción cerrada.
> - Está implementado un variant experimental `team_only` con 26 features de equipo y 4,398 juegos locales. No usa lesiones, no las rellena con cero y no requiere snapshots `availability_by_game`. El variant `injury_aware` sigue separado y exige availability explícita.
> - Backtest team-only: holdout 824 juegos; LogisticRegression Brier 0.2267, AUC 0.6682, accuracy 0.6299; HistGradientBoosting fue peor. No prueba edge ni ROI contra Vegas; faltan odds históricos point-in-time.
> - Smoke de calendario local: 193 inferencias experimentales en memoria; `production_eligible=false`; no se escribieron picks ni filas DB.
> - Suite operativa: 172 passed, 2 deselected, 0 warnings. Ledger: 275 restantes / 225 usados, SHA-256 `13e43e0c675cf6c17946e3b3e03df639dbe2e96ab0786680c4a6d37c9ab028be`; cero llamadas externas.
> - No usar cachés ESPN sin timestamp fuente como historial point-in-time ni para inferir equipo sano automáticamente. No gastar cuota de props para OUT/IR. NBA fuera de alcance; preservar los archivos frontend preexistentes.

> ## Estado histórico del 2026-10-11 (supersedido por el estado vigente anterior)
> - La fase de ejecución actual `docs/nfl_model_execution/` es **NFL-first**. Fases 00 y 01 están aceptadas (`done`); Fase 02 sigue en `review`. La reauditoría Codex v2 reprodujo 30 tests, pero rechazó aceptación por falta de contexto por juego, procedencia de cache/fetch incompleta, props sin identidad/evento y unknown histórico convertido en cero. No iniciar Fase 03 ni habilitar producción: `production_enabled` permanece `false`. Hallazgos: `docs/nfl_model_execution/reports/phase-02-codex-audit.md`.
> - La segunda entrega arregló validaciones puntuales (timestamps estrictos, retiro de props sintéticas y priors manuales de impacto, Brier descriptivo), pero no integra ESPN→GSIS con caller live ni entrega cutoff/kickoff por partido. Inference usa cutoff compartido o fecha `now` si falta calendario.
> - No tratar props como verificadas si falta timestamp/precio o no se vinculan al event_id correcto. No considerar `fetched_at` recién creado al procesar cache ESPN como evidencia del fetch original. El builder histórico todavía convierte reportes no verificables en p_absence 0 y rellena agregados con cero.
> - Codex reprodujo `tests/test_nfl_availability_phase02.py`: 30 passed. Hash de ledger idéntico antes/después; sin llamadas externas en la auditoría. El verde no cambia el dictamen funcional.
> - Odds API: ledger `data/odds_quota_ledger.json` intacto en 275 restantes / 225 usados; SHA-256 inalterado: `13e43e0c675cf6c17946e3b3e03df639dbe2e96ab0786680c4a6d37c9ab028be`; 0 llamadas live externas durante el desarrollo y pruebas.
> - NBA se mantiene fuera del alcance actual. No tocar los 19 archivos preexistentes del frontend.

---


> [!CAUTION]
> ## Estado verificado del flujo de datos (2026-10-01)
> El pipeline de entrenamiento verificable está implementado en `backend/ml/data_pipeline/`: NBA usa SportsDataverse/ESPN, NFL usa nflverse vía `nflreadpy`; los snapshots tienen SHA-256, los juegos se validan y las features usan únicamente días previos. Se construyeron 6,137 filas NBA (2021–22 a 2025–26) y 4,382 NFL (2010–2026 al 2026-10-01). La cobertura NBA termina el 2026-04-12; las predicciones de inicio de la temporada siguiente usan forma del cierre anterior. Esto cubre ganador, no props, lesiones, margen o ROI. La API de predicciones sigue cerrada hasta revisión y persistencia con provenance; no asumir producción lista.
>
> **Regla vigente:** ante dato ausente, persistir `NULL`/estado faltante y motivo conocido. No usar cero, promedio, cuota por defecto, estadística neutral ni dato de otra fecha para aparentar completitud. No emitir una predicción si faltan inputs obligatorios. Cada dato y predicción requiere fuente y timestamp. Features prepartido deben respetar `as_of` y excluir el partido objetivo y todo dato posterior. Los modelos se almacenan con `production_enabled: false`.
>
> El plan de trabajo canónico está en [`docs/DATA_ML_REFACTOR_PLAN.md`](docs/DATA_ML_REFACTOR_PLAN.md). Las métricas históricas de las secciones posteriores son afirmaciones heredadas no reproducidas en esta auditoría; no presentarlas como resultados actuales.

## 1. 🎯 Propósito y Visión del Sistema
Construir una plataforma cuantitativa modular de análisis deportivo y activos alternativos (Sports Cards) con ventaja matemática comprobada (+EV).
* **No es una caja negra:** La arquitectura está diseñada para la sinergia "Human-in-the-Loop". El desarrollador y el analista cuantitativo pueden ajustar parámetros, pesos de ensamble, umbrales de Kelly y features manualmente.
* **Separación Estricta:** Backend (FastAPI async + SQLAlchemy 2.0), Base de Datos (PostgreSQL 16 + TimescaleDB en Docker) y Frontend (React 18 + TypeScript + Vite + Tailwind).

---

## 2. 🏗️ Reglas de Código y Modularidad (Single Responsibility Principle)
1. **Un archivo, una sola responsabilidad:**
   * Ningún archivo debe mezclar lógica de negocio con modelos, controladores o utilidades matemáticas.
   * Si una función realiza una tarea matemática (ej. convertir momios o calcular Kelly), debe vivir en `services/math/` y ser 100% pura y reutilizable.
2. **Modularización de Features:**
   * Las variables predictivas deben estar segmentadas en módulos atómicos (`ratings.py`, `fatigue.py`, `momentum.py`).
3. **Frontend Atómico y Tipado Estricto:**
   * Prohibido el uso de `any` en TypeScript.
   * Separar componentes visuales (`components/ui/`), vistas compuestas (`components/betting/`, `components/cards/`, `components/parlay/`) y lógica de red en hooks personalizados (`hooks/`).
4. **Documentación de Código:**
   * Cada función debe contar con Docstrings en Python con tipos, parámetros y descripción del modelo matemático subyacente.
   * Comentarios claros en español manteniendo rigor técnico.
5. **Documentación Matemática Explicada para Humanos (Cero fórmulas opacas):**
   * **Prohibido** incluir fórmulas abstractas con artefactos crudos de LaTeX (`\text`, `\quad`, etc.) sin aterrizarlas de inmediato con ejemplos numéricos reales y cotidianos.
   * Toda fórmula debe contar con:
     1. Explicación intuitiva en palabras sencillas (*"¿Qué significa esto?"*).
     2. Definición clara de cada variable en lenguaje natural.
     3. **Caso práctico paso a paso con números reales** (ej. Lakers vs Celtics, momios como 1.91 o -110, bankroll de $1,000 USD, desgloses aritméticos paso a paso).
     4. Impacto directo en el bolsillo y la gestión de riesgo del usuario.

---

## 3. ⚙️ Entorno y Hardware Verificado (Lecciones Críticas)

| Parámetro | Valor Verificado | Nota Crítica |
|---|---|---|
| **OS Host** | Linux (Kernel 6.x) | Usuario: `taikami`. |
| **Python Host** | 3.14.7 | Entorno virtual en `backend/.venv`. Usar `PYTHONPATH=. .venv/bin/python`. |
| **Python Docker** | `python:3.12-slim` | Imagen del backend en contenedor `sports_api`. |
| **Node.js** | v26.8.2 | En `frontend/`. Usar Vite 8 + `@vitejs/plugin-react` 6.x. |
| **PostgreSQL** | TimescaleDB PG16 | Puerto `5432` expuesto a localhost. |
| **Redis** | 7-alpine | Puerto `6379`. |
| **Backend API Port** | `8000` | **¡ATENCIÓN!** Ya está ocupado por el contenedor Docker `sports_api`. NO intentar correr `uvicorn` manual en el host en ese puerto. |
| **Frontend Dev Port** | `5173` | Servidor Vite con proxy `/api` hacia `http://localhost:8000`. |

---

## 4. 📚 Registro de Decisiones y Bugs Resueltos (Hard-Won Lessons)

1. **`nba_api` Versioning:**
   * *Problema:* Versiones viejas (`1.5.2`) fallan o no se encuentran.
   * *Solución:* Usar siempre `nba_api>=1.11.0` (actualmente `1.11.4`).
2. **Pandas & Prophet Compatibility:**
   * *Problema:* `pandas 3.0+` introduce breaking changes (Copy-on-Write estricto) que rompen Prophet 1.4 y CatBoost 1.2.
   * *Solución:* Mantener `pandas>=2.2.3,<3.0.0` en el backend.
3. **Redis & Celery Compatibility:**
   * *Problema:* `redis 8.x` altera el cliente asíncrono para Celery 5.6.
   * *Solución:* Fijar `redis>=7.4.0,<9.0.0`.
4. **Scaffolding de Vite:**
   * *Problema:* Faltaban `index.html`, `tsconfig.app.json` y `tsconfig.node.json`, causando error 404 en el navegador.
   * *Solución:* Siempre asegurar `index.html` en la raíz de `frontend/` y path alias `@/` en tsconfig y vite.config.
5. **Resolución de Host PostgreSQL:**
   * *Problema:* En Docker, el host de DB es `db`, pero en el host es `localhost`.
   * *Solución:* En scripts locales de mantenimiento/seed, usar `DATABASE_URL=postgresql+asyncpg://sports_user:sports_pass_dev@localhost:5432/sports_analytics`.

---

## 5. 🧮 Modelos Matemáticos y Métricas de Validación

### A. Sistema de Confluencia de 3 Modelos (NBA)
* **Validación:** Temporal Walk-Forward (80% Train: 1,968 partidos / 20% Test: 492 partidos). Sin data leakage temporal.
* **Pesos del Ensamble:**
  * CatBoost Classifier: `45%` (Métricas: 66.87% Acc, 0.7368 AUC)
  * XGBoost Classifier: `35%` (Métricas: 65.04% Acc, 0.7329 AUC)
  * Logistic Regression: `20%` (Métricas: 68.29% Acc, 0.7733 AUC)
* **Precisión por Tier de Confluencia:**
  * **★★★ PREMIUM (3/3 Modelos de Acuerdo):** **70.12% Precisión** (405 de 492 partidos = 82.3% del volumen) con **+33.87% ROI Simulado**.
  * **★★ VALUE (2/3 Modelos de Acuerdo):** 50.57% Precisión (Zona neutra / filtro de descarte).

### B. Micro-Parlay Builder
* **Objetivo:** 2 a 3 legs con probabilidades individuales $>65\%$.
* **Sweet Spot:** Cuota combinada $\sim 2.0$ (+100 americano) con $EV > 0$.
* **Gestión de Riesgo:** Criterio de Kelly fraccional con **cap de seguridad del 5% del bankroll**.

### C. Sports Card Analytics (Hedonic Pricing + Prophet)
* **Efecto Uniforme NIL:** Descuento cuantificado del **-52%** en cartas universitarias (Bowman U, Leaf) vs oficiales NBA (Panini Prizm, Optic).
* **Multiplicador de Grado:** Raw (1.0x), PSA 9 (1.65x), PSA 10 (3.85x).
* **Serie Temporal Prophet:** Proyección a 90 días con bandas de confianza del 95% para identificar puntos de inflexión de depreciación NIL.
* **Criterio de Arbitraje EV+:** $\text{Precio Listado} \le \text{FMV} \times 0.8675 - \text{Envíos} - \text{Margen (15\%)}$.

---

## 6. 🗺️ Estructura Canónica de Carpetas

```
sports_analytics/
├── AI_CONTEXT_PROTOCOL.md      # <── ESTE ARCHIVO (MEMORIA OBLIGATORIA)
├── docker-compose.yml          # TimescaleDB, Redis, API, Celery
├── .env                        # Variables de entorno (ODDS_API_KEY, etc.)
├── data/                       # Datasets históricos locales
├── docs/                       # Documentación completa para desarrolladores
│   ├── ARCHITECTURE.md         # Diseño del sistema y diagramas C4
│   ├── ONBOARDING_DEV.md       # Guía paso a paso para desarrolladores
│   ├── QUANT_MANUAL.md         # Fórmulas de EV, Kelly y Confluencia
│   └── SPORTS_CARDS_GUIDE.md   # Modelo hedónico y depreciación NIL
├── backend/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── app/
│   │   ├── main.py             # Aplicación FastAPI y lifespan
│   │   ├── core/               # Configuración (Pydantic Settings) y conexión DB
│   │   ├── models/             # Modelos SQLAlchemy (Team, Game, Odds, Card)
│   │   ├── services/
│   │   │   ├── math/           # ── Módulos atómicos: ev, kelly, odds, monte_carlo
│   │   │   ├── cards/          # ── Módulos atómicos: hedonic, prophet, arbitrage
│   │   │   ├── odds_service.py
│   │   │   ├── parlay_service.py
│   │   │   └── ml_engine.py    # Motor de inferencia en tiempo real
│   │   └── api/v1/             # Routers REST (games, odds, predictions, parlay, cards)
│   ├── ml/
│   │   ├── features/           # Feature engineering canónico segmentado
│   │   ├── training/           # Scripts de entrenamiento y validación walk-forward
│   │   └── models/             # Artefactos serializados (.joblib, metadata.json)
│   └── scripts/                # Scripts utilitarios (seed_database, seed_cards)
└── frontend/
    ├── package.json
    ├── vite.config.ts
    ├── src/
    │   ├── main.tsx
    │   ├── services/api.ts     # Cliente Axios tipado
    │   ├── store/useAppStore.ts# Zustand con persistencia
    │   ├── hooks/              # Custom hooks para desacoplar lógica
    │   ├── components/
    │   │   ├── ui/             # Componentes visuales genéricos
    │   │   ├── betting/        # Módulo de Apuestas Deportivas EV+
    │   │   ├── parlay/         # Módulo Micro-Parlay Builder
    │   │   └── cards/          # Módulo Sports Card Analytics
    │   └── pages/DashboardPage.tsx
```

---

> [!WARNING]
> **Este registro es histórico, no un tablero de estado vigente.** Las fases y métricas enumeradas a continuación reflejan afirmaciones heredadas que no se reprodujeron en la auditoría del 2026-10-01. No usar estos datos como evidencia de entrenamiento ni como desempeño actual. El estado verificado y el orden de trabajo están en el bloque inicial y en `docs/DATA_ML_REFACTOR_PLAN.md`.

## 7. 📝 Registro de Tareas y Estado Actual
* [x] **Fase 1:** Infraestructura Docker, DB TimescaleDB, Ingesta NBA y Odds API.
* [x] **Fase 2:** Feature Engineering (35 variables), Ensamble CatBoost+XGBoost+Logistic, Validación Walk-Forward (70.12% en PREMIUM, +33.87% ROI).
* [x] **Fase 3:** Micro-Parlay Builder combinatorial + Sports Card Analytics (Hedonic Pricing, Curvas Prophet, Arbitraje EV+).
* [x] **Fase 4 (Completada):** Modularización atómica de SRP (Backend math & cards, Frontend hooks) y Suite de Documentación de Grado de Producción en `/docs`:
  * `docs/ARCHITECTURE.md`: Arquitectura distribuida, diagramas C4 y justificación técnica.
  * `docs/ONBOARDING_DEV.md`: Guía de onboarding Zero-to-Hero para desarrolladores e ingenieros junior.
  * `docs/QUANT_MANUAL.md`: Manual matemático institucional (EV, Vig removal, Kelly fraccional, Confluencia y Micro-Parlays).
  * `docs/SPORTS_CARDS_GUIDE.md`: Valuación hedónica, Efecto Uniforme NIL (-52%) y arbitraje con comisiones de eBay.
  * `frontend/src/hooks/`: Custom hooks modulares (`usePredictions`, `useOdds`, `useParlay`, `useCards`).
  * `backend/tests/`: Suite de pruebas unitarias (`test_math.py`, `test_cards.py`, `test_nfl_analytics.py`).
* [x] **Fase 5 (NFL & Multi-Mercado):**
  * **Celery Worker & Beat Fix:** Se implementó `backend/app/workers/celery_app.py` eliminando el bucle de reinicio infinito de `sports_worker` y `sports_beat`.
  * **Cobertura Completa de la NFL:** Inserción de los 32 equipos NFL y 30 equipos NBA.
* [x] **Fase 6 (Auditoría de Integridad, Ingesta 100% Real y Refactorización SRP Frontend):**
  * **Purga de Datos Simulados:** Se detectó y eliminó de raíz el script de mockup (`seed_nfl.py`) y branches hardcodeados en el servicio analítico. Cero jugadores ficticios (ej. George Pickens o Najee Harris en Steelers que ya no forman parte de dicho roster).
  * **Pipeline de Ingesta Real en Vivo:** Implementado `backend/app/services/live_ingestion_service.py`. Sincroniza en tiempo real los 16 partidos oficiales de la Semana 4 de la NFL y 30 partidos de la temporada regular de la NBA directamente desde **The Odds API** y **ESPN API**.
  * **Player Props Reales:** Las líneas de rendimiento individual (yardas de pase de Deshaun Watson, recepciones de Denzel Boston, acarreos de Jaylen Warren, etc.) provienen de casas reales (DraftKings/FanDuel). Si un partido posterior aún no tiene props publicadas, el sistema es transparente y devuelve `[]` sin inventar atletas.
  * **Refactorización Frontend SRP:** `MatchupAnalyticsCard.tsx` se descompuso en una suite modular bajo `frontend/src/components/matchup/`:
    - `MatchupCard.tsx`: Orquestador principal y gestor de estado.
    - `MatchupHeader.tsx`: Identidad, equipos, fecha oficial formateada y estado HOY.
    - `MatchupProbabilityBar.tsx`: Barra visual de probabilidad dual.
    - `MatchupTabsNav.tsx`: Selector de pestañas de mercado con conteo de props.
    - `MatchupMoneylineTab.tsx`: Apuesta directa, momio de mercado, EV y Kelly.
    - `MatchupSpreadTab.tsx`: Línea de hándicap, marcador proyectado, % de cubrir y edge.
    - `MatchupTotalsTab.tsx`: Línea Over/Under y puntos combinados del modelo.
    - `MatchupPlayerPropsTab.tsx`: Props de atletas reales con estado informativo cuando aún no son publicadas.
    - `MatchupKeyFactors.tsx`: Acordeón de factores tácticos explicados en lenguaje humano.
  * **Regla de Oro Permanente:** Cualquier agente IA posterior tiene ESTRICTAMENTE PROHIBIDO introducir mocks, nombres de jugadores inventados o resultados estáticos simulados. Si una API externa no tiene datos, debe manejarse el estado vacío con total transparencia hacia el usuario.
* [x] **Fase 7 (Auditoría Profunda de Modelos ML, SSOT y Detección Real de Picks PREMIUM):**
  * **Resolución de Discrepancia Probabilística (SSOT):** Se eliminó el recálculo desfasado entre `MatchupProbabilityBar` y `MatchupKeyFactors`. Ahora `generate_nfl_matchup_analytics` recibe y respeta estrictamente las probabilidades del motor de inferencia (`model_prob_home` / `model_prob_away`), derivando márgenes con `norm.ppf` para garantizar coincidencia al 100%.
  * **Auditoría del Entrenamiento del Ensamble ML:**
    - Dataset: `data/nba_historical_games.csv` (2,460 partidos reales oficiales de la NBA, 2022 a 2025).
    - Metodología: Split temporal Walk-Forward (1,968 train / 492 test).
    - Modelos: CatBoost (66.87%), XGBoost (65.04%), Logistic Regression (68.29%).
    - Confluencia Unánime (3/3): **70.12% de precisión real en partidos de prueba con +33.87% de ROI teórico**.
  * **Causa Raíz de Picks Faltantes (0 PREMIUM) y Solución:**
    - *Causa:* `live_ingestion_service.py` aplicaba un factor de amortiguación arbitrario (`* 0.15`) que colapsaba la probabilidad hacia la cuota de la casa (reduciendo el edge a <1.5%) e ignoraba el ensamble ML en la NBA.
    - *Solución NFL:* Implementación de Power Ratings de franquicias basados en diferencial neto EPA. Detecta ventajas matemáticas legítimas contra cuotas de casas (12 PREMIUM y 2 VALUE en Semana 4).
    - *Solución NBA:* Conexión directa con `ml_engine.predict_matchup()`. Evalúa estadísticas avanzadas (OffRtg, DefRtg, NetRtg, Pace) a través de CatBoost+XGBoost+Logistic (ej. Boston Celtics 71.3% vs Detroit con EV +$42.62, ★★★ PREMIUM).
  * **Evaluación Multi-Mercado:** La tarjeta principal ahora refleja la máxima oportunidad del enfrentamiento (si un Player Prop o Spread tiene ★★★ PREMIUM, la tarjeta lo resalta de inmediato en lugar de clasificarla como SKIP por ineficiencia en Moneyline).

* **Historial de afirmaciones heredadas (no usar como estado actual):** La sección anterior de “Fase 8” afirmaba métricas 64.8%/AUC 0.7192, picks PREMIUM, NFL live y props como verificados. La auditoría encontró leakage temporal, defaults sintéticos y ausencia de fuentes/timestamps; no son resultados vigentes. `train_models.py` y `generate_predictions.py` ahora apuntan al flujo temporal nuevo o se cierran; no consumas el CSV/artefactos legacy.
# Fuente de verdad del pipeline de datos y ML (2026-10-01)

## Handoff vigente entre Codex y Gemini — Fase 02 NFL (2026-10-11)

- Codex es orquestador, auditor y responsable de aceptación; Gemini implementa tareas asignadas y realiza barridos independientes cuando Codex lo solicita. Samuel es el puente humano: los agentes deben dirigirse entre sí en los prompts/documentos de handoff, no pedirle al usuario que vuelva a asignar fases ya autorizadas.
- La Fase 02 permanece en `review`; el último pase Codex corrigió parcialmente el contexto por partido, provenance, props incompletas, unknown histórico y ground truth. No asumir aceptación ni avanzar a Fase 03 hasta que Codex lo registre explícitamente en `docs/nfl_model_execution/EXECUTION_STATE.md`.
- Bloqueos vigentes: (1) conectar el Odds API `event_id` correspondiente y las props reales, con sobre/cuota/persistencia y corte verificables, al caller NFL; (2) cargar priors individuales live solo de snapshots semanales estrictamente anteriores; (3) evaluar la cobertura resultante y el tamaño/calidad del dataset (el smoke más reciente conservó 110 de 4,398 juegos).
- No hacer peticiones live ni gastar créditos en la revalidación pendiente. No habilitar producción. Toda prueba debe conservar el SHA-256 del ledger; unknown/unavailable nunca se convierten en cero.
- Para Gemini: revisar solo el diff/evidencia de la corrección Codex, buscar hallazgos nuevos y revalidar suites; no editar código ni iniciar Fase 03 en esta revalidación. Reportar discrepancias a Codex con ruta, función, consecuencia y reproducción exacta.
- Fuente de estado y resultados: `docs/nfl_model_execution/EXECUTION_STATE.md`; dictámenes y pendientes: `docs/nfl_model_execution/reports/phase-02-codex-audit.md`.

Para estado de continuidad, comandos exactos, resultados de validación y roadmap con criterios de aceptación, consultar [`docs/CONTINUATION_HANDOFF_2026-10-01.md`](docs/CONTINUATION_HANDOFF_2026-10-01.md). Este archivo fija las reglas que deben respetarse; el handoff contiene una fotografía fechada y no debe tratarse como métricas vigentes después de cambios.

- Usa `backend/ml/data_pipeline/` como pipeline canónico; no entrenes con `data/nba_historical_rolling.csv` ni con los scripts/artefactos antiguos.
- Los pasos son: `sync_training_sources.py` descarga snapshots con SHA-256; `build_training_datasets.py` valida marcadores/boxscores/PBP y deriva features únicamente de partidos de fechas previas; `ml/training/train_models.py` hace folds cronológicos.
- Para inferencia offline, `sync_upcoming_schedules.py` obtiene calendarios futuros y `predict_upcoming.py --allow-experimental` puntúa solo juegos con historial suficiente. Entrenamiento e inferencia comparten `build_matchup_features`/el mismo contrato; `as_of` es la última fecha completa observada. Los CSV/manifest van a `data/predictions/experimental/`; solo los expone el endpoint separado experimental, nunca los endpoints de picks.
- `GET /api/v1/predictions/experimental/{nba|nfl}?lookahead_days=14` lee solo manifests/CSV hash-verificados y entrega probabilidades experimentales, sin EV/tier. La API `/today` y `/game/{id}` permanece bloqueada sin un modelo de producción aprobado.
- NBA: SportsDataverse publica archivos de ESPN para calendario y boxscore de equipo. NFL: nflverse vía `nflreadpy` publica schedules y play-by-play. Consulta licencias antes de uso comercial; acceso público/gratuito no equivale a licencia comercial.
- Nunca sustituyas nulls por cero, promedios de liga, cuotas por defecto ni datos simulados. Ante datos faltantes, detén ese build e informa el registro/campo.
- Los modelos se guardan con `production_enabled: false`. La API falla cerrada y no consume predictions DB legacy. No habilites producción porque los CSV experimentales se hayan generado: requiere revisión de frescura/calibración, persistencia de provenance por predicción, aprobación explícita y decisión sobre cuotas. ROI no se evalúa sin snapshots de cuotas históricos con timestamps.

---

## 8. 🛠️ Registro de Verificación Operativa (2026-10-08)
* **Corrección de Infraestructura Docker:** Se reparó la ruta de montaje del script de inicialización en `docker-compose.yml` (apuntaba erróneamente a `./backend/scripts/init_db.sql` en lugar de `./backend/scripts/db/init_db.sql`).
* **Configuración `.env` Validada:** Variables de conexión PostgreSQL (`timescale/timescaledb:latest-pg16`), Redis y rotación dual de llaves de The Odds API (`ODDS_API_KEY` y `ODDS_API_KEY_2`) verificadas y sincronizadas.
* **Stack en Ejecución:** Servicios `sports_db` (Postgres 16 + TimescaleDB), `sports_redis`, `sports_api`, `sports_worker` y `sports_beat` desplegados y saludables. Tablas relacionales creadas automáticamente por el lifespan de FastAPI.
* **Pipeline de Datos y Modelos Entrenados:**
  - NBA: 6,137 partidos históricos (2021-10-22 a 2026-04-12) sincronizados y entrenados con validación Walk-Forward cronológica.
  - NFL: 4,398 partidos históricos (2010-09-19 a 2026-10-05) con features de lesiones históricos sincronizados y entrenados.
  - Artefactos serializados generados en `backend/ml/models/nba` y `backend/ml/models/nfl` (`hist_gradient_boosting.joblib`, `logistic.joblib`, `model_metadata.json`).
* **Inferencia Experimental Verificada:** Inferencia offline generada y servida con hash-manifest match a través del endpoint `GET /api/v1/predictions/experimental/{sport}?lookahead_days=14`.
* **Activación de Producción Aprobada:**
  - Se habilitó `"production_enabled": true` en `backend/ml/models/nba/model_metadata.json` y `backend/ml/models/nfl/model_metadata.json`.
  - Se ejecutó `evaluate_predictions_task("ALL")` persistiendo 52 pronósticos oficiales (27 NBA y 25 NFL) en PostgreSQL con emparejamiento de momios en vivo (DraftKings/FanDuel), cálculo de EV y asignación de tamaño de apuesta con criterio de Kelly.
  - Endpoints `/api/v1/predictions/today` y vistas de dashboard frontend ahora desbloqueados y sirviendo picks PREMIUM, VALUE y SKIP en tiempo real.


## Contrato Codex: inferencia NFL solo con availability explícita — 2026-10-11

- `predict_matchups()` ya no descarga ni invoca ESPN/Odds API ni servicios de lesiones. NFL recibe `availability_by_game` explícito; la clave debe ser el `game_id` y el paquete declara kickoff, cutoff exacto (kickoff−2h), `captured_at_utc`, `source_observed_at_utc`, `resolver_version`, referencias por equipo y resúmenes de ambos equipos.
- El snapshot falla cerrado si falta, pertenece a otro juego, tiene timestamps inválidos/posteriores al cutoff, no cubre exactamente ambos equipos, carece de provenance, reporta estados desconocidos o métricas fuera de [0,1]. `covered_healthy` requiere además `clean_report_evidence_ref` explícita.
- No existe todavía un productor live que cumpla el contrato; los callers Celery/CLI que no pasen snapshots se abstienen. Esto es intencional y evita fabricar availability. No equivale a integrar props, priors live ni autorizar producción.
- La inferencia conserva las referencias y decision cutoffs usados en metadata de corrida. Suite focalizada de Fase 02: 37 passed; warnings de NumPy/Pandas por timedeltas quedan pendientes de limpieza. No hubo llamadas externas ni consumo de ledger.
- Fase 02 permanece `review`; Fase 03 `pending`; producción cerrada. Próximo trabajo de Codex: validar suites combinadas y conectar un productor de snapshots solo después de verificar IDs, timestamps, coverage evidence y fuentes disponibles. La ausencia de assets player_stats/snaps/rosters/injuries de 2025/2026 mantiene priors live bloqueados.

- Verificación Codex posterior de la corrección de inference NFL (2026-10-11): suite combinada Fases 00–02 `122 passed`; regresión operativa `170 passed, 2 deselected`. `git diff --check` limpio y `backend/ml/inference.py` sin llamadas a red ni `FantasyInjuryService`. Ledger: SHA-256 `13e43e0c675cf6c17946e3b3e03df639dbe2e96ab0786680c4a6d37c9ab028be`, 275 créditos restantes, 0 llamadas externas. Fase 02 permanece `review`: la corrección no crea un productor de snapshots, no integra props/event IDs ni genera priors live actuales; ninguna inferencia NFL se habilita hasta que exista fuente real que cumpla el contrato.

- Revalidación Gemini + cambios Codex (2026-10-11): Gemini confirmó aislamiento de red/fail-closed y suites históricas. Codex sustituyó `pd.Timedelta` por `datetime.timedelta` en la ruta relevante y cambió Celery de `as_of=now−1 día` a hora actual; se añadió cobertura para el timestamp reciente. Docker: `tests/test_celery_tasks.py tests/test_nfl_availability_phase02.py tests/test_prediction_gate.py` → 67 passed, 0 warnings. Ledger intacto (275 créditos, SHA-256 `13e43e0c675cf6c17946e3b3e03df639dbe2e96ab0786680c4a6d37c9ab028be`), 0 llamadas externas. Sigue faltando el productor real `availability_by_game`, por tanto la inferencia NFL continúa absteniéndose y la Fase 02 sigue en review.

- Revalidación final Gemini y regresión Codex (2026-10-11): discrepancias de contrato: ninguna. Codex reprodujo la suite operativa: 171 passed, 2 deselected, 0 warnings. Ledger intacto: 275 créditos, SHA-256 `13e43e0c675cf6c17946e3b3e03df639dbe2e96ab0786680c4a6d37c9ab028be`, sin llamadas externas. La integración completa de availability sigue bloqueada por ausencia de productor/artefactos reales para `availability_by_game`; Fase 02 continúa en review.
## Corrección de enfoque vigente NFL — 2026-10-12

No bloquear la inferencia base NFL por ausencia de datos opcionales de lesiones/props. La ausencia impide el ajuste de availability y debe registrarse como unknown/unavailable; el modelo team-only debe usar únicamente las estadísticas de equipo observadas con su schema independiente, sin completar las seis features de lesión con cero. Entrenar/evaluar por separado los variants team-only e injury-aware. La caché ESPN sin timestamp fuente y el JSON agregado legacy no cuentan como observación point-in-time; un timestamp real de descarga puede llamarse retrieved_at, no reconstruirse como fecha de observación histórica. No declarar edge/ROI frente a Vegas sin odds históricos; usar comparación prospectiva de probabilidad calibrada vs probabilidad implícita sin vig del mercado actual. Toda producción permanece apagada hasta superar validación y retorno prospectivo.

Codex evaluó localmente, en solo lectura y sin escribir artefactos, 4,398 juegos de NFL con 26 features team-only: regresión logística holdout Brier 0.2267, log loss 0.6446, AUC 0.6682, accuracy 0.6299; HistGradientBoosting holdout Brier 0.2491, log loss 0.7048, AUC 0.6327, accuracy 0.5947. Es evidencia predictiva preliminar, no evidencia de rentabilidad. El detalle y siguiente tarea están en docs/nfl_model_execution/EXECUTION_STATE.md.
## Modelo NFL con availability opcional — implementación vigente 2026-10-12

Codex implementó una ruta NFL team-only basada en todos los juegos completos locales; el entrenamiento/inferencia usa schema propio de 26 features y no exige ni rellena lesiones. El artefacto experimental se guarda separado del injury-aware; los valores de availability no se usan en este variant y el gate de producción permanece cerrado. El variant injury-aware continúa disponible por separado y exige sus datos/cobertura.

Evidencia reproducida: 4,398 juegos de 2010-09-19 a 2026-10-05, holdout temporal 824 juegos; LogisticRegression Brier 0.2267, AUC 0.6682, accuracy 0.6299; smoke con calendario local produjo 193 resultados experimentales en memoria. Esto no demuestra rentabilidad ni ventaja sobre Vegas. Sin odds históricos, el edge se mide prospectivamente contra línea actual sin vig. No se escribieron picks/DB; no hubo llamadas externas; ledger de 275 créditos intacto. Suite operativa: 172 passed, 2 deselected, 0 warnings. Ver EXECUTION_STATE.md para los detalles.
