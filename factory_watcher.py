#!/usr/bin/env python3
"""
Griddio AI Factory Watcher
Monitoriza el archivo .ai-factory/STATE.md y coordina los turnos entre
Antigravity (Ejecutor) y Codex (Auditor/Planificador) en tmux.
"""

import os
import time
import subprocess

STATE_FILE = ".ai-factory/STATE.md"
PANES_FILE = ".ai-factory/panes.env"

def get_panes():
    panes = {"PANE_AGY": "factory:0.0", "PANE_CODEX": "factory:0.1"}
    if os.path.exists(PANES_FILE):
        with open(PANES_FILE, "r") as f:
            for line in f:
                if "=" in line:
                    k, v = line.strip().split("=", 1)
                    panes[k] = v
    return panes

def read_status():
    if not os.path.exists(STATE_FILE):
        return "IDLE", 0
    mtime = os.path.getmtime(STATE_FILE)
    with open(STATE_FILE, "r", encoding="utf-8") as f:
        first_line = f.readline().strip()
    if first_line.startswith("STATUS:"):
        return first_line.split("STATUS:", 1)[1].strip(), mtime
    return "UNKNOWN", mtime

def send_to_tmux(pane_id, text):
    # -l envía el texto literal sin interpretar caracteres especiales, y luego enviamos Enter (C-m)
    subprocess.run(["tmux", "send-keys", "-t", pane_id, "-l", text], check=False)
    time.sleep(0.5)
    subprocess.run(["tmux", "send-keys", "-t", pane_id, "C-m"], check=False)

def main():
    panes = get_panes()
    print("==================================================================")
    print(" 🤖 AI FACTORY WATCHER ACTIVO")
    print(f" Panel Antigravity : {panes['PANE_AGY']}")
    print(f" Panel Codex       : {panes['PANE_CODEX']}")
    print("==================================================================")
    print(" Estado inicial puesto en IDLE para dar tiempo a que carguen agy y codex.")
    print(" -> Cuando veas que ambos cargaron arriba, abre otra terminal o edita")
    print("    .ai-factory/STATE.md y cambia la primera línea a:")
    print("    STATUS: WAITING_FOR_CODEX")
    print("==================================================================\n")

    last_triggered_status = "IDLE"
    last_mtime = 0

    while True:
        status, mtime = read_status()

        if status != last_triggered_status:
            if status == "WAITING_FOR_CODEX":
                print(f"[{time.strftime('%H:%M:%S')}] 🧠 Detectado WAITING_FOR_CODEX. Esperando 4s antes de enviar a Codex...")
                time.sleep(4)
                prompt = (
                    "Lee .ai-factory/DIRECTIVES.md, .ai-factory/BACKLOG.md, .ai-factory/MEMORY.md y .ai-factory/STATE.md. "
                    "Revisa el código con git diff, audita lo que hizo Antigravity, actualiza BACKLOG.md y STATE.md, "
                    "y al terminar cambia la primera línea de .ai-factory/STATE.md exactamente a STATUS: WAITING_FOR_ANTIGRAVITY "
                    "(o STATUS: DONE si todo el BACKLOG terminó)."
                )
                send_to_tmux(panes["PANE_CODEX"], prompt)
                last_triggered_status = status
                last_mtime = mtime

            elif status == "WAITING_FOR_ANTIGRAVITY":
                print(f"[{time.strftime('%H:%M:%S')}] 🚀 Detectado WAITING_FOR_ANTIGRAVITY. Esperando 4s antes de enviar a Antigravity...")
                time.sleep(4)
                prompt = (
                    "/boost Lee .ai-factory/DIRECTIVES.md, .ai-factory/MEMORY.md y .ai-factory/STATE.md. "
                    "Ejecuta al 100% las instrucciones que te dejó Codex en STATE.md. Cuando termines de programar y probar todo, "
                    "escribe tu reporte detallado en .ai-factory/STATE.md y cambia la primera línea exactamente a STATUS: WAITING_FOR_CODEX"
                )
                send_to_tmux(panes["PANE_AGY"], prompt)
                last_triggered_status = status
                last_mtime = mtime

            elif status in ("DONE", "HUMAN_HELP"):
                print(f"\n[{time.strftime('%H:%M:%S')}] 🛑 CICLO FINALIZADO CON ESTADO: {status}")
                print("Presiona Enter para cerrar este panel o Ctrl+C.")
                input()
                break

        time.sleep(3)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nWatcher detenido por el usuario.")