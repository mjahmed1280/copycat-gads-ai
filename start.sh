#!/bin/bash
# Cloud Run entrypoint: API on localhost (internal), Streamlit UI on $PORT (public, behind login).
set -u

gunicorn app:app --bind 127.0.0.1:5000 --workers 1 --threads 4 --timeout 1800 &
API_PID=$!

streamlit run ui.py \
  --server.port="${PORT:-8080}" --server.address=0.0.0.0 --server.headless=true \
  --browser.gatherUsageStats=false &
UI_PID=$!

# If either process dies, exit so Cloud Run restarts the container.
wait -n "$API_PID" "$UI_PID"
exit 1
