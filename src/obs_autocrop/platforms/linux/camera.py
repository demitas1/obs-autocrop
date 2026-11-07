"""CameraLocator の Linux 実装（PLAN.md 5章）。pyudev と /dev/v4l/by-* を使う。"""

from __future__ import annotations

from obs_autocrop.models import (
    CameraAmbiguous,
    CameraIdentity,
    CameraInfo,
    CameraNotFound,
    ResolvedCamera,
)

BY_ID_DIR = "/dev/v4l/by-id/"
BY_PATH_DIR = "/dev/v4l/by-path/"


def _attr(device, name: str) -> str | None:
    try:
        return device.attributes.asstring(name)
    except KeyError:
        return None


def _link_name(value: str, prefix: str) -> str:
    """フルパスで指定されてもリンク名で比較できるようにする。"""
    return value[len(prefix):] if value.startswith(prefix) else value


class LinuxCameraLocator:
    def __init__(self, context=None):
        # テストでは pyudev.Context の代わりにモックを渡す
        if context is None:
            import pyudev

            context = pyudev.Context()
        self._context = context

    def list_cameras(self) -> list[CameraInfo]:
        cameras = []
        for dev in self._context.list_devices(subsystem="video4linux"):
            props = dev.properties
            if ":capture:" not in props.get("ID_V4L_CAPABILITIES", ""):
                continue
            if _attr(dev, "index") != "0":  # メタデータ用ノードを除外する
                continue
            links = sorted(dev.device_links)
            cameras.append(CameraInfo(
                node=dev.device_node,
                name=_attr(dev, "name") or "",
                vendor_id=props.get("ID_VENDOR_ID", ""),
                product_id=props.get("ID_MODEL_ID", ""),
                serial=props.get("ID_SERIAL_SHORT", ""),
                by_id=tuple(l for l in links if l.startswith(BY_ID_DIR)),
                by_path=tuple(l for l in links if l.startswith(BY_PATH_DIR)),
                extra={"id_path": props.get("ID_PATH", ""), "sys_path": dev.sys_path},
            ))
        return sorted(cameras, key=lambda c: c.node)

    def resolve(self, identity: CameraIdentity) -> ResolvedCamera:
        cameras = self.list_cameras()
        if identity.kind == "by-id":
            link = BY_ID_DIR + _link_name(identity.value, BY_ID_DIR)
            matches = [c for c in cameras if link in c.by_id]
        elif identity.kind == "by-path":
            link = BY_PATH_DIR + _link_name(identity.value, BY_PATH_DIR)
            matches = [c for c in cameras if link in c.by_path]
        elif identity.kind == "usb":
            link = ""
            matches = [
                c for c in cameras
                if c.vendor_id.lower() == identity.vendor_id.lower()
                and c.product_id.lower() == identity.product_id.lower()
                and (not identity.serial or c.serial == identity.serial)
                and (not identity.name or c.name == identity.name)
            ]
        else:
            raise ValueError(f"unknown identity kind: {identity.kind}")

        if not matches:
            raise CameraNotFound(identity, cameras)
        if len(matches) > 1:
            raise CameraAmbiguous(identity, matches)
        cam = matches[0]
        if not link:
            link = (cam.by_id or cam.by_path or ("",))[0]
        return ResolvedCamera(node=cam.node, link=link, info=cam)

    def config_snippet(self, camera: CameraInfo) -> str:
        lines = [f"# {camera.node}  {camera.name}"]
        lines.append("[camera]")
        if camera.by_id:
            lines.append(f'by_id = "{_link_name(camera.by_id[0], BY_ID_DIR)}"')
        for i, p in enumerate(camera.by_path):
            prefix = "" if not camera.by_id and i == 0 else "# "
            lines.append(f'{prefix}by_path = "{_link_name(p, BY_PATH_DIR)}"')
        for key in ("vendor_id", "product_id", "serial", "name"):
            lines.append(f'# {key} = "{getattr(camera, key)}"')
        return "\n".join(lines)
