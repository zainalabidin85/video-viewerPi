#!/bin/bash

echo "=== video-viewerPi Setup Script ==="

# Update and install system dependencies
echo "Updating package list..."
sudo apt update

echo "Installing GStreamer and plugins..."
sudo apt install -y \
    gstreamer1.0-tools \
    gstreamer1.0-plugins-base \
    gstreamer1.0-plugins-good \
    gstreamer1.0-plugins-bad \
    gstreamer1.0-plugins-ugly \
    gstreamer1.0-libav \
    python3-gi \
    python3-gst-1.0

# Optional: Install Python dependencies if needed
echo "Installing Python dependencies..."
pip3 install --upgrade pip
pip3 install argparse

# Display success message
echo ""
echo "✅ Setup complete!"
echo "You can now run video-viewerPi like this:"
echo "  python3 video-viewerPi.py /dev/video0 rtp://192.168.4.3:1234"
