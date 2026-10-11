# Fase 03 — Catálogo de features NFL y dataset canónico

## Objetivo

Mantener una ruta repetible desde datos crudos hasta filas de modelado, con nombres, timestamps, reglas de agregación, owner y versión. Cada feature debe existir en el mismo contrato de entrenamiento e inferencia.

## Orden recomendado de conjuntos de features

Primero construir y evaluar grupos en forma incremental; no meter todas las columnas disponibles a la vez.

### Grupo A — Baseline temporal (obligatorio)

- Win rate, margen y puntos a favor/en contra solo con partidos anteriores.
- Rest days calculados por kickoff UTC real; identificar short week/bye y no confundir con NBA back-to-back.
- Separar regular season/postseason o incluir tipo de partido con análisis segmentado.
- Ventanas de forma como temporada, rolling corto/largo y/o ponderación exponencial definida con tuning solo en CV.

### Grupo B — Eficiencia de equipo desde nflverse PBP

- EPA/play y success rate por ofensiva y defensa.
- Número de jugadas válidas y cobertura; EPA con diferentes familias: pass/run, early downs, third/fourth down, red zone, explosive plays, turnovers/sacks.
- Construir opponent-adjusted efficiency solo con reglas que pueden calcularse con equipos observados antes del cutoff.
- Definir denominador y exclusiones: no-plays, penalties, kneels, spikes, special teams, garbage time si aplica. Mantener la definición en un feature version.
- Shrink/shrinkage para pocos juegos/plays; toda constante debe estimarse o justificarse con validación temporal, no elegirse para mejorar el holdout.

### Grupo C — Jugadores y profundidad

- Snap count y snap percentage por jugador/posición/equipo; rol reciente, titularidad/profundidad, share de producción previa.
- QB: participación, eficiencia histórica pregame y reemplazo; evitar que PPR sea proxy único.
- O-line/defensa: snap/role/depth e injury status si fuente y cobertura lo permiten.
- Datos NGS/FTN/Next Gen solo si disponibilidad/licencia/pipeline y provenance se verifican. El paquete SportsDataverse expone loaders adicionales, pero no se agrega por nombre sin demostrar campo útil.

### Grupo D — Contexto prepartido

- Localía, descanso, superficie/roof/stadium estables y travel si hay fuente válida.
- Weather solo si se tiene forecast pregame timestamped. Clima observado al kickoff puede ser post-cutoff y no se usa retrospectivamente como input.
- Lesión/disponibilidad según contrato de Fase 02.

### Grupo E — Mercado

- No mezclar market price como si fuera feature deportiva.
- Mantener dos modelos experimentales: (1) solo señal deportiva; (2) market-aware con odds pre-cutoff y features explícitas de precio/movimiento.
- Ambos compiten contra no-vig market baseline. El modelo market-aware se aprueba solo si mejora sobre precio de mercado usando datos capturados antes de su cutoff.

## Pasos de dataset

1. Crear schema por juego con `game_id`, `game_start_utc`, team IDs, season, season_type, target/settlement, `as_of`, manifest refs y feature version.
2. Mantener raw game results como targets; filtrarlos de las features del mismo partido.
3. Para cada feature crear una función pura `feature(as_of, records)`; devolver valor + refs + observed-at max + coverage/status.
4. Asegurar que cada fold construye rolling data desde el mismo universo temporal; tests de invariancia cambiando datos posteriores al kickoff no deben cambiar vector anterior.
5. Aplicar `require_columns`, validar unidades/valores finitos/rangos; no “arreglar” fallas imputando 0/medias/liga.
6. Crear razones de skip por target missing, schedule missing, PBP coverage, mapping, injury source o line absent; reportar conteos con cada build.
7. Comprobar duplicados de game/player/week y cambios de team/trade/roster. Evitar mezclar historial de un franchise ID inválido.
8. Versionar dataset y manifest con source asset hashes, code revision, package versions, cutoff, split spec, feature names, transformations y dataset hash.
9. Inference llama al mismo transformador con fuente de datos pregame; compara schema, order, types y version exacta con artifact. Rechaza artefactos incompatibles.

## Aceptación

- Dataset puede reconstruirse con los manifests congelados y coincide byte/hash cuando las dependencias quedan fijadas.
- Se puede inspeccionar para cualquier fila todos los inputs y los tiempos; todos estrictamente anteriores al `as_of`.
- Ninguna feature de disponibilidad usa boxscore de objetivo.
- Los motivos de exclusión y cobertura quedan reportados.
- La lista de `features` de metadata coincide exactamente con el contrato cargado al inferir.
- Reporte de calidad y metadata enlazan el mismo dataset hash.

