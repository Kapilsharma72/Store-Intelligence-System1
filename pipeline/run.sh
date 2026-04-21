#!/usr/bin/env bash
set -euo pipefail

SIMULATE=false
API_BASE_URL="${API_BASE_URL:-http://localhost:8000}"
CLIPS_DIR="data/clips"
SAMPLE_FILE="data/sample/sample_events.jsonl"

for arg in "$@"; do
    case $arg in
        --simulate)
            SIMULATE=true
            ;;
    esac
done

if [ "$SIMULATE" = true ]; then
    echo "Running in simulate mode: replaying $SAMPLE_FILE at 10x speed"
    if [ ! -f "$SAMPLE_FILE" ]; then
        echo "ERROR: Sample file $SAMPLE_FILE not found"
        exit 1
    fi
    python3 pipeline/simulate.py --input "$SAMPLE_FILE" --speed 10 --api-url "$API_BASE_URL"
else
    # Check for video files
    shopt -s nullglob
    videos=("$CLIPS_DIR"/*.mp4)
    if [ ${#videos[@]} -eq 0 ]; then
        echo "WARNING: No video files found in $CLIPS_DIR and --simulate not set. Exiting."
        exit 0
    fi

    echo "Processing ${#videos[@]} video file(s)..."
    for video in "${videos[@]}"; do
        echo "Processing: $video"
        python3 pipeline/process_video.py --video "$video" --api-url "$API_BASE_URL"
    done
fi
