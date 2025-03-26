📹 video-viewerPi  
video-viewerPi is a lightweight, headless video pipeline application for Raspberry Pi, NVIDIA Jetson, and Linux systems. It uses GStreamer to route and process video from various input sources (USB, CSI, RTP, or file) and outputs it to screen, RTP stream, or saved file.

🔧 Features  
🎥 Supports multiple input types: USB webcam, CSI camera, RTP stream, or video file  
📤 Flexible output: Local display, RTP stream, or saved video  
⚙️ Hardware-accelerated encoding (Jetson / Raspberry Pi)  
💻 Headless CLI usage — perfect for embedded systems  

📦 Installation  
Run the included setup script:

```bash
chmod +x setup.sh
./setup.sh
```

Alternatively, manually install the required packages:

```bash
sudo apt update
sudo apt install -y \
  gstreamer1.0-tools \
  gstreamer1.0-plugins-base \
  gstreamer1.0-plugins-good \
  gstreamer1.0-plugins-bad \
  gstreamer1.0-plugins-ugly \
  gstreamer1.0-libav \
  python3-gi \
  python3-gst-1.0 
```

🚀 Usage
```bash
python3 video-viewerPi.py <input_uri> <output_uri> <options>
```

📥 Input URI examples   
USB camera:-	/dev/video0	Standard webcam  
CSI camera:-	csi://0	Raspberry Pi or Jetson cam  
RTP stream:-	rtp://@:5000	RTP input on port 5000  
File:-	video.mp4	Video file input  

📤 Output URI examples  
Local:-	local (default)	Display video locally  
RTP stream:-	rtp://[remote-IP]:1234	Stream output via RTP  
File save:-	save://output.mp4	Save to an MP4 file  

⚙️ Options  
--input-codec=h264 or mjpeg (default: h264, for RTP input only)  
--output-codec=h264 or mjpeg (default: h264)  
--hw-encoder	Enable hardware-accelerated H.264 encoding  

📂 Examples  
View USB camera output locally
```bash
python3 video-viewerPi.py /dev/video0
```

View CSI camera output locally
```bash
python3 video-viewerPi.py csi://0
```

Save video file to disk
```bash
python3 video-viewerPi.py /dev/video0 save://output.mp4
```

Stream USB camera to another device via RTP  
```bash
python3 video-viewerPi.py /dev/video0 rtp://<remote-ip>:5000
```

Receive and display incoming RTP stream  
```bash
python3 video-viewerPi.py rtp://@:5000
```

Stream USB camera to another device via RTP uses mjpeg codec & GPU  
```bash
python3 video-viewerPi.py /dev/video0 rtp://<remote-ip>:5000 --input-codec=mjpeg --output-codec=mjpeg --hw-encoder
```

📌 Notes
Hardware encoder is available for Jetson (nvh264enc) and Raspberry Pi (v4l2h264enc)

The application auto-detects the platform and selects appropriate encoder

Tested on Raspberry Pi OS, Jetson Nano, and Ubuntu 22.04

📃 License
This project is licensed under the MIT License.
