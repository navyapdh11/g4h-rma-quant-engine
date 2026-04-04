#!/bin/bash
# G4H-RMA Quant Engine V6.0 — Daily Autodeploy Scheduler
# ========================================================
# Runs autodeploy every day at 23:00 (11 PM)
# Runs as a background daemon with auto-recovery

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

SCHEDULER_PID_FILE="$SCRIPT_DIR/.scheduler.pid"
SCHEDULER_LOG="$SCRIPT_DIR/logs/scheduler.log"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" >> "$SCHEDULER_LOG"
}

# Cleanup on exit
cleanup() {
    log "STOP Scheduler daemon shutting down"
    rm -f "$SCHEDULER_PID_FILE"
    exit 0
}

trap cleanup SIGTERM SIGINT SIGHUP EXIT

log "START Daily scheduler initialized (target: 23:00)"

while true; do
    NOW=$(date '+%H:%M')
    
    if [ "$NOW" = "23:00" ]; then
        # Prevent duplicate runs within the same minute
        LAST_RUN_FILE="$SCRIPT_DIR/.last_autodeploy"
        
        if [ -f "$LAST_RUN_FILE" ]; then
            LAST_RUN=$(cat "$LAST_RUN_FILE")
            TODAY=$(date '+%Y-%m-%d')
            if [ "$LAST_RUN" = "$TODAY" ]; then
                # Already ran today, skip
                sleep 61
                continue
            fi
        fi
        
        log "TRIGGER Running daily autodeploy at $NOW"
        
        # Run autodeploy
        bash "$SCRIPT_DIR/autodeploy.sh" >> "$SCHEDULER_LOG" 2>&1
        EXIT_CODE=$?
        
        # Record today's run
        date '+%Y-%m-%d' > "$LAST_RUN_FILE"
        
        if [ $EXIT_CODE -eq 0 ]; then
            log "SUCCESS Daily autodeploy completed"
        else
            log "FAILED Daily autodeploy exited with code $EXIT_CODE"
        fi
        
        # Wait past this minute to avoid duplicate
        sleep 61
    fi
    
    sleep 30
done
