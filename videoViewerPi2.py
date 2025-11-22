#!/usr/bin/env python3
# videoViewerPi (Appsink Upgrade)
# Copyright 2025
# Licensed under MIT

import gi, os, re, argparse, threading, time
import numpy as np
from flask import Flask, Response, render_template

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

        # appsink frame buffer
        self.frame = None
        self.cuda_frame = None
        self.running = False

        self.pipeline = None
        self.http_pipeline = None
        self.loop = None

        # Flask web server (unchanged)
        self.app = Flask(__name__, static_folder="static", template_folder="static")
        self.configure_routes()



    # --------------------------------------------------------
    # PLATFORM DETECTION
    # --------------------------------------------------------
    def detect_platform(self):
        try:
            with open("/sys/firmware/devicetree/base/model", "r") as f:
                m = f.read().lower()
                if "raspberry pi" in m:
                    print("[INFO] Platform: Raspberry Pi")
                    return "rpi"
                if "jetson" in m or "nvidia" in m:
                    print("[INFO] Platform: Jetson")
                    return "jetson"
        except:
            pass

        print("[INFO] Platform: Generic Linux")
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
    # INPUT PARSER (RTSP, RTP, FILE, etc.)
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

        if u.startswith("rtsp://"):
            return {"type":"rtsp", "uri":u}

        if os.path.isfile(u):
            return {"type":"file", "path":u}

        raise ValueError(f"Unsupported input: {u}")



    # --------------------------------------------------------
    # OUTPUT PARSER (ADD APPSINK HERE)
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

        # NEW MODE
        if u == "appsink": return {"type":"appsink"}

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
    # BUILD NORMAL PIPELINE (unchanged)
    # --------------------------------------------------------
    def build_pipeline(self, inp, outp):

        # INPUT (same as your original file)
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
                     else "rtph264depay ! h264parse ! avdec_h264")

            src = f"{base} caps=\"{caps}\" ! {depay} ! videoconvert"

        elif inp["type"] == "rtsp":
            src = (
                f"rtspsrc location=\"{inp['uri']}\" latency=0 protocols=udp name=src "
                "! queue ! rtph264depay ! h264parse ! avdec_h264 ! videoconvert"
            )

        else:
            raise ValueError("Unknown input type")

        # OUTPUT
        if outp["type"] == "local":
            sink = "autovideosink sync=false"

        elif outp["type"] == "save":
            enc = self.get_encoder(self.output_codec)
            sink = f"{enc} ! mp4mux ! filesink location=\"{outp['file']}\""

        elif outp["type"] == "rtp":
            enc = self.get_encoder(self.output_codec)
            sink = f"{enc} ! rtph264pay config-interval=1 pt=96 ! udpsink host={outp['host']} port={outp['port']}"

        elif outp["type"] == "multicast":
            enc = self.get_encoder(self.output_codec)
            sink = f"{enc} ! rtph264pay pt=96 ! udpsink host={outp['host']} port={outp['port']} auto-multicast=true ttl=1"

        elif outp["type"] == "http":
            return None  # handled in start_http

        elif outp["type"] == "appsink":
            return None  # handled in start_appsink

        return f"{src} ! {sink}"



    # --------------------------------------------------------
    # NEW: BUILD APPSINK PIPELINE
    # --------------------------------------------------------
    def build_appsink_pipeline(self, inp):
        # ---------- INPUT ----------
        if inp["type"] in ("v4l2", "csi"):  # Add CSI support
            if inp["type"] == "v4l2":
                dev = inp["device"]
            else:  # CSI
                dev = f"/dev/video{inp['index']}"
                
            if self.platform == "jetson":
                # GPU → RGBA (best for TensorRT/jetson-inference)
                return (
                    f"v4l2src device={dev} ! "
                    "video/x-raw(memory:NVMM),width=1280,height=720,framerate=30/1 ! "
                    "nvvidconv ! video/x-raw,format=RGBA ! "
                    "appsink name=appsink max-buffers=1 drop=true emit-signals=true sync=false"
                )
            else:
                # Pi / Generic → CPU BGR
                return (
                    f"v4l2src device={dev} ! "
                    "video/x-raw,width=1280,height=720,framerate=30/1 ! "
                    "videoconvert ! video/x-raw,format=BGR ! "
                    "appsink name=appsink max-buffers=1 drop=true emit-signals=true sync=false"
                )

        raise ValueError("Appsink mode currently supports only V4L2/CSI input")


    # --------------------------------------------------------
    # START APPSINK MODE (YOLO/JETSON)
    # --------------------------------------------------------
    def start_appsink(self, inp):
        print("[INFO] Starting APPSINK mode")

        pipeline_str = self.build_appsink_pipeline(inp)
        print("PIPELINE:", pipeline_str)

        self.pipeline = Gst.parse_launch(pipeline_str)
        appsink = self.pipeline.get_by_name("appsink")

        self.pipeline.set_state(Gst.State.PLAYING)
        self.running = True

        # Start frame-capture thread
        threading.Thread(target=self.capture_thread, args=(appsink,), daemon=True).start()


    # --------------------------------------------------------
    # Capture Thread
    # --------------------------------------------------------
    def capture_thread(self, appsink):
        print("[INFO] Capture thread started")
        while self.running:
            try:
                sample = appsink.emit("pull-sample")
                if not sample:
                    time.sleep(0.001)
                    continue

                buffer = sample.get_buffer()
                if buffer is None:
                    continue
                    
                ok, mapinfo = buffer.map(Gst.MapFlags.READ)
                if not ok:
                    continue

                data = mapinfo.data

                # Jetson → RGBA
                if self.platform == "jetson":
                    arr = np.frombuffer(data, dtype=np.uint8)
                    self.frame = arr.copy()  # Use copy to avoid reference issues
                    self.cuda_frame = arr.copy()

                # Pi / PC → BGR
                else:
                    w, h = 1280, 720
                    arr = np.frombuffer(data, dtype=np.uint8).reshape((h, w, 3))
                    self.frame = arr.copy()

                buffer.unmap(mapinfo)
                time.sleep(0.001)
                
            except Exception as e:
                print(f"[ERROR] Capture thread: {e}")
                time.sleep(0.01)


    # --------------------------------------------------------
    # GET FRAME (CPU)
    # --------------------------------------------------------
    def get_frame(self):
        return self.frame


    # --------------------------------------------------------
    # GET CUDA FRAME (Jetson)
    # --------------------------------------------------------
    def get_cuda_frame(self):
        return self.cuda_frame



    # --------------------------------------------------------
    # HTTP MODE (unchanged)
    # --------------------------------------------------------
    def build_http_pipeline(self, inp):
        t = inp["type"]

        # -----------------------------
        # V4L2
        # -----------------------------
        if t == "v4l2":
            src = (
                f"v4l2src device={inp['device']} ! "
                f"video/x-raw,width=640,height=480,framerate=30/1 ! videoconvert "
            )

        # -----------------------------
        # CSI
        # -----------------------------
        elif t == "csi":
            src = (
                f"v4l2src device=/dev/video{inp['index']} ! "
                f"video/x-raw,width=640,height=480 ! videoconvert "
            )

        # -----------------------------
        # FILE
        # -----------------------------
        elif t == "file":
            src = (
                f"filesrc location=\"{inp['path']}\" ! decodebin ! videoconvert "
            )

        # -----------------------------
        # RTP
        # -----------------------------
        elif t == "rtp":
            src = (
                f"udpsrc port={inp['port']} "
                "caps=\"application/x-rtp,media=video,encoding-name=H264,payload=96\" ! "
                "rtph264depay ! h264parse ! avdec_h264 ! videoconvert "
            )

        # -----------------------------
        # MULTICAST
        # -----------------------------
        elif t == "multicast":
            src = (
                f"udpsrc multicast-group={inp['host']} auto-multicast=true port={inp['port']} "
                "caps=\"application/x-rtp,media=video,encoding-name=H264,payload=96\" ! "
                "rtph264depay ! h264parse ! avdec_h264 ! videoconvert "
            )

        # -----------------------------
        # UDP
        # -----------------------------
        elif t == "udp":
            src = (
                f"udpsrc port={inp['port']} ! "
                "application/x-rtp,media=video,encoding-name=H264,payload=96 ! "
                "rtph264depay ! h264parse ! avdec_h264 ! videoconvert "
            )

        # -----------------------------
        # RTSP (Low latency, dynamic pad)
        # -----------------------------
        elif t == "rtsp":
            src = (
                f"rtspsrc location=\"{inp['uri']}\" latency=0 protocols=udp name=src "
                "! queue ! rtph264depay ! h264parse ! avdec_h264 ! videoconvert "
            )

        else:
            raise ValueError("HTTP mode does not support this input type")

        # Final output: JPEG → appsink
        return (
            src +
            "! jpegenc idct-method=1 "
            "! appsink name=appsink emit-signals=true max-buffers=1 drop=true sync=false"
        )

    

    # --------------------------------------------------------
    # FLASK ROUTES (unchanged)
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
            
    def start_http(self, inp):
        """Start HTTP streaming mode"""
        print("[INFO] Starting HTTP mode")
        pipeline_str = self.build_http_pipeline(inp)
        print("HTTP PIPELINE:", pipeline_str)
        
        self.http_pipeline = Gst.parse_launch(pipeline_str)
        bus = self.http_pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message", self.on_http_message)
        
        self.http_pipeline.set_state(Gst.State.PLAYING)
        
        # Start Flask in a separate thread
        threading.Thread(target=self.app.run, 
                        kwargs={"host": "0.0.0.0", "port": 5000, "debug": False, "use_reloader": False},
                        daemon=True).start()
        
        print("[HTTP] Server running on http://0.0.0.0:5000")
        self.loop = GLib.MainLoop()
        try:
            self.loop.run()
        except KeyboardInterrupt:
            self.stop()
        
    def on_http_message(self, bus, message):
        """Handle HTTP pipeline messages"""
        t = message.type
        if t == Gst.MessageType.EOS:
            print("[HTTP] End of stream")
            self.loop.quit()
        elif t == Gst.MessageType.ERROR:
            err, debug = message.parse_error()
            print(f"[HTTP] Error: {err}, {debug}")
            self.loop.quit()
        elif t == Gst.MessageType.WARNING:
            err, debug = message.parse_warning()
            print(f"[HTTP] Warning: {err}, {debug}")

    def on_rtsp_pad_added(self, src, pad):
        """Handle dynamic pad addition for RTSP"""
        print("[RTSP] Pad added:", pad.get_name())
        depay = self.pipeline.get_by_name("depay") or self.pipeline.get_by_name("rtph264depay")
        if depay:
            sinkpad = depay.get_static_pad("sink")
            if sinkpad:
                pad.link(sinkpad)

    def on_message(self, bus, message, loop):
        """Handle main pipeline messages"""
        t = message.type
        if t == Gst.MessageType.EOS:
            print("[INFO] End of stream")
            loop.quit()
        elif t == Gst.MessageType.ERROR:
            err, debug = message.parse_error()
            print(f"[ERROR] {err}, {debug}")
            loop.quit()
        elif t == Gst.MessageType.WARNING:
            err, debug = message.parse_warning()
            print(f"[WARNING] {err}, {debug}")


    # --------------------------------------------------------
    # START MAIN ENTRY
    # --------------------------------------------------------
    def start(self):
        inp = self.parse_input(self.input_uri)
        out = self.parse_output(self.output_uri)

        print("\n====== videoViewerPi2======")
        print("Input :", self.input_uri)
        print("Output:", self.output_uri)
        print("Platform:", self.platform)
        
        # ---- APPSINK MODE ----
        if out["type"] == "appsink":
            self.start_appsink(inp)
            return

        # ---- HTTP MODE ----
        if out["type"] == "http":
            self.start_http(inp)
            return

        pipeline_str = self.build_pipeline(inp, out)
        print("\nLaunching pipeline:\n", pipeline_str)

        self.pipeline = Gst.parse_launch(pipeline_str)

        # Attach dynamic RTSP pads
        if inp["type"] == "rtsp":
            src = self.pipeline.get_by_name("src")
            if src:
                src.connect("pad-added", self.on_rtsp_pad_added)

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

    parser = argparse.ArgumentParser(
        description="video-viewerPi2 Edition",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog="""
Examples:
  # USB camera → local display
  videoViewerPi2 /dev/video0 local

  # USB camera → RTP output
  videoViewerPi2 /dev/video0 rtp://192.168.1.50:5000

  # RTSP IP Camera → local display
  videoViewerPi2 rtsp://user:pswd@192.168.1.10:554/stream1 local
  if your username has @ symbol you may use %40 in command

  # RTSP → RTP restream
  videoViewerPi2 rtsp://192.168.1.10/stream rtp://@5000

  # Multicast receiver → local
  videoViewerPi2 mc://239.255.1.1:5000 local

  # Save to MP4
  videoViewerPi2 /dev/video0 save://video.mp4

  # HTTP MJPEG server (browser view)
  videoViewerPi2 /dev/video0 http

Supported Input:
  /dev/video0        USB camera
  csi://0            CSI camera
  file.mp4           File playback
  rtp://@:5000       RTP (H264/MJPEG)
  mc://239.x.x.x:5000 Multicast
  udp://@:5000       Raw UDP
  rtsp://IP/stream   RTSP IP camera

Supported Output:
  local              Display window
  rtp://IP:PORT      RTP output
  mc://IP:PORT       Multicast output
  save://file.mp4    Save MP4
  http               MJPEG HTTP server
  appsink            A-I
        """
    )

    parser.add_argument("input_uri", help="Input URI")
    parser.add_argument("output_uri", nargs="?", default="local",
                        help="local, rtp://ip:port, mc://..., save://file.mp4, http, appsink")

    parser.add_argument("--input-codec", choices=["h264","mjpeg"], default="h264")
    parser.add_argument("--output-codec", choices=["h264","mjpeg"], default="h264")
    parser.add_argument("--hw-encoder", action="store_true")

    parser.add_argument("--resolution")
    parser.add_argument("--fps")

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

    # Handle appsink mode differently
    if args.output_uri == "appsink":
        viewer.start()  # This will now call start_appsink() and return
        try:
            while viewer.running:
                time.sleep(0.1)
        except KeyboardInterrupt:
            viewer.running = False
            viewer.stop()
    else:
        viewer.start()
