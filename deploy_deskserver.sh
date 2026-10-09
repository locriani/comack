#!/usr/bin/env bash
# Deploy Comack to a host you control
# Usage: ./deploy_comack-server.sh [user@host] [dest-dir]
#   user@host can be a plain LAN name, an IP, or a Tailscale hostname,
#   e.g. "comack@myhost.tail1234.ts.net" or "comack@192.168.1.20"
set -e

DEST_HOST="${1:-user@your-server}"
DEST_DIR="${2:-/home/your-user/comack}"

echo "=========================================="
echo " Deploying Comack to $DEST_HOST:$DEST_DIR "
echo "=========================================="

# Rsync project files excluding venv, git, and the server's own .env
rsync -avz --exclude '.env' --exclude '.venv' --exclude '.venv_pio' --exclude '.git' --exclude 'firmware' --exclude 'simulator/__pycache__' --exclude '__pycache__' --exclude 'data/' \
    ./ "$DEST_HOST:$DEST_DIR"

echo "Sync complete."
echo "On $DEST_HOST, you can run:"
echo "  cd $DEST_DIR && docker compose up -d --build"
echo "or run as systemd service:"
echo "  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt   # first time only"
echo "  sudo cp $DEST_DIR/comack.service /etc/systemd/system/"
echo "  sudo systemctl daemon-reload && sudo systemctl restart comack"
