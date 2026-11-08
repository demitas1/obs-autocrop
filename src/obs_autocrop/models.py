"""OS 非依存のデータモデルと例外（PLAN.md 3.1, 5.2）。"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class CameraIdentity:
    """config.toml の [camera] から作るカメラの識別子。

    kind: "by-id" | "by-path" | "usb"
    value: by-id / by-path のリンク名（"usb" の場合は空）
    """

    kind: str
    value: str = ""
    vendor_id: str = ""
    product_id: str = ""
    serial: str = ""
    name: str = ""

    def describe(self) -> str:
        if self.kind == "usb":
            parts = [f"{self.vendor_id}:{self.product_id}"]
            if self.serial:
                parts.append(f"serial={self.serial}")
            if self.name:
                parts.append(f"name={self.name!r}")
            return "usb " + " ".join(parts)
        return f"{self.kind} {self.value}"


@dataclass(frozen=True)
class CameraInfo:
    """列挙したキャプチャデバイス 1 台分の情報。"""

    node: str
    name: str
    vendor_id: str = ""
    product_id: str = ""
    serial: str = ""
    by_id: tuple[str, ...] = ()
    by_path: tuple[str, ...] = ()
    extra: dict = field(default_factory=dict, compare=False, hash=False)


@dataclass(frozen=True)
class ResolvedCamera:
    """識別子の解決結果。node は CaptureBackend / CaptureSourceAdapter にそのまま渡せる。"""

    node: str
    link: str
    info: CameraInfo


class CameraError(Exception):
    pass


class CameraNotFound(CameraError):
    def __init__(self, identity: CameraIdentity, candidates: list[CameraInfo]):
        self.identity = identity
        self.candidates = candidates
        super().__init__(f"camera not found: {identity.describe()}")


class CameraAmbiguous(CameraError):
    def __init__(self, identity: CameraIdentity, matches: list[CameraInfo]):
        self.identity = identity
        self.matches = matches
        nodes = ", ".join(m.node for m in matches)
        super().__init__(f"camera ambiguous: {identity.describe()} matches {nodes}")


@dataclass(frozen=True)
class CropRect:
    """ソースのピクセル座標でのクロップ矩形（PLAN.md 6.3）。"""

    x: int
    y: int
    w: int
    h: int

    MIN_SIZE = 16

    def clamp(self, src_w: int, src_h: int) -> tuple["CropRect", list[str]]:
        """ソースの範囲内に収めた矩形と、修正した場合の警告を返す。"""
        warnings = []
        x = min(max(self.x, 0), src_w - self.MIN_SIZE)
        y = min(max(self.y, 0), src_h - self.MIN_SIZE)
        w = min(max(self.w, self.MIN_SIZE), src_w - x)
        h = min(max(self.h, self.MIN_SIZE), src_h - y)
        out = CropRect(x, y, w, h)
        if out != self:
            warnings.append(f"crop {self} clamped to {out} (source {src_w}x{src_h})")
        return out, warnings

    def to_filter_settings(self) -> dict:
        """Crop/Pad フィルタの設定（relative=false, cx/cy は幅と高さ）。"""
        return {"relative": False, "left": self.x, "top": self.y, "cx": self.w, "cy": self.h}

    @classmethod
    def from_filter_settings(cls, s: dict, src_w: int, src_h: int) -> "CropRect":
        """Crop/Pad フィルタの設定から矩形を求める。relative の既定は true（4辺の削り量）。"""
        left, top = s.get("left", 0), s.get("top", 0)
        if s.get("relative", True):
            return cls(left, top, src_w - left - s.get("right", 0), src_h - top - s.get("bottom", 0))
        return cls(left, top, s.get("cx", src_w - left), s.get("cy", src_h - top))
