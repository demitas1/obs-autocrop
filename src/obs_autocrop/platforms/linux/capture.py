"""CaptureBackend の Linux 実装（PLAN.md 7.2）。"""

from __future__ import annotations

import cv2


class LinuxCaptureBackend:
    def open(self, node: str) -> cv2.VideoCapture:
        return cv2.VideoCapture(node, cv2.CAP_V4L2)
