# Fase 07 — Frontend honesto, backend-calculated

## Regla

El frontend consulta y presenta; no infiere ni calcula datos deportivos. Formateo de fechas, porcentajes derivados de una respuesta (si el backend no los provee), tiers, win probabilities, líneas, EV, stakes, standings, top pick y simulación deben provenir del backend. La opción preferida es que el backend devuelva los valores ya listos para presentar junto con fuentes y vigencia.

## Pasos

1. Buscar en toda la aplicación NFL: arrays de equipos/jugadores con estadísticas, `??` con precios/probabilidades, fallback de cuotas, seeds/mock data, cifras en texto, simulaciones locales y fixtures importados en runtime. Revisar componentes, hooks, stores, API client y pages; separar stories/tests de producción.
2. Crear matriz componente → endpoint → response field → estado vacio → estado error → freshness. No dejar tarjetas conectadas a endpoint legacy sin estado 503/no-model.
3. Retirar parlay fallback ficticio y cualquier ranking generado localmente; backend devuelve `[]` o `unavailable` con reason.
4. Reemplazar la tabla fija “10k iteraciones” por resultado persistido del backend o empty state. El frontend no simula 10k eventos con números inventados.
5. Standings/metrics/top pick solo mostrar si backend provee fuente, temporada, cutoff y cobertura. Si la DB no tiene temporadas completas, responder unavailable y explicar cobertura.
6. MVP/TopPick/EV/pick tier solo derivan de predicciones API aprobadas, jamás nombre fijo o confiabilidad fija.
7. Manejar 503/409/429/timeout y respuestas `unavailable/stale/experimental`; no convertirlas en fake default o `0%` ambiguo.
8. Revisar diffs preexistentes antes de editar. Mantener diseño visual si es compatible, pero nunca conservar un fallback de dato ficticio por estética.
9. Agregar tests de componente que monten error/empty/stale/available con fixtures identificadas; asegurar que UI no altera valores devueltos.

## Aceptación

- Búsqueda estática y revisión manual no encuentran mocks/valores de deporte activos en UI.
- Backend es único lugar donde se calcula sport stats, prediction, odds no-vig, EV, Kelly, ranking, standing o simulation.
- Cada widget muestra source time/status o un estado vacío/error honesto.
- No renderiza “PREMIUM”, “confiabilidad”, ROI o recomendación cuando la API declara experimental/unavailable.
- La pantalla conserva identidad de modo NFL y no presenta datos NBA en ausencia de servicio NBA.

