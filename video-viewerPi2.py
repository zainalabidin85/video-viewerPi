#!/usr/bin/env python3
# video-viewerPi (OOP Edition)
# Copyright (c) 2025
# Licensed under MIT

import gi, os, re, argparse, threading
from flask import Flask, Response, render_template
from urllib.parse import urlparse

gi.require_version("Gst", "1.0")
from gi.repository import Gst, GLib

Gst.init(None)


# ============================================================
#                     CLASS VideoViewerPi
# ============================================================
class VideoViewerPi:

    def __init__(self, input_uri, output_uri="local",
                 input_codec="h264", output_codec="h264",
                 hw_encoder=False, resolution="", fps=""):

        self.input_uri = input_uri
        self.output_uri = output_uri
        self.input_codec = input_codec
        self.output_codec = output_codec
        self.hw_encoder = hw_encoder
        self.resolution = resolution
        self.fps = fps

        self.platform = self.detect_platform()

        self.pipeline = None
        self.http_pipeline = None
        self.loop = None

        # Flask app
        self.app = Flask(__name__, static_folder="static", template_folder="static")
        self.configure_routes()


    # --------------------------------------------------------
    # PLATFORM DETECTION
    # --------------------------------------------------------
    def detect_platform(self):
        try:
            with open("/sys/firmware/devicetree/base/model", "r") as f:
                m = f.read().lower()
                if "raspberry pi" in m: return "rpi"
                if "jetson" in m or "nvidia" in m: return "jetson"
        except:
            pass
        return "generic"


    # --------------------------------------------------------
    # RESOLUTION PARSER
    # --------------------------------------------------------
    @staticmethod
    def parse_resolution(r):
        if not r: return ""
        if "x" in r: return r
        presets = {"1080":"1920x1080", "720":"1280x720", "480":"640x480"}
        return presets.get(r, "")


    # --------------------------------------------------------
    # INPUT PARSER
    # --------------------------------------------------------
    def parse_input(self, u):
        if u.startswith("/dev/video"):
            return {"type":"v4l2", "device":u}
        if u.startswith("csi://"):
            return {"type":"csi", "index":u.split("csi://")[1]}
        if u.startswith("udp://"):
            s = u[6:]
            host = None
            if ":" in s:
                host, p = s.rsplit(":",1)
                host = host or None
            else:
                p = s
            return {"type":"udp", "host":host, "port":int(p)}

        if u.startswith("rtp://"):
            m = re.match(r"rtp://(?:[\d\.]+|@):(\d+)", u)
            if m: return {"type":"rtp", "port":int(m.group(1))}

        if u.startswith("mc://"):
            m = re.match(r"mc://([\d\.]+):(\d+)", u)
            if m:
                return {"type":"multicast",
                        "host":m.group(1),
                        "port":int(m.group(2))}

        if os.path.isfile(u):
            return {"type":"file", "path":u}

        raise ValueError(f"Unsupported input: {u}")


    # --------------------------------------------------------
    # OUTPUT PARSER
    # --------------------------------------------------------
    def parse_output(self, u):
        if u.startswith("rtp://"):
            m = re.match(r"rtp://([\d\.]+):(\d+)", u)
            if m:
                return {"type":"rtp", "host":m.group(1), "port":int(m.group(2))}

        if u.startswith("mc://"):
            m = re.match(r"mc://([\d\.]+):(\d+)", u)
            if m:
                return {"type":"multicast", "host":m.group(1), "port":int(m.group(2))}

        if u == "local": return {"type":"local"}
        if u.startswith("save://"): return {"type":"save","file":u[7:]}
        if u == "http": return {"type":"http"}

        raise ValueError(f"Unsupported output: {u}")


    # --------------------------------------------------------
    # ENCODER SELECTION
    # --------------------------------------------------------
    def get_encoder(self, codec):
        if codec == "mjpeg": return "jpegenc"

        if codec == "h264":
            if self.hw_encoder:
                if self.platform == "jetson":
                    return "nvh264enc insert-sps-pps=true"
                if self.platform == "rpi":
                    return "v4l2h264enc"
            return "x264enc tune=zerolatency byte-stream=true key-int-max=30"

        raise ValueError("Unsupported codec")


    # --------------------------------------------------------
    # BUILD PIPELINE STRING
    # --------------------------------------------------------
    def build_pipeline(self, inp, outp):
        # ---------------- INPUT ----------------
        if inp["type"] in ("v4l2","csi"):
            caps = "video/x-raw"
            if self.resolution:
                w,h = self.resolution.split("x")
                caps += f", width={w}, height={h}"
            if self.fps:
                caps += f", framerate={self.fps}/1"

            dev = inp["device"] if inp["type"]=="v4l2" else f"/dev/video{inp['index']}"
            src = f"v4l2src device={dev} ! {caps} ! videoconvert"

        elif inp["type"]=="file":
            src = f"filesrc location=\"{inp['path']}\" ! decodebin ! videoconvert"

        elif inp["type"] in ("rtp","multicast","udp"):
            base = "udpsrc"
            if inp["type"]=="multicast":
                base += f" multicast-group={inp['host']} auto-multicast=true"
            elif inp["type"]=="udp" and inp["host"]:
                base += f" address={inp['host']}"

            base += f" port={inp['port']}"

            caps = ("application/x-rtp,media=video,encoding-name=JPEG,payload=26"
                    if self.input_codec=="mjpeg"
                    else "application/x-rtp,media=video,encoding-name=H264,payload=96")

            depay = ("rtpjpegdepay ! jpegdec"
                     if self.input_codec=="mjpeg"
                     else "rtph264depay ! avdec_h264")

            src = f"{base} caps=\"{caps}\" ! {depay} ! videoconvert"

        else:
            raise ValueError("Unknown input type")

        # ---------------- OUTPUT ----------------
        if outp["type"] == "local":
            sink = "autovideosink sync=false"

        elif outp["type"] == "save":
            enc = self.get_encoder(self.output_codec)
            sink = f"{enc} ! mp4mux ! filesink location=\"{outp['file']}\""

        elif outp["type"] == "rtp":
            enc = self.get_encoder(self.output_codec)
            if self.output_codec=="mjpeg":
                sink = f"{enc} ! rtpjpegpay pt=26 ! udpsink host={outp['host']} port={outp['port']}"
            else:
                sink = f"{enc} ! rtph264pay config-interval=1 pt=96 ! udpsink host={outp['host']} port={outp['port']}"

        elif outp["type"] == "multicast":
            enc = self.get_encoder(self.output_codec)
            sink = f"{enc} ! rtph264pay pt=96 ! udpsink host={outp['host']} port={outp['port']} auto-multicast=true ttl=1"

        elif outp["type"] == "http":
            return None  # special pipeline handled separately

        else:
            raise ValueError("Unknown output type")

        return f"{src} ! {sink}"


    # --------------------------------------------------------
    # HTTP MJPEG PIPELINE (APPSINK)
    # --------------------------------------------------------
    def build_http_pipeline(self, inp):
        dev = inp["device"]
        pipeline_str = (
            f"v4l2src device={dev} do-timestamp=true ! "
            f"image/jpeg,width=640,height=480 ! jpegdec ! videoconvert ! "
            f"jpegenc idct-method=1 ! queue ! "
            f"appsink name=appsink emit-signals=true max-buffers=1 drop=true sync=false"
        )
        return pipeline_str


    # --------------------------------------------------------
    # FLASK ROUTES
    # --------------------------------------------------------
    def configure_routes(self):

        @self.app.route("/")
        def index():
            return render_template("index.html")

        @self.app.route("/stream")
        def stream():
            appsink = self.http_pipeline.get_by_name("appsink")
            def generate():
                while True:
                    sample = appsink.emit("pull-sample")
                    if not sample: continue
                    buf = sample.get_buffer()
                    ok, mapinfo = buf.map(Gst.MapFlags.READ)
                    if ok:
                        yield (b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" +
                               mapinfo.data + b"\r\n")
                        buf.unmap(mapinfo)
            return Response(generate(), mimetype="multipart/x-mixed-replace; boundary=frame")


    # --------------------------------------------------------
    # START HTTP MODE
    # --------------------------------------------------------
    def start_http(self, inp):
        print("[INFO] Starting HTTP MJPEG Server on port 8080")

        pipeline_str = self.build_http_pipeline(inp)
        self.http_pipeline = Gst.parse_launch(pipeline_str)
        self.http_pipeline.set_state(Gst.State.PLAYING)

        threading.Thread(target=self.app.run, kwargs={"host":"0.0.0.0", "port":8080}).start()


    # --------------------------------------------------------
    # MESSAGE HANDLER
    # --------------------------------------------------------
    def on_message(self, bus, msg, loop):
        if msg.type == Gst.MessageType.EOS:
            loop.quit()
        elif msg.type == Gst.MessageType.ERROR:
            err, dbg = msg.parse_error()
            print("[ERROR]", err, dbg)
            loop.quit()


    # --------------------------------------------------------
    # START PIPELINE
    # --------------------------------------------------------
    def start(self):
        inp = self.parse_input(self.input_uri)
        out = self.parse_output(self.output_uri)

        print("\n====== video-viewerPi (OOP Edition) ======")
        print("Input :", self.input_uri)
        print("Output:", self.output_uri)
        print("Platform:", self.platform)

        # HTTP Mode
        if out["type"] == "http":
            self.start_http(inp)
            return

        pipeline_str = self.build_pipeline(inp, out)
        print("\nLaunching pipeline:\n", pipeline_str)

        self.pipeline = Gst.parse_launch(pipeline_str)
        bus = self.pipeline.get_bus()
        bus.add_signal_watch()

        self.loop = GLib.MainLoop()
        bus.connect("message", self.on_message, self.loop)

        self.pipeline.set_state(Gst.State.PLAYING)

        try:
            self.loop.run()
        except KeyboardInterrupt:
            print("\n[INFO] User Stop")
            self.pipeline.send_event(Gst.Event.new_eos())
            bus.timed_pop_filtered(Gst.CLOCK_TIME_NONE, Gst.MessageType.EOS)
        finally:
            self.stop()


    # --------------------------------------------------------
    # STOP PIPELINE
    # --------------------------------------------------------
    def stop(self):
        if self.pipeline:
            self.pipeline.set_state(Gst.State.NULL)
            print("[STOP] Main pipeline stopped")

        if self.http_pipeline:
            self.http_pipeline.set_state(Gst.State.NULL)
            print("[STOP] HTTP pipeline stopped")


# ============================================================
#                     ARGPARSE WRAPPER
# ============================================================
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="video-viewerPi OOP")

    parser.add_argument("input_uri", help="/dev/video0, csi://0, file.mp4, rtp://@:5000, mc://..., udp://...")
    parser.add_argument("output_uri", nargs="?", default="local",
                        help="local, rtp://ip:port, mc://ip:port, save://file.mp4, http")

    parser.add_argument("--input-codec", choices=["h264","mjpeg"], default="h264")
    parser.add_argument("--output-codec", choices=["h264","mjpeg"], default="h264")
    parser.add_argument("--hw-encoder", action="store_true")

    parser.add_argument("--resolution", help="1080, 720, 480, or 1280x720")
    parser.add_argument("--fps", help="30, 15, etc")

    args = parser.parse_args()
    resolution = VideoViewerPi.parse_resolution(args.resolution)

    viewer = VideoViewerPi(
        args.input_uri,
        args.output_uri,
        args.input_codec,
        args.output_codec,
        args.hw_encoder,
        resolution,
        args.fps
    )

    viewer.start()
