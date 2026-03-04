#!/bin/bash
set -e

echo "Starting VoiceEditor Linux Installation..."

# 1. Update system and install dependencies
echo "Installing system dependencies (sudo required)..."
sudo apt-get update
sudo apt-get install -y curl git ffmpeg python3 python3-pip python3-venv

# 2. Install UV (Standalone installer)
if ! command -v uv &> /dev/null
then
    echo "Installing 'uv' package manager..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    source $HOME/.cargo/env
else
    echo "'uv' is already installed."
fi

# 3. Project Directory Setup
INSTALL_DIR="$HOME/VoiceEditor"
if [ ! -d "$INSTALL_DIR" ]; then
    echo "Current directory is being used for setup: $(pwd)"
    INSTALL_DIR=$(pwd)
else
    cd "$INSTALL_DIR"
fi

# 4. Sync dependencies and setup environment
echo "Syncing Python dependencies and downloading models..."
# This will run src/setup_env.py as defined in main.py setup
uv run main.py setup

echo "-------------------------------------------------------"
echo "Installation Complete!"
echo "Project Path: $INSTALL_DIR"
echo "You can now run the GUI using: uv run main_gui.py"
echo "Or use 'deploy_service.sh' to set up auto-start."
echo "-------------------------------------------------------"
