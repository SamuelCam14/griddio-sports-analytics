# Fase 00 — Gobernanza, gate y baseline reproducible

## Objetivo

Crear una línea base única y proteger a los usuarios de picks que todavía no tienen evidencia de ventaja. No mejorar features todavía.

## Prerrequisitos

- Usuario asigna esta fase a Gemini.
- Revisar protocolo AI y estado local; no descartar modificaciones previas.
- No consumir Odds API en esta fase salvo una llamada de saldo autorizada y sin cuota, si está disponible; registrar el método.

## Pasos

1. Registrar branch, commit, `git status --short`, versiones Python/dependencias y los hashes existentes de datasets/artefactos sin imprimir secretos.
2. Confirmar todos los consumidores del modelo NFL, endpoint `/today`, jobs Celery y rutas frontend que muestran predictions/EV. Documentar cada uno y su gate actual.
3. Cerrar `production_enabled` para NFL con el mecanismo del proyecto y verificar que los endpoints de picks no sirven picks durante fases de construcción. Mantener disponible solo estado/experimental según el contrato actual.
4. Corregir la generación de metadata para usar booleanos Python (`False` por default) y asegurar que el proceso de entrenamiento no puede habilitar producción. El cambio es de seguridad operacional, no aprobación de modelo.
5. Comparar `data/quality_report.json`, `data/processed/nfl_training.manifest.json` (si existe) y `backend/ml/models/nfl/model_metadata.json`. Registrar hashes, fechas, filas, intervalo temporal, nombres/versiones de modelos y métricas; explicar divergencias. No equiparar reportes de 1-oct y metadata de 8-oct.
6. Revisar temporal splitter: demostrar con fechas/IDs qué filas están en cada fold y holdout. Añadir assertion de que train, validación y lockbox final no se solapan por fecha y que toda observación de train precede a test.
7. Inventariar cambios locales en los archivos de frontend relacionados. El resultado será una lista para reconciliar, no una orden de sobrescribir.
8. Crear reporte de baseline `docs/nfl_model_execution/reports/phase-00-baseline.md` con tabla de endpoints, consumidor, estado de gate, hash, métricas y limitación.

## Entregables

- Gate cerrado y verificado en NFL.
- Metadata de entrenamiento siempre deshabilitada y sin error de tipo/nombre.
- Reporte baseline reproducible que evita mezclar artefactos de distinto hash.
- Prueba/assertion para no-overlap temporal.
- Inventario de consumidores/endpoint/estado.

## Aceptación

- Ningún endpoint normal de picks devuelve predicción como lista para apostar con gate cerrado.
- El entrenamiento nunca deja `production_enabled=true`.
- Hay una sola identificación clara de dataset/artefacto y fecha por corrida.
- Las fechas de los folds y lockbox están explícitas, ordenadas y no se pisan.
- El reporte no llama “ROI” a accuracy/AUC ni usa métricas de la sección legacy del protocolo.

## Fallos y rollback

Si un endpoint ignora el gate, dejar el gate cerrado y reportar endpoint/response. No compensar con ocultamiento visual. Si el cambio de metadata afecta carga de modelos, restaurar funcionalidad con gate cerrado; nunca restaurar `true`.

