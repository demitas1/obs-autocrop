from types import SimpleNamespace

import pytest

from obs_autocrop.config import camera_identities, camera_identity
from obs_autocrop.models import CameraAmbiguous, CameraIdentity, CameraNotFound
from obs_autocrop.platforms.linux.camera import LinuxCameraLocator


class FakeAttrs(dict):
    def asstring(self, name):
        return self[name]


def fake_dev(n, index, capture=True, vid="f326", pid="1306", serial="HU1", port="0:2:1.0",
             name="Webcam AC410: Webcam AC410"):
    by_id = f"/dev/v4l/by-id/usb-Image+_Webcam_AC410_{serial}-video-index{index}"
    pci = "pci-0000:09:00.3"
    by_path = [f"/dev/v4l/by-path/{pci}-usb-{port}-video-index{index}",
               f"/dev/v4l/by-path/{pci}-usbv2-{port}-video-index{index}"]
    return SimpleNamespace(
        device_node=f"/dev/video{n}",
        sys_path=f"/sys/x/video{n}",
        device_links=iter([by_id, *by_path]),
        properties={
            "ID_V4L_CAPABILITIES": ":capture:" if capture else ":",
            "ID_VENDOR_ID": vid, "ID_MODEL_ID": pid, "ID_SERIAL_SHORT": serial,
            "ID_PATH": f"{pci}-usb-{port}",
        },
        attributes=FakeAttrs(index=str(index), name=name),
    )


class FakeContext:
    def __init__(self, *devs):
        self.devs = devs

    def list_devices(self, subsystem):
        assert subsystem == "video4linux"
        # device_links はジェネレータなので呼ぶたびに作り直す
        return [fake_dev(**d) for d in self.devs]


ONE = [dict(n=0, index=0), dict(n=1, index=1, capture=False)]
TWO_SAME_MODEL = ONE + [dict(n=2, index=0, serial="HU2", port="0:3:1.0"),
                        dict(n=3, index=1, capture=False, serial="HU2", port="0:3:1.0")]


def locator(devs):
    return LinuxCameraLocator(context=FakeContext(*devs))


def test_list_filters_metadata_nodes():
    cams = locator(ONE).list_cameras()
    assert [c.node for c in cams] == ["/dev/video0"]
    assert cams[0].serial == "HU1"
    assert len(cams[0].by_path) == 2


@pytest.mark.parametrize("identity, link", [
    (CameraIdentity("by-id", "usb-Image+_Webcam_AC410_HU1-video-index0"),
     "/dev/v4l/by-id/usb-Image+_Webcam_AC410_HU1-video-index0"),
    (CameraIdentity("by-id", "/dev/v4l/by-id/usb-Image+_Webcam_AC410_HU1-video-index0"),
     "/dev/v4l/by-id/usb-Image+_Webcam_AC410_HU1-video-index0"),
    (CameraIdentity("by-path", "pci-0000:09:00.3-usb-0:2:1.0-video-index0"),
     "/dev/v4l/by-path/pci-0000:09:00.3-usb-0:2:1.0-video-index0"),
    (CameraIdentity("by-path", "pci-0000:09:00.3-usbv2-0:2:1.0-video-index0"),
     "/dev/v4l/by-path/pci-0000:09:00.3-usbv2-0:2:1.0-video-index0"),
    (CameraIdentity("usb", vendor_id="F326", product_id="1306"),
     "/dev/v4l/by-id/usb-Image+_Webcam_AC410_HU1-video-index0"),
])
def test_resolve_one(identity, link):
    r = locator(ONE).resolve(identity)
    assert r.node == "/dev/video0"
    assert r.link == link


def test_resolve_metadata_node_is_not_a_match():
    with pytest.raises(CameraNotFound):
        locator(ONE).resolve(CameraIdentity("by-id", "usb-Image+_Webcam_AC410_HU1-video-index1"))


def test_resolve_not_found():
    with pytest.raises(CameraNotFound) as e:
        locator(ONE).resolve(CameraIdentity("usb", vendor_id="046d", product_id="085c"))
    assert [c.node for c in e.value.candidates] == ["/dev/video0"]


def test_resolve_ambiguous_and_disambiguated_by_serial():
    loc = locator(TWO_SAME_MODEL)
    with pytest.raises(CameraAmbiguous) as e:
        loc.resolve(CameraIdentity("usb", vendor_id="f326", product_id="1306"))
    assert [c.node for c in e.value.matches] == ["/dev/video0", "/dev/video2"]
    r = loc.resolve(CameraIdentity("usb", vendor_id="f326", product_id="1306", serial="HU2"))
    assert r.node == "/dev/video2"


def test_resolve_by_path_distinguishes_ports():
    r = locator(TWO_SAME_MODEL).resolve(
        CameraIdentity("by-path", "pci-0000:09:00.3-usb-0:3:1.0-video-index0"))
    assert r.info.serial == "HU2"


def test_config_identities_priority():
    cam = {"by_id": "a", "by_path": "b", "vendor_id": "f326", "product_id": "1306", "name": ""}
    assert [i.kind for i in camera_identities(cam)] == ["by-id", "by-path", "usb"]
    assert camera_identity(cam).kind == "by-id"
    assert camera_identity({"by_id": "", "by_path": "b"}).kind == "by-path"
    with pytest.raises(ValueError):
        camera_identity({"by_id": ""})


def test_config_snippet_roundtrip():
    loc = locator(ONE)
    snippet = loc.config_snippet(loc.list_cameras()[0])
    assert 'by_id = "usb-Image+_Webcam_AC410_HU1-video-index0"' in snippet
    assert '# by_path = "pci-0000:09:00.3-usb-0:2:1.0-video-index0"' in snippet
