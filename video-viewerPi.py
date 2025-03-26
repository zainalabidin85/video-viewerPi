import gi
import sys
import re
import os
import argparse
from urllib.parse import urlparse

gi.require_version('Gst', '1.0')
from gi.repository import Gst

Gst.init(None)

def detect_platform():
    """Detect if running on Jetson, Raspberry Pi, or generic Linux."""
    try:
        with open("/sys/firmware/devicetree/base/model", "r") as f:
            model = f.read().lower()
            if "raspberry pi" in model:
                return "rpi"
            elif "jetson" in model or "nvidia" in model:
                return "jetson"
    except:
        pass

    try:
        with open("/proc/device-tree/compatible", "rb") as f:
            info = f.read().lower()
            if b"nvidia" in info:
                return "jetson"
            elif b"raspberrypi" in info:
                return "rpi"
    except:
        pass

    return "generic"

def parse_input(input_uri):
    if input_uri.startswith("/dev/video"):
        return {"type": "v4l2", "device": input_uri}
    elif input_uri.startswith("csi://"):
        cam_index = input_uri.replace("csi://", "")
        return {"type": "csi", "index": cam_index}
    elif input_uri.startswith("rtp://"):
        match = re.match(r"rtp://(?:([\d\.]+)|@):(\d+)", input_uri)
        if match:
            return {"type": "rtp", "port": int(match.group(2))}
    elif os.path.exists(input_uri):
        return {"type": "file", "path": input_uri}
    else:
        raise ValueError(f"Unsupported input: {input_uri}")

def parse_output(output_uri):
    if output_uri.startswith("rtp://"):
        match = re.match(r"rtp://([\d\.]+):(\d+)", output_uri)
        if match:
            return {"type": "rtp", "host": match.group(1), "port": int(match.group(2))}
    elif output_uri == "local":
        return {"type": "local"}
    elif output_uri.startswith("save://"):
        filename = output_uri.replace("save://", "")
        return {"type": "save", "file": filename}
    raise ValueError("Unsupported output. Use rtp://, local, or save://filename")

def get_encoder(codec, platform, use_hw=False):
    """Return encoder GStreamer pipeline string for given codec and platform."""
    if codec == "mjpeg":
        return "jpegenc"
    if codec == "h264":
        if use_hw:
            if platform == "jetson":
                return "nvh264enc insert-sps-pps=true"
            elif platform == "rpi":
                return "v4l2h264enc"
        return "x264enc tune=zerolatency"
    raise ValueError("Unsupported codec")

def build_pipeline(input_cfg, output_cfg, input_codec="h264", output_codec="h264", use_hw_encoder=False):
    platform = detect_platform()

    # Input pipe
    if input_cfg["type"] == "v4l2":
        input_pipe = f"v4l2src device={input_cfg['device']} ! videoconvert"
    elif input_cfg["type"] == "csi":
        input_pipe = f"v4l2src device=/dev/video{input_cfg['index']} ! videoconvert"
    elif input_cfg["type"] == "file":
        input_pipe = f"filesrc location=\"{input_cfg['path']}\" ! decodebin ! videoconvert"
    elif input_cfg["type"] == "rtp":
        if input_codec == "mjpeg":
            input_pipe = (
                f"udpsrc port={input_cfg['port']} "
                f"caps=\"application/x-rtp, media=video, encoding-name=JPEG, payload=26\" ! "
                f"rtpjpegdepay ! jpegdec ! videoconvert"
            )
        else:
            input_pipe = (
                f"udpsrc port={input_cfg['port']} "
                f"caps=\"application/x-rtp, media=video, encoding-name=H264, payload=96\" ! "
                f"rtph264depay ! avdec_h264 ! videoconvert"
            )
    else:
        raise ValueError("Unknown input type")

    # Output pipe
    encoder = get_encoder(output_codec, platform, use_hw_encoder)
    if output_cfg["type"] == "rtp":
        if output_codec == "mjpeg":
            output_pipe = f"{encoder} ! rtpjpegpay pt=26 ! udpsink host={output_cfg['host']} port={output_cfg['port']}"
        else:
            output_pipe = f"{encoder} ! rtph264pay config-interval=1 pt=96 ! udpsink host={output_cfg['host']} port={output_cfg['port']}"
    elif output_cfg["type"] == "local":
        output_pipe = "autovideosink sync=false"
    elif output_cfg["type"] == "save":
        output_pipe = f"{encoder} ! mp4mux ! filesink location=\"{output_cfg['file']}\""
    else:
        raise ValueError("Unknown output type")

    return f"{input_pipe} ! {output_pipe}"

def start_pipeline(input_uri, output_uri, input_codec="h264", output_codec="h264", use_hw_encoder=False):
    input_cfg = parse_input(input_uri)
    output_cfg = parse_output(output_uri)
    
    platform = detect_platform()
    
    print("\n-----------INPUT-----------")
    print(f"display: {input_uri}")
    print(f"codec: {input_codec}")
    print("-----------OUTPUT----------")
    print(f"display: {output_uri}")
    print(f"codec: {output_codec}")
    print(f"hardware encoder: {'enabled' if use_hw_encoder else 'disabled'}")
    print(f"platform: {platform}")
    
    pipeline_str = build_pipeline(input_cfg, output_cfg, input_codec, output_codec, use_hw_encoder)

    print("\nLaunching pipeline:")
    print(pipeline_str, "\n")

    pipeline = Gst.parse_launch(pipeline_str)
    pipeline.set_state(Gst.State.PLAYING)

    try:
        print(f"Streaming {input_uri} ➝ {output_uri} (Ctrl+C to stop)")
        while True:
            pass
    except KeyboardInterrupt:
        print("Stopping...")
        pipeline.set_state(Gst.State.NULL)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="pi-viewer: a headless video pipeline app for Raspberry Pi and Jetson")
    parser.add_argument("input_uri", help="Input URI (e.g., /dev/video0, csi://0, rtp://..., or file.mp4)")
    parser.add_argument("output_uri", nargs="?", default="local", help="Output URI (e.g., rtp://..., save://file.mp4, or 'local')")
    parser.add_argument("--input-codec", choices=["h264", "mjpeg"], default="h264", help="Input codec if using RTP (default: h264)")
    parser.add_argument("--output-codec", choices=["h264", "mjpeg"], default="h264", help="Output codec for RTP/save (default: h264)")
    parser.add_argument("--hw-encoder", action="store_true", help="Use hardware H.264 encoder (if available)")

    args = parser.parse_args()

    if args.output_uri == "local":
        print(f"[pi-viewer] No output specified — defaulting to 'local'")

    start_pipeline(args.input_uri, args.output_uri, args.input_codec, args.output_codec, args.hw_encoder)
