#!/usr/bin/env bash
# Wrapper fuer den Cron-Betrieb von smsammad.
#
# - Verhindert ueberlappende Laeufe desselben Tasks per flock (falls ein
#   Lauf laenger dauert als das Cron-Intervall, wird der naechste einfach
#   uebersprungen statt parallel zu laufen).
# - Schreibt IMMER ins Logfile (Erfolg wie Fehler), damit man im Zweifel
#   nachschauen kann.
# - Bei Erfolg keine Ausgabe auf stdout/stderr -> cron bleibt still, kein
#   Mail-Spam bei jedem Lauf.
# - Bei Fehler (Exit-Code != 0): Ausgabe zusaetzlich auf stderr, damit
#   crons eigenes MAILTO (falls auf diesem Host ein lokaler Mailtransport
#   eingerichtet ist) als zweite, grobe Absicherung greift -- unabhaengig
#   von der App-eigenen Fehlermail (config.ini: [notification]), die
#   praezisere Details liefert, aber z.B. bei einem kaputten config.ini
#   selbst nicht mehr greifen kann.
#
# Aufruf: cron_run.sh <ticket-to-sms|sms-to-ticket> [weitere Argumente]

set -uo pipefail

TASK="${1:?Usage: $0 ticket-to-sms|sms-to-ticket}"
shift

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG="$PROJECT_DIR/config.ini"
LOG_DIR="/var/log/smsammad"
LOG_FILE="$LOG_DIR/$TASK.log"
# Bewusst NICHT in /tmp: dort koennte durch einen versehentlichen Aufruf mit
# sudo/als root eine root-owned Lock-Datei zurueckbleiben, an der der
# normale Cron-User dann mit "Permission denied" scheitert (live passiert).
# LOG_DIR gehoert bereits eindeutig dem Cron-User (siehe Check unten), daher
# hier stattdessen.
LOCK_FILE="$LOG_DIR/$TASK.lock"

# /var/log/smsammad muss vorher mit Schreibrechten fuer den
# Cron-User angelegt sein (root-owned per Default) -- siehe README. Dieses
# Skript bewusst NICHT mit sudo/als root aufrufen -- sonst gehoeren Log-/
# Lock-Dateien root und der normale Cron-User scheitert danach mit
# "Permission denied".
if [ ! -w "$LOG_DIR" ]; then
    echo "FEHLER: $LOG_DIR existiert nicht oder ist nicht beschreibbar. " \
         "Einmalig einrichten: sudo mkdir -p $LOG_DIR && sudo chown $(id -un):$(id -gn) $LOG_DIR" >&2
    exit 1
fi

exec 200>"$LOCK_FILE"
if ! flock -n 200; then
    # Vorheriger Lauf desselben Tasks laeuft noch -- diesmal ueberspringen.
    exit 0
fi

OUTPUT="$(cd "$PROJECT_DIR" && python3 run.py --config "$CONFIG" "$@" "$TASK" 2>&1)"
EXIT_CODE=$?

{
    echo "=== $(date -Is) (exit $EXIT_CODE) ==="
    echo "$OUTPUT"
} >> "$LOG_FILE"

if [ "$EXIT_CODE" -ne 0 ]; then
    echo "$OUTPUT" >&2
fi

exit "$EXIT_CODE"
