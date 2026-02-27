#!/bin/bash
set -e

echo "================================"
echo "  lmarena-cli installer"
echo "================================"

sudo apt update
sudo apt install -y python3 python3-pip python3-venv xclip

python3 -m venv venv
source venv/bin/activate

pip install --upgrade pip
pip install playwright rich

playwright install chromium
playwright install-deps

mkdir -p logs screenshots

echo ""
echo "✓ Installed. Run:"
echo "  source venv/bin/activate"
echo "  python3 arena.py"
