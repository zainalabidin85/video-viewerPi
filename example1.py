# Using videoViewer as OOP

from videoViewerPi2 import VideoViewerPi

viewer = VideoViewerPi(
      input_uri = "/dev/video0",
      output_uri = "local"
)

viewer.start()
