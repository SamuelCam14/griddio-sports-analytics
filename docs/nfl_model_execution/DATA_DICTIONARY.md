# Diccionario inicial de datos y features NFL

Este catálogo define el contrato previsto, no afirma que toda columna esté actualmente en el dataset. La Fase 01 debe añadir campo, unidad, versión, cobertura por season, null rate, clave y hash real del snapshot. Un dato no es disponible solo porque la librería tenga una función para cargarlo.

| Campo/grupo | Fuente propuesta | Momento válido | Transformación/consumidor | Regla si falta |
|---|---|---|---|---|
| `game_id`, teams, season/type | nflverse schedule; ESPN crosswalk solo para IDs | schedule published; identidad congelada | join de resultados/PBP/odds | no resolver ambigüedad: skip |
| `game_start_utc` | fuente con timestamp UTC inequívoco | antes del kickoff | cutoff y orden de games | no predecir si no puede establecerse |
| `home_score`, `away_score`, `home_win` | schedule/result feed | postgame | target/settlement solamente | target missing: excluir con motivo |
| raw PBP `epa`, `success`, `posteam`, `defteam` | nflverse PBP | final del partido observado | agregados del equipo para futuros partidos | juego incompleto: quarantine/skip |
| Off/Def EPA per play | derivado de PBP con filtros versionados | solo juegos previos | eficiencia rolling/ajustada | no feature si denominator/coverage insuficientes |
| Success/turnover/explosive/sack rates | PBP o player stats | solo juegos previos | grupo de eficiencia/contexto | conservar coverage; no default |
| QB/player box stats | nflverse player stats | partidos previos al target | rol/producción rolling | no completar por nombre no mapeado |
| Player snap shares | nflverse snap_counts | semanas previas al target | rol/depth/availability impact | null ≠ cero; source unavailable flag |
| Weekly roster/depth | nflverse weekly rosters/depth charts | snapshot/semana antes del kickoff | elegibilidad, team mapping, replacement | unknown si join ambiguo |
| Injury status | ESPN live; nflverse injury release para histórico si timestamp apto | observation timestamp < cutoff | availability resolver | after-cutoff/untimestamped → no usar |
| `did_play` | boxscore/snap target game | postgame | etiqueta ground truth resolver/evaluation | no enviar al winner model como input |
| Player prop listed/line/price | Odds API event markets/outcomes | snapshot <= decision cutoff | señal de mercado/resolver; price benchmark en su mercado | missing coverage → unknown, no OUT |
| h2h/spreads/totals odds | Odds API bookmakers | snapshot <= cutoff | no-vig market baseline y EV | sin precio fresco → no EV/pick |
| Weather forecast | fuente forecast con issue time | issue time < cutoff | feature contextual opcional | observed-after-start no usar |
| Venue/surface/roof | schedule/venue reference | estático y validado | contexto estable | unknown si source mapping no probado |
| `source_ref`, `snapshot_sha256` | manifests de fuentes | por fila/dataset | provenance | faltante invalida artefact production |
| `decision_cutoff`, `as_of` | job de inferencia | instante inmutable pregame | splitter, features, price selection | mandatory para comparar/servir |

## Grupos de features

Cada feature concreta requiere definición aprobada: nombre, unidad, formulas, sample denominator, lookback, shrinkage, timestamp max, nulos, deporte/market, y evidencia de no-leak. Mantener versiones de grupo: `team_form`, `play_efficiency`, `player_role`, `availability`, `game_context`, `market_features`.

## Datos de mercado

Probabilidad de mercado sin vig es referencia; probabilidad model es estimación propia. Guardar precios de outcomes completos para remover vig con método claro. No copiar la cuota de una casa distinta del precio de EV. Player prop record usa participant canonical ID, stat market, line, side, price y bookmaker; puntos alternativos son líneas diferentes, no duplicados.

