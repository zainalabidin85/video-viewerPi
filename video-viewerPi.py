#!/usr/bin/env python3
# video-viewerPi
# Copyright (c) 2025 Zainal Abidin Arsat
# Licensed under the MIT License. See LICENSE file for details.


import gi, sys, os, re, argparse, threading
from urllib.parse import urlparse
from flask import Flask, Response, render_template

gi.require_version('Gst', '1.0')
from gi.repository import Gst, GLib

# Initialize GStreamer
Gst.init(None)

# Flask app for HTTP MJPEG streaming
app = Flask(__name__, static_folder="static", template_folder="static")
http_pipeline = None

def detect_platform():
    """
    Detects the hardware platform (Raspberry Pi, Jetson, or generic Linux).

    Returns:
        str: 'rpi', 'jetson', or 'generic'
    """
    try:
        with open("/sys/firmware/devicetree/base/model", "r") as f:
            m = f.read().lower()
            if "raspberry pi" in m:
                return "rpi"
            if "jetson" in m or "nvidia" in m:
                return "jetson"
    except:
        pass
    return "generic"

def parse_resolution(r):
    """
    Parses resolution strings or presets (e.g., '720') into width x height.

    Args:
        r (str): Resolution string or preset.

    Returns:
        str: Resolution in 'widthxheight' format.
    """
    if not r:
        return ""
    if "x" in r:
        return r
    presets = {"1080": "1920x1080", "720": "1280x720", "480": "640x480"}
    return presets.get(r, "")

def parse_input(u):
    """
    Parses the input URI into structured type/configuration.

    Args:
        u (str): Input URI.

    Returns:
        dict: Dictionary describing input type and relevant parameters.
    """
    if u.startswith("/dev/video"):
        return {"type": "v4l2", "device": u}
    if u.startswith("csi://"):
        return {"type": "csi", "index": u.split("csi://", 1)[1]}
    if u.startswith("udp://"):
        s = u[len("udp://"):]
        if ":" in s:
            host_part, port_part = s.rsplit(':', 1)
            host = host_part or None
        else:
            host = None
            port_part = s
        return {"type": "udp", "host": host, "port": int(port_part)}
    if u.startswith("rtp://"):
        m = re.match(r"rtp://(?:[\d\.]+|@):(?P<port>\d+)", u)
        if m:
            return {"type": "rtp", "port": int(m.group('port'))}
    if u.startswith("mc://"):
        m = re.match(r"mc://(?P<host>[\d\.]+):(?P<port>\d+)", u)
        if m:
            return {"type": "multicast", "host": m.group('host'), "port": int(m.group('port'))}
    if os.path.isfile(u):
        return {"type": "file", "path": u}
    raise ValueError(f"Unsupported input URI: {u}")

def parse_output(u):
    """
    Parses the output URI into structured type/configuration.

    Args:
        u (str): Output URI.

    Returns:
        dict: Dictionary describing output type and relevant parameters.
    """
    if u.startswith("rtp://"):
        m = re.match(r"rtp://(?P<host>[\d\.]+):(?P<port>\d+)", u)
        if m:
            return {"type": "rtp", "host": m.group('host'), "port": int(m.group('port'))}
    if u.startswith("mc://"):
        m = re.match(r"mc://(?P<host>[\d\.]+):(?P<port>\d+)", u)
        if m:
            return {"type": "multicast", "host": m.group('host'), "port": int(m.group('port'))}
    if u == "local":
        return {"type": "local"}
    if u.startswith("save://"):
        return {"type": "save", "file": u.split("save://", 1)[1]}
    if u == "http":
        return {"type": "http"}
    raise ValueError(f"Unsupported output URI: {u}")

def get_encoder(c, p, hw):
    """
    Selects the appropriate encoder element for GStreamer.

    Args:
        c (str): Codec type ('h264' or 'mjpeg').
        p (str): Platform ('rpi', 'jetson', etc).
        hw (bool): Whether to use hardware acceleration.

    Returns:
        str: GStreamer encoder string.
    """
    if c == "mjpeg":
        return "jpegenc"
    if c == "h264":
        if hw:
            if p == "jetson":
                return "nvh264enc insert-sps-pps=true"
            if p == "rpi":
                return "v4l2h264enc"
        return "x264enc tune=zerolatency byte-stream=true key-int-max=30"
    raise ValueError("Unsupported codec")

def build_pipeline(inp, outp, in_c, out_c, hw, res, fps):
    """
    Builds the GStreamer pipeline string.

    Args:
        inp (dict): Input configuration.
        outp (dict): Output configuration.
        in_c (str): Input codec.
        out_c (str): Output codec.
        hw (bool): Enable hardware encoding.
        res (str): Resolution (e.g., '640x480').
        fps (str): Framerate (e.g., '30').

    Returns:
        str or None: GStreamer pipeline string, or None if using HTTP.
    """
    global http_pipeline
    plat = detect_platform()

    # --- Input ---
    if inp['type'] in ('v4l2', 'csi'):
        caps = "video/x-raw"
        if res:
            w, h = res.split('x')
            caps += f", width={w}, height={h}"
        if fps:
            caps += f", framerate={fps}/1"
        dev = inp['device'] if inp['type'] == 'v4l2' else f"/dev/video{inp['index']}"
        src = f"v4l2src device={dev} ! {caps} ! videoconvert"
    elif inp['type'] == 'file':
        src = f"filesrc location={inp['path']} ! decodebin ! videoconvert"
    elif inp['type'] in ('rtp', 'multicast', 'udp'):
        base = "udpsrc"
        if inp['type'] == 'multicast':
            base += f" multicast-group={inp['host']} auto-multicast=true"
        elif inp['type'] == 'udp' and inp['host']:
            base += f" address={inp['host']}"
        base += f" port={inp['port']}"
        depay = "rtpjpegdepay ! jpegdec" if in_c == 'mjpeg' else "rtph264depay ! avdec_h264"
        caps = (
            "application/x-rtp,media=video,encoding-name=JPEG,payload=26"
            if in_c == 'mjpeg' else
            "application/x-rtp,media=video,encoding-name=H264,payload=96"
        )
        src = f"{base} caps=\"{caps}\" ! {depay} ! videoconvert"
    else:
        raise ValueError("Unknown input type")

    # --- Output ---
    if outp['type'] == 'local':
        sink = "autovideosink sync=false"
    elif outp['type'] == 'http':
        pipeline_str = (
            f"v4l2src device={inp['device']} do-timestamp=true ! "
            f"image/jpeg,width=640,height=480 ! jpegdec ! videoconvert ! "
            f"jpegenc idct-method=1 ! queue ! "
            f"appsink name=appsink emit-signals=true max-buffers=1 drop=true sync=false"
        )
        http_pipeline = Gst.parse_launch(pipeline_str)
        http_pipeline.set_state(Gst.State.PLAYING)
        threading.Thread(target=app.run, kwargs={"host": "0.0.0.0", "port": 8080}).start()
        return None
    else:
        enc = get_encoder(out_c, plat, hw)
        if outp['type'] == 'rtp':
            if out_c == "mjpeg":
                sink = f"{enc} ! rtpjpegpay pt=26 ! udpsink host={outp['host']} port={outp['port']}"
            else:
                sink = f"{enc} ! rtph264pay config-interval=1 pt=96 ! udpsink host={outp['host']} port={outp['port']}"
        elif outp['type'] == 'multicast':
            if out_c == "mjpeg":
                sink = f"{enc} ! rtpjpegpay pt=26 ! udpsink host={outp['host']} port={outp['port']} auto-multicast=true ttl=1"
            else:
                sink = f"{enc} ! rtph264pay config-interval=1 pt=96 ! udpsink host={outp['host']} port={outp['port']} auto-multicast=true ttl=1"
        elif outp['type'] == 'save':
            sink = f"{enc} ! mp4mux ! filesink location={outp['file']}"
        else:
            raise ValueError("Unknown output type")

    return f"{src} ! {sink}"

@app.route('/stream')
def stream():
    """
    Flask route for MJPEG streaming.

    Returns:
        Response: multipart/x-mixed-replace MJPEG stream.
    """
    global http_pipeline
    appsink = http_pipeline.get_by_name("appsink")
    if not appsink:
        return "Appsink not found", 500

    def generate():
        while True:
            sample = appsink.emit("pull-sample")
            if not sample:
                continue
            buf = sample.get_buffer()
            result, map_info = buf.map(Gst.MapFlags.READ)
            if result:
                frame_data = map_info.data
                yield (b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + frame_data + b'\r\n')
                buf.unmap(map_info)
    return Response(generate(), mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/')
def index():
    """
    Flask index route rendering the static HTML page.

    Returns:
        str: Rendered HTML content.
    """
    return render_template("index.html")

def on_message(bus, msg, loop):
    """
    GStreamer message handler for errors and EOS.

    Args:
        bus (Gst.Bus): GStreamer bus.
        msg (Gst.Message): Message to parse.
        loop (GLib.MainLoop): Loop to terminate on error or EOS.
    """
    if msg.type == Gst.MessageType.EOS:
        loop.quit()
    elif msg.type == Gst.MessageType.ERROR:
        err, dbg = msg.parse_error()
        print(f"[ERROR] {err}: {dbg}")
        loop.quit()

def start_pipeline(inp_uri, out_uri, in_c, out_c, hw, res, fps):
    """
    Parses arguments and launches the appropriate video pipeline.

    Args:
        inp_uri (str): Input URI.
        out_uri (str): Output URI.
        in_c (str): Input codec.
        out_c (str): Output codec.
        hw (bool): Use hardware encoder.
        res (str): Resolution.
        fps (str): Framerate.
    """
    inp = parse_input(inp_uri)
    outp = parse_output(out_uri)
    plat = detect_platform()

    print("\n-----------INPUT-----------")
    print(f"URI: {inp_uri}")
    print(f"codec: {in_c}")
    print(f"hardware encoder: {'enabled' if hw else 'disabled'}")
    print(f"platform: {plat}")
    print("\n-----------OUTPUT----------")
    print(f"URI: {out_uri}")
    print(f"codec: {out_c}")
    print(f"resolution: {res if res else '[none]'}")
    print(f"fps: {fps if fps else '[none]'}")

    pipeline_str = build_pipeline(inp, outp, in_c, out_c, hw, res, fps)
    if pipeline_str is None:
        return

    print(f"\nLaunching pipeline:\n{pipeline_str}\n")
    pipeline = Gst.parse_launch(pipeline_str)
    bus = pipeline.get_bus()
    bus.add_signal_watch()
    loop = GLib.MainLoop()
    bus.connect("message", on_message, loop)
    pipeline.set_state(Gst.State.PLAYING)
    try:
        loop.run()
    except KeyboardInterrupt:
        print("Interrupted by user, stopping..")
        # tell all elements we're done so mp4mux writes its index
        pipeline.send_event(Gst.Event.new_eos())
        # wait mp4mux finish
        bus.timed_pop_filtered(Gst.CLOCK_TIME_NONE, Gst.MessageType.EOS)
    finally:
        pipeline.set_state(Gst.State.NULL)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="video-viewerPi with http support")
    parser.add_argument("input_uri", help="/dev/video0, csi://0, file.mp4, rtp://@:5000, mc://remote-ip:port, udp://@:5000")
    parser.add_argument("output_uri", nargs="?", default="local", help="local, rtp://remote-ip:port, mc://239.0.0.1:5000, save://file.mp4, or http")
    parser.add_argument("--input-codec", choices=["h264", "mjpeg"], default="h264", help="Codec for incoming RTP/UDP")
    parser.add_argument("--output-codec", choices=["h264", "mjpeg"], default="h264", help="Codec for outgoing RTP/save")
    parser.add_argument("--hw-encoder", action="store_true", help="Enable hardware H.264 encoder")
    parser.add_argument("--resolution", help="Capture resolution (e.g. 1080, 720, 480)")
    parser.add_argument("--fps", help="Capture framerate (e.g. 30, 15)")
    args = parser.parse_args()
    res = parse_resolution(args.resolution) if args.resolution else ""
    start_pipeline(args.input_uri, args.output_uri, args.input_codec, args.output_codec, args.hw_encoder, res, args.fps)
