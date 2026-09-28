#!/usr/bin/env bash
# Deploy Comack to comack-server
set -e

DEST_HOST="${1:-comack-server@comack-server.example-tailnet.ts.net}"
DEST_DIR="${2:-/home/comack-server/comack}"

echo "=========================================="
echo " Deploying Comack to $DEST_HOST:$DEST_DIR "
echo "=========================================="

# Rsync project files excluding venv, git, and the server's own .env
rsync -avz --exclude '.env' --exclude '.venv'--exclude '.venv_pio' --exclude '.git' --exclude 'firmware' --exclude 'simulator/__pycache__' --exclude '__pycache__' --exclude 'data/' \
    ./ "$DEST_HOST:$DEST_DIR"

echo "Sync complete."
echo "On $DEST_HOST, you can run:"
echo "  cd $DEST_DIR && docker compose up -d --build"
echo "or run as systemd service:"
echo "  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt   # first time only"
echo "  sudo cp $DEST_DIR/comack.service /etc/systemd/system/"
echo "  sudo systemctl daemon-reload && sudo systemctl restart comack"
