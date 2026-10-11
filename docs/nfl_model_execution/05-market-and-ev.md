# Fase 05 — Valor frente al mercado, EV y backtest

## Objetivo

Determinar si las probabilidades estiman valor a una cuota realmente obtenible. “Acertar el ganador” no es condición suficiente: una cuota mala puede tener EV negativo.

## Contrato de precio

Un price record debe tener `event_id`, sportsbook/bookmaker, market key, selection/participant, line/point si aplica, odds American/decimal, source timestamp, fetched timestamp, `decision_cutoff`, market status y mapping confidence. Nunca usar precio tomado después del partido ni “best odds” construido con casas no disponibles en la región/usuario.

## Comparación

- De-vig se calcula por mercado y bookmaker (o método ensemble documentado) y conserva probabilidades crudas y fair.
- Probabilidad modelo y fair-market se reportan por separado.
- EV se calcula con probabilidad modelo calibrada y cuota ejecutable exacta observada. Con pushes/voids, usar settlement probabilístico explícito; no tratarlos automáticamente como derrota.
- Comparar precio tomado a cutoff con closing market como CLV; no usar closing line como feature si el pick se tomó antes.
- Registrar razones de no-pick: edge insuficiente, odds stale, mercado no cubierto, evento suspendido, feature crítica unknown.

## Historical vs prospective data

1. Inventariar OddsSnapshots de DB y sus timestamps; verificar cobertura de NFL, bookmaker, mercado y archivo player props.
2. La documentación oficial dice que historical odds/properties dependen de plan pagado; el histórico de mercados adicionales inicia en 2023-05-03 y usa consultas caras. No consultar ni comprarlo en este plan sin saldo y permiso explícitos.
3. Con cuota existente, hacer pilot retrospectivo solo después de aprobar presupuesto específico; comparar coste estimado y medido antes de expandir. El histórico de props no es parte del ledger live de 500 créditos.
4. Sin histórico verificable suficiente, iniciar archivo prospectivo ahora y dejar ROI como `not_evaluable`; no reconstruir cuotas desde resultados ni completar datos de mercado por interpolación.

## Backtest

- Walk-forward, decisión a tiempo fijo (horizonte prepartido preregistrado) y precios capturados a ese horizonte.
- Simular precios, bookmaker y disponibilidad exactamente como API/UI los habría mostrado.
- Reportar pick count, volumen, odds distribution, profit, yield, drawdown, CLV, Brier/logloss, ROI con intervalos de incertidumbre y control por múltiples pruebas.
- Comparar flat stake primary con Kelly solo como sensibilidad posterior. No llamar “seguro” al Kelly ni permitir que un default aumente el stake.
- Segmentos separados por favorites/underdogs, dirección de desacuerdo, bookmaker, market, status de injuries y temporada.
- Preinscribir umbral de edge/odds/frescura sin mirar el lockbox; registrar cuando un precio no estaba disponible.

## Aceptación

- La ejecución de backtest solo consume cuotas observadas al cutoff y settlement real.
- Baseline no-vig y modelo se comparan en el mismo universo de eventos.
- Sin cuotas históricas, salida marca `not_evaluable`, no estima ROI.
- No se declara “ventaja” por una muestra pequeña sin incertidumbre y sin comparación con mercado.
- EV y staking son backend only y respetan ausencia de price/source/time.

