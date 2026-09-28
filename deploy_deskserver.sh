#!/usr/bin/env bash
# Deploy Comack to comack-server
set -e

DEST_HOST="${1:-comack-server.example-tailnet.ts.net}"
DEST_DIR="${2:-/opt/comack}"

echo "=========================================="
echo " Deploying Comack to $DEST_HOST:$DEST_DIR "
echo "=========================================="

# Rsync project files excluding venv and git
rsync -avz --exclude '.venv' --exclude '.git' --exclude '__pycache__' --exclude 'data/*.db' \
    ./ "$DEST_HOST:$DEST_DIR"

echo "Sync complete."
echo "On $DEST_HOST, you can run:"
echo "  cd $DEST_DIR && docker compose up -d --build"
echo "or run as systemd service:"
echo "  sudo cp $DEST_DIR/comack.service /etc/systemd/system/"
echo "  sudo systemctl daemon-reload && sudo systemctl restart comack"
