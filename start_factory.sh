#!/usr/bin/env bash
# ==============================================================================
# Griddio AI Factory - Orchestration Launcher
# ==============================================================================
# Inicia una sesión de tmux de 3 paneles:
#   - factory:0.0 (Izquierda arriba) -> Antigravity (Ejecutor)
#   - factory:0.1 (Derecha arriba)   -> Codex (Auditor/Planificador)
#   - factory:0.2 (Abajo a lo ancho) -> python3 factory_watcher.py
# ==============================================================================

set -euo pipefail

SESSION="factory"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ------------------------------------------------------------------
# CONFIGURACIÓN DE TUS COMANDOS CLI (Cámbialos si usas banderas extra)
# ------------------------------------------------------------------
AGY_CMD="agy"
CODEX_CMD="codex --dangerously-bypass-approvals-and-sandbox"

# 1. Matar sesión previa si existe para empezar 100% limpio
tmux kill-session -t "$SESSION" 2>/dev/null || true

# 2. Asegurar que STATE.md empiece en IDLE para que NO dispare nada a los 3 segundos
if [ -f "$SCRIPT_DIR/.ai-factory/STATE.md" ]; then
    sed -i '1s/^STATUS:.*/STATUS: IDLE/' "$SCRIPT_DIR/.ai-factory/STATE.md"
fi

echo "Iniciando AI Factory en tmux..."

# 3. Crear sesión y forzar que las ventanas/paneles empiecen en 0
tmux new-session -d -s "$SESSION" -c "$SCRIPT_DIR"

# Guardamos el ID real del panel de Antigravity (Izquierda Arriba)
PANE_AGY=$(tmux display-message -p -t "$SESSION" '#{pane_id}')

# Dividir verticalmente para el Watcher (Abajo) y guardar su ID
PANE_WATCHER=$(tmux split-window -v -p 30 -t "$PANE_AGY" -c "$SCRIPT_DIR" -P -F '#{pane_id}')

# Dividir el panel superior horizontalmente para Codex (Derecha Arriba) y guardar su ID
PANE_CODEX=$(tmux split-window -h -p 50 -t "$PANE_AGY" -c "$SCRIPT_DIR" -P -F '#{pane_id}')

# Guardar los IDs exactos de los paneles para que factory_watcher.py nunca falle
echo "PANE_AGY=$PANE_AGY" > "$SCRIPT_DIR/.ai-factory/panes.env"
echo "PANE_CODEX=$PANE_CODEX" >> "$SCRIPT_DIR/.ai-factory/panes.env"

# 4. Lanzar Antigravity (agy) en el panel izquierdo
tmux send-keys -t "$PANE_AGY" "$AGY_CMD" C-m

# 5. Lanzar Codex en el panel derecho
tmux send-keys -t "$PANE_CODEX" "$CODEX_CMD" C-m

# 6. Lanzar el Watcher en el panel inferior
tmux send-keys -t "$PANE_WATCHER" "python3 factory_watcher.py" C-m

# Enfocar el panel del Watcher abajo para ver las instrucciones
tmux select-pane -t "$PANE_WATCHER"

# Conectarse a la sesión
if [ -n "${TMUX:-}" ]; then
    tmux switch-client -t "$SESSION"
else
    tmux attach-session -t "$SESSION"
fi