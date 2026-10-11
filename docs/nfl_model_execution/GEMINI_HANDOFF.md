# Protocolo de traspaso Codex ↔ Gemini

## Rol

Gemini ejecuta cambios de una fase asignada. Codex conserva la orquestación, revisa evidencia y decide si una fase está aceptada. Samuel conserva autoridad sobre gasto, apuestas manuales y habilitación de producción.

Gemini no debe iniciar la fase siguiente sin aceptación de Codex en `EXECUTION_STATE.md`. Si una condición de aceptación falla, parar esa tarea, documentar la causa y pedir revisión; no ocultar fallos con defaults, fixtures de producción o ajustes de umbral.

## Paquete obligatorio de lectura por sesión

1. `AI_CONTEXT_PROTOCOL.md` — reglas vigentes y limitaciones del repo.
2. `docs/nfl_model_execution/README.md` — objetivo, límites y secuencia.
3. `docs/nfl_model_execution/EXECUTION_STATE.md` — fase/commit/status actual.
4. Documento específico de fase asignada.
5. `docs/DATA_ML_REFACTOR_PLAN.md` y `docs/CONTINUATION_HANDOFF_2026-10-01.md` si afecta ingestion, ML o producción.
6. `git status --short` y diff de archivos que piensa tocar; preservar cambios del usuario.

Si una regla o dato del handoff difiere del código ejecutable, el código y snapshots se inspeccionan de nuevo y la discrepancia se documenta. No se reescribe este plan para justificar un resultado después de verlo.

## Prompt de tarea recomendado

```text
Ejecuta únicamente la Fase NN descrita en
docs/nfl_model_execution/NN-*.md. Lee AI_CONTEXT_PROTOCOL.md,
docs/nfl_model_execution/README.md y EXECUTION_STATE.md antes de actuar.

Inspecciona el checkout/diff actual y preserva cambios preexistentes.
Sigue los pasos en orden. No empieces una fase posterior. No inventes
datos ni conviertas datos desconocidos en cero. No llames Odds API sin
un cupo reservado en EXECUTION_STATE.md y sin verificar el coste previsto
en docs/nfl_model_execution/01-data-and-quota.md.

Ejecuta las pruebas/validaciones indicadas para esta fase, registra
comandos y resultados, actualiza EXECUTION_STATE.md y entrega el formato
de cierre de GEMINI_HANDOFF.md. Si un prerequisito o criterio falla,
detente y reporta la causa; no debilites el criterio para marcar done.
```

## Reglas de comunicación documental

- Un trabajo por turno; tarea atómica y de una fase.
- Actualizar `EXECUTION_STATE.md` antes de comenzar (`in_progress`) y al terminar (`review`, `blocked` o `failed`).
- Cada métrica debe apuntar a dataset hash, manifest, período, split y comando que la generó.
- Cada llamada externa con costo debe figurar en el registro de cuotas antes y después. La clave nunca aparece en texto/terminal/reportes.
- No modificar `README.md` del plan para marcar progreso: usar `EXECUTION_STATE.md` y dejar los criterios inmutables. Cambios a criterios requieren revisión de Codex antes de implementarlos.
- Gemini no decide sus propios criterios de aceptación ni cambia `production_enabled` a true.
- Si aparecen datos reales diferentes de los asumidos, registrar nueva observación en catálogo y pedir revisión de contrato antes de adaptar el modelo.

