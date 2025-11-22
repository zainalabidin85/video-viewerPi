# Using appsink output of videoViewerPi2 for jetson inference.

import jetson.utils
from videoViewerPi2 import VideoViewerPi

viewer = VideoViewerPi("/dev/video0", "appsink")
viewer.start()

net = jetson.inference.detectNet("ssd-mobilenet-v2", threshold=0.5)

while True:
    cuda_mem = viewer.get_cuda_frame()
    if cuda_mem is None:
        continue

    cuda_img = jetson.utils.cudaFromNumpy(cuda_mem.reshape(720, 1280, 4))
    detections = net.Detect(cuda_img)
