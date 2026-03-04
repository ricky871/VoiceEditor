#!/bin/bash

# Configuration and variables
INSTALL_DIR=$(pwd)
SERVICE_NAME="voiceeditor"
SERVICE_FILE="${SERVICE_NAME}.service"
if [ -n "$SUDO_USER" ] && [ "$SUDO_USER" != "root" ]; then
    USER_NAME="$SUDO_USER"
else
    USER_NAME=$(whoami)
fi

HOME_PATH=$(getent passwd "$USER_NAME" | cut -d: -f6)
if [ -z "$HOME_PATH" ]; then
    HOME_PATH=$HOME
fi

# Find the absolute path of 'uv'
UV_PATH=""
if command -v uv >/dev/null 2>&1; then
    UV_PATH=$(command -v uv)
fi

if [ -z "$UV_PATH" ] || [ ! -x "$UV_PATH" ]; then
    for candidate in "$HOME_PATH/.local/bin/uv" "$HOME_PATH/.cargo/bin/uv" "/usr/local/bin/uv" "/usr/bin/uv"; do
        if [ -x "$candidate" ]; then
            UV_PATH="$candidate"
            break
        fi
    done
fi

if [ -z "$UV_PATH" ] || [ ! -x "$UV_PATH" ]; then
    echo "Error: 'uv' executable not found for user '$USER_NAME'."
    echo "Checked PATH and common locations under $HOME_PATH."
    exit 1
fi

echo "Deploy user: $USER_NAME"
echo "Using home: $HOME_PATH"
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
