#!/bin/bash

# Configuration and variables
INSTALL_DIR=$(pwd)
SERVICE_NAME="voiceeditor"
SERVICE_FILE="${SERVICE_NAME}.service"
USER_NAME=$(whoami)
HOME_PATH=$HOME

echo "Configuring the systemd service for Linux deployment..."

# 1. Generate the actual service file from the template
sed -e "s|{{USER}}|$USER_NAME|g" \
    -e "s|{{INSTALL_DIR}}|$INSTALL_DIR|g" \
    -e "s|{{HOME}}|$HOME_PATH|g" \
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
