"""Minimale DirectShow-Anbindung (IAMCameraControl / IAMVideoProcAmp) per comtypes."""
import ctypes
from ctypes import POINTER, c_long, HRESULT
from comtypes import GUID, IUnknown, COMMETHOD
from pygrabber.dshow_graph import FilterGraph


class IAMCameraControl(IUnknown):
    _iid_ = GUID("{C6E13370-30AC-11d0-A18C-00A0C9118956}")
    _methods_ = [
        COMMETHOD([], HRESULT, "GetRange", (["in"], c_long, "Property"),
                  (["out"], POINTER(c_long), "Min"), (["out"], POINTER(c_long), "Max"),
                  (["out"], POINTER(c_long), "Step"), (["out"], POINTER(c_long), "Default"),
                  (["out"], POINTER(c_long), "Caps")),
        COMMETHOD([], HRESULT, "Set", (["in"], c_long, "Property"),
                  (["in"], c_long, "Value"), (["in"], c_long, "Flags")),
        COMMETHOD([], HRESULT, "Get", (["in"], c_long, "Property"),
                  (["out"], POINTER(c_long), "Value"), (["out"], POINTER(c_long), "Flags")),
    ]


class IAMVideoProcAmp(IUnknown):
    _iid_ = GUID("{C6E13360-30AC-11d0-A18C-00A0C9118956}")
    _methods_ = IAMCameraControl._methods_


CAM_PROPS = ["Pan", "Tilt", "Roll", "Zoom", "Exposure", "Iris", "Focus"]
PROC_PROPS = ["Brightness", "Contrast", "Hue", "Saturation", "Sharpness", "Gamma",
              "ColorEnable", "WhiteBalance", "BacklightCompensation", "Gain"]
FLAG_AUTO, FLAG_MANUAL = 1, 2


class Camera:
    def __init__(self, name_part="Rally"):
        self.graph = FilterGraph()
        names = self.graph.get_input_devices()
        idx = next(i for i, n in enumerate(names) if name_part.lower() in n.lower())
        self.name = names[idx]
        self.graph.add_video_input_device(idx)
        flt = self.graph.get_input_device().instance
        self.cam = flt.QueryInterface(IAMCameraControl)
        try:
            self.proc = flt.QueryInterface(IAMVideoProcAmp)
        except Exception:
            self.proc = None

    def _iface(self, kind):
        return self.cam if kind == "cam" else self.proc

    def range(self, kind, prop):
        names = CAM_PROPS if kind == "cam" else PROC_PROPS
        try:
            mn, mx, st, df, caps = self._iface(kind).GetRange(names.index(prop))
            return dict(min=mn, max=mx, step=st, default=df, caps=caps)
        except Exception:
            return None

    def get(self, kind, prop):
        names = CAM_PROPS if kind == "cam" else PROC_PROPS
        return self._iface(kind).Get(names.index(prop))

    def set(self, kind, prop, value, auto=False):
        names = CAM_PROPS if kind == "cam" else PROC_PROPS
        self._iface(kind).Set(names.index(prop), int(value), FLAG_AUTO if auto else FLAG_MANUAL)
