# Use appsink output of videoViewerPi2 for ai (yolo)

from videoViewerPi2 import VideoViewerPi
from ultralytics import YOLO

viewer = VideoViewerPi("/dev/video0", "appsink")
viewer.start()

model = YOLO("yolov8n.pt")

while True:
    frame = viewer.get_frame()
    if frame is None:
        continue

    results = model(frame)
    cv2.imshow("YOLO", results[0].plot())
    if cv2.waitKey(1) == ord('q'):
        break
