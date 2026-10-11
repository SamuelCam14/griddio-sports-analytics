# Fase 08 — Shadow trial, evaluación prospectiva y kill criteria

## Objetivo

Medir utilidad fuera de muestra antes de gastar en fuentes premium o afirmar ventaja. Usuario coloca apuestas manualmente; la plataforma no controla sportsbook ni envía wagers.

## Antes del trial

1. Congelar versión de modelo/features/calibrador, markets permitidos, bookmaker/price rules y cut-off.
2. Definir por escrito el mínimo de observaciones/mercados y horizonte antes de evaluar. Elegirlo con análisis de potencia/incertidumbre, no después de ver ROI.
3. Elegir al menos un tramo prospective shadow: producir/persistir recomendaciones sin reportarlas como apuestas ganadoras. Guardar línea exacta disponible y closing line.
4. Si luego se autoriza trial manual con dinero real, el usuario define límite por wager, por semana y pérdida máxima; iniciar separado del bankroll general. No subir stake para recuperar pérdidas.
5. Activar panel de exposición/capturas solo después de tests de idempotencia y privacidad.

## Registro manual por pick

Guardar server pick id, timestamp de presentación, event/market/selection, probabilidad/EV del backend, bookmaker/price mostrado, freshness, cuota realmente aceptada, stake introducido por el usuario, estado settled/void/push y resultado. Registrar explícitamente si el usuario no apostó y motivo. No reemplazar price shown con price aceptado al medir recomendación y ejecución.

## Kill/continue

- **Kill inmediato de picks:** fuente requerida down/stale, precio no disponible, mapping incierto, metadata/hash no válido, drift de features, cuotas restantes alcanzan reserva, API reporta cuota agotada o UI no puede demostrar el snapshot mostrado.
- **No pasar a trial con dinero:** no supera baseline no-vig en holdout, calibración insuficiente, no hay evidencia de CLV o el backtest requiere valores no observables.
- **Cerrar/archivar modelo o mercado:** al llegar al horizonte y muestra preregistrados, si resultados prospectivos no apoyan valor después de vig/fees y la incertidumbre sigue siendo compatible con pérdida, no comprar datos premium ni seguir ajustando el mismo lockbox. Abrir hipótesis nueva requeriría un plan y periodo confirmatorio nuevos.
- **Continuar a evaluación ampliada:** solo si señal OOS, calibración, CLV y EV ejecutable pasan criterios preregistrados; revisión de Codex y decisión explícita del usuario.
- La cifra de profit aislada en una racha corta no es criterio de continue ni proof de ventaja.

## Aceptación

- Trial tiene definición congelada, registros completos y no hay lookahead.
- El usuario conserva control de apuestas y límites; no existe automated bet placement.
- Los reportes separan recomendaciones, ejecución real, precio presentado y precio ejecutado.
- La decisión final es `continue`, `revise-new-hypothesis` o `close`, con evidencias y autorización.

