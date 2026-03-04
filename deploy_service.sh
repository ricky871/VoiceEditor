#!/bin/bash

# Configuration and variables
INSTALL_DIR=$(pwd)
SERVICE_NAME="voiceeditor"
SERVICE_FILE="${SERVICE_NAME}.service"
USER_NAME=$(whoami)
HOME_PATH=$HOME

# Find the absolute path of 'uv'
# If running with sudo, 'which' might search root's PATH, so we also check common user paths
USER_UV_PATH=$(sudo -u $USER_NAME which uv 2>/dev/null)
if [ -n "$USER_UV_PATH" ]; then
    UV_PATH="$USER_UV_PATH"
elif [ -f "$HOME/.cargo/bin/uv" ]; then
    UV_PATH="$HOME/.cargo/bin/uv"
elif [ -f "$HOME/.local/bin/uv" ]; then
    UV_PATH="$HOME/.local/bin/uv"
elif command -v uv >/dev/null 2>&1; then
    UV_PATH=$(command -v uv)
else
    echo "Error: 'uv' executable not found. Please ensure 'uv' is installed and in your PATH."
    exit 1
fi
echo "Using 'uv' at: $UV_PATH"

# --- Handle uninstallation ---
if [ "$1" == "uninstall" ]; then
    echo "Uninstalling VoiceEditor systemd service..."
    sudo systemctl stop $SERVICE_NAME || true
    sudo systemctl disable $SERVICE_NAME || true
    if [ -f "/etc/systemd/system/$SERVICE_FILE" ]; then
        sudo rm "/etc/systemd/system/$SERVICE_FILE"
        echo "Removed /etc/systemd/system/$SERVICE_FILE"
    fi
    sudo systemctl daemon-reload
    echo "Service uninstalled successfully."
    exit 0
fi

echo "Configuring the systemd service for Linux deployment..."

# 1. Generate the actual service file from the template
sed -e "s|{{USER}}|$USER_NAME|g" \
    -e "s|{{INSTALL_DIR}}|$INSTALL_DIR|g" \
    -e "s|{{HOME}}|$HOME_PATH|g" \
    -e "s|{{UV_PATH}}|$UV_PATH|g" \
    voiceeditor.service.template > $SERVICE_FILE

echo "Created service file: $SERVICE_FILE"

# 2. Copy the service file to systemd directory
echo "Copying to /etc/systemd/system/ (sudo required)..."
sudo cp $SERVICE_FILE /etc/systemd/system/
sudo chmod 644 /etc/systemd/system/$SERVICE_FILE

# 3. Enable and start the service
echo "Reloading systemd daemon and enabling service..."
sudo systemctl daemon-reload
sudo systemctl enable $SERVICE_FILE
sudo systemctl start $SERVICE_FILE

# 4. Success message
echo "-------------------------------------------------------"
echo "Deployment Complete!"
echo "Service status Check: sudo systemctl status $SERVICE_NAME"
echo "View Service logs: journalctl -u $SERVICE_NAME -f"
echo "The Web GUI should now be accessible on port 8196."
echo "Note: If running in background, access via http://$(hostname -I | awk '{print $1}'):8196"
echo "-------------------------------------------------------"
