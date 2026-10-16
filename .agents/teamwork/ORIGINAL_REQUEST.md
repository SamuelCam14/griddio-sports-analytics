# Original User Request

## 2026-10-15T22:56:35Z

# Teamwork Project: Reevaluación y Entrega MVP NFL Analytics

Reevaluación y entrega funcional del MVP de la plataforma de analítica deportiva predictiva para la NFL, impulsada por un ensamble de 2 a 3 modelos independientes sin data leaks, un modelo dinámico de impacto de lesiones basado en fantasy points previos (sin pesos fijos ni imputación con ceros), y un motor de valor esperado (+EV), hándicap y microparlays conectado directamente al frontend existente con datos 100% reales.

Working directory: `/home/samuelc/Documents/Progreso/griddio-sports-analytics`
Integrity mode: development

## Requirements

### R1. Auditoría de APIs y Extracción de Datos 100% Reales (Zero-Synthetic & Zero-Imputation)
- Auditar y consultar las fuentes documentadas del proyecto (ESPN para lesiones y estadísticas, NFLverse / nflreadpy para calendarios y métricas de juego, The Odds API para líneas de mercado). Si falta alguna métrica para fantasy o cuotas, investigar e integrar fuentes públicas gratuitas y verificables.
- Prohibir estrictamente cualquier dato simulado, inventado o hardcodeado.
- Si un dato o métrica no existe para un partido o jugador, no imputar con 0 ni asignar penalizaciones manuales implícitas; modelar y manejar la ausencia de información de manera estadísticamente coherente sin alterar arbitrariamente las lecturas.

### R2. Modelo Dinámico de Lesiones Basado en Rendimiento Fantasy Previo
- Implementar la evaluación dinámica del impacto de bajas (jugadores cuestionables o descartados) correlacionando los reportes de lesiones de ESPN con los fantasy points acumulados exclusivamente en las semanas previas de la temporada activa (estricto corte temporal: nunca usar datos de la semana en juego para evitar temporal leakage).
- Evitar factores de ponderación fijos por posición; la penalización debe ser proporcional al impacto y producción reciente del jugador específico dentro del esquema de su equipo.
- Programar verificación continua de lesiones: seguimiento diario y recálculo obligatorio en la ventana previa al kickoff (cuando la NFL publica la lista oficial de inactivos).

### R3. Ensamble de Modelos Independientes y Confluencia (+EV)
- Diseñar, entrenar y evaluar de 2 a 3 modelos matemáticos o de machine learning independientes con arquitecturas y enfoques metodológicos diferenciados, priorizando el comportamiento y la tendencia de la temporada en curso sobre temporadas lejanas.
- Los modelos deben generar probabilidades de victoria propias e independientes de las cuotas de las casas de apuestas (Vegas).
- Usar las cuotas de mercado observadas para calcular el valor esperado (EV) y la ventaja matemática real.
- Emitir pronósticos y recomendaciones de alta confianza exclusivamente cuando exista confluencia (acuerdo entre los modelos independientes) y exista EV positivo (+EV) con cuotas mínimas de 1.40.
- Analizar líneas de handicap / spread (principales y alternas) para detectar dónde reside la mayor probabilidad con ventaja matemática para superar la línea del casino.
- Construir microparlays con ventaja matemática correlacionada con objetivo de retorno combinado >= 2.0x (+100) y EV positivo.

### R4. Infraestructura y Protección de Recursos Externos
- Utilizar el entorno Docker existente (`sports_db`, `sports_redis`, `sports_api`, `sports_worker`, `sports_beat`).
- Preservar la integridad del ledger de cuotas (`data/odds_quota_ledger.json`) para nunca consumir créditos de The Odds API durante ejecuciones de prueba o validaciones automatizadas.
- Garantizar que las consultas HTTP de lectura del frontend (GET) no disparen llamadas de red externas bloqueantes ni sincrónicas a proveedores terceros.

### R5. Entrega de MVP Integrado con Frontend
- Conectar los pronósticos en tiempo real generados por el backend a la interfaz de usuario existente en `frontend/` mediante endpoints FastAPI con marcas de tiempo (`observed_at`, `predicted_at`, `as_of`).
- El frontend debe renderizar los picks, probabilidades de los modelos, análisis de handicap y microparlays en tiempo real consumiendo exclusivamente datos reales del backend.

## Acceptance Criteria

### Integridad de Datos y Auditoría
- [ ] 100% de los datos en base de datos y memoria provienen de fuentes reales verificadas (cero datos simulados o inventados).
- [ ] No existen imputaciones artificiales de '0' ni pesos posicionales fijos en los pipelines de features ni en los modelos.
- [ ] El ledger `data/odds_quota_ledger.json` permanece inalterado y protegido tras todas las ejecuciones de pruebas.

### Dinámica de Lesiones y Fantasy
- [ ] Las métricas de impacto de lesiones se calculan dinámicamente mediante los fantasy points de semanas estrictamente previas al partido evaluado.
- [ ] Pruebas automatizadas demuestran que un jugador de alto impacto reciente genera mayor degradación dinámica en el equipo que un jugador con bajo rendimiento reciente, sin depender de jerarquías fijas por posición.
- [ ] El flujo de prepartido actualiza y recalcula predicciones con la lista de inactivos antes del kickoff.

### Ensamble y Reglas de Decisión (+EV)
- [ ] Al menos 2 modelos independientes implementados y validados con evaluación temporal walk-forward.
- [ ] La emisión de picks principales exige confluencia de modelos y EV > 0 con cuotas >= 1.40.
- [ ] Detección algorítmica de valor en spread / handicap y generación de microparlays con cuota objetivo >= 2.0x y EV+.

### Verificación Técnica del Sistema
- [ ] Suite completa de backend pytest ejecutada y aprobada al 100% dentro del contenedor `sports_api`.
- [ ] `npm run lint` y `npm run build` en `frontend/` ejecutan con 0 errores y 0 advertencias.
- [ ] Los endpoints `/api/v1/predictions/today?sport=NFL` y `/api/v1/parlay/recommended?sport=NFL` responden datos reales estructurados y consumibles por el dashboard.
