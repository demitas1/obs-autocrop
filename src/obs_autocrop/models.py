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
