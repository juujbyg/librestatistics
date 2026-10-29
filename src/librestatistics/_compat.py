# SPDX-License-Identifier: GPL-2.0-or-later
"""Platform helpers shared by the librestatistics editions (Linux + Windows).

Everything here degrades to "no data" instead of raising.  Only psutil and the
standard library are used (ctypes/winreg for the Windows GPU backend).
"""

import os
import platform
import sys

import psutil

IS_WIN = sys.platform == "win32"


# ── small portable wrappers ──────────────────────────────────────────────────

def hostname():
    return platform.node() or "localhost"


def kernel():
    return platform.release()


def loadavg():
    """(1, 5, 15)-minute load average, or None if the platform can't tell."""
    try:
        return tuple(os.getloadavg())
    except (AttributeError, OSError):
        pass
    try:
        return tuple(psutil.getloadavg())
    except Exception:
        return None


def temperatures():
    try:
        return psutil.sensors_temperatures() if hasattr(psutil, "sensors_temperatures") else {}
    except Exception:
        return {}


def fans():
    try:
        return psutil.sensors_fans() if hasattr(psutil, "sensors_fans") else {}
    except Exception:
        return {}


def battery():
    try:
        return psutil.sensors_battery() if hasattr(psutil, "sensors_battery") else None
    except Exception:
        return None


def root_path():
    """Path whose usage represents 'the' system disk."""
    if IS_WIN:
        return os.environ.get("SystemDrive", "C:") + "\\"
    return "/"


def is_system_user(user):
    """True for root on Linux and the built-in service accounts on Windows."""
    if user == "root":
        return True
    if IS_WIN and user:
        u = user.upper()
        return u.startswith(("NT AUTHORITY\\", "NT SERVICE\\", "FONT DRIVER HOST\\",
                             "WINDOW MANAGER\\", "UMFD-", "DWM-"))
    return False


def short_user(user):
    """Drop the DOMAIN\\ prefix on Windows."""
    if IS_WIN and user and "\\" in user:
        return user.rsplit("\\", 1)[-1]
    return user


# ── Windows GPU utilisation via PDH (ctypes, no extra dependencies) ──────────

def _make_structs():
    import ctypes
    from ctypes import wintypes

    class FmtValue(ctypes.Structure):
        _fields_ = [("CStatus", wintypes.DWORD), ("doubleValue", ctypes.c_double)]

    class Item(ctypes.Structure):
        _fields_ = [("szName", ctypes.c_wchar_p), ("FmtValue", FmtValue)]

    return Item


PDH_FMT_DOUBLE = 0x00000200
PDH_MORE_DATA = 0x800007D2
PDH_VALID = (0, 1)  # PDH_CSTATUS_VALID_DATA, PDH_CSTATUS_NEW_DATA

ENGINE_COUNTER = r"\GPU Engine(*)\Utilization Percentage"
MEM_COUNTERS = {"dedicated": r"\GPU Adapter Memory(*)\Dedicated Usage",
                "shared": r"\GPU Adapter Memory(*)\Shared Usage"}


def parse_counter_array(buf, count):
    """Decode a PDH_FMT_COUNTERVALUE_ITEM_W array -> [(instance name, float)]."""
    import ctypes
    Item = _make_structs()
    items = ctypes.cast(buf, ctypes.POINTER(Item))
    out = []
    for i in range(count):
        it = items[i]
        if it.FmtValue.CStatus in PDH_VALID and it.szName:
            out.append((it.szName, it.FmtValue.doubleValue))
    return out


def gpu_name_from_registry():
    try:
        import winreg
        base = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
        for i in range(8):
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, f"{base}\\{i:04d}") as k:
                    desc = winreg.QueryValueEx(k, "DriverDesc")[0]
                    if desc:
                        return str(desc)
            except OSError:
                continue
    except Exception:
        pass
    return "GPU"


class WindowsGPU:
    """GPU utilisation from the Windows performance counters (any vendor)."""

    DEFAULT_LABELS = {"3D": "3D", "VideoDecode": "Video", "VideoEncode": "Video",
                      "VideoProcessing": "VidEnh", "Copy": "Blit"}

    def __init__(self, labels=None):
        self.labels = dict(labels or self.DEFAULT_LABELS)
        self.name = "GPU"
        self._pdh = None
        self._query = None
        self._counters = {}

    # -- PDH plumbing --

    def _open(self):
        import ctypes
        from ctypes import wintypes
        pdh = ctypes.WinDLL("pdh")
        H = ctypes.c_void_p
        pdh.PdhOpenQueryW.argtypes = [wintypes.LPCWSTR, ctypes.c_size_t, ctypes.POINTER(H)]
        pdh.PdhAddEnglishCounterW.argtypes = [H, wintypes.LPCWSTR, ctypes.c_size_t, ctypes.POINTER(H)]
        pdh.PdhCollectQueryData.argtypes = [H]
        pdh.PdhGetFormattedCounterArrayW.argtypes = [
            H, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD),
            ctypes.c_void_p]
        for fn in (pdh.PdhOpenQueryW, pdh.PdhAddEnglishCounterW, pdh.PdhCollectQueryData,
                   pdh.PdhGetFormattedCounterArrayW):
            fn.restype = wintypes.DWORD
        self._pdh = pdh
        q = H()
        if pdh.PdhOpenQueryW(None, 0, ctypes.byref(q)) != 0:
            raise OSError("PdhOpenQuery failed")
        self._query = q
        for key, path in (("engine", ENGINE_COUNTER), *MEM_COUNTERS.items()):
            c = H()
            if pdh.PdhAddEnglishCounterW(q, path, 0, ctypes.byref(c)) == 0:
                self._counters[key] = c
            elif key == "engine":
                raise OSError("GPU Engine counter unavailable")
        if pdh.PdhCollectQueryData(q) != 0:
            raise OSError("PdhCollectQueryData failed")

    def _array(self, counter):
        import ctypes
        from ctypes import wintypes
        size, count = wintypes.DWORD(0), wintypes.DWORD(0)
        r = self._pdh.PdhGetFormattedCounterArrayW(counter, PDH_FMT_DOUBLE, ctypes.byref(size),
                                                   ctypes.byref(count), None)
        if r != PDH_MORE_DATA or not size.value:
            return []
        buf = ctypes.create_string_buffer(size.value)
        r = self._pdh.PdhGetFormattedCounterArrayW(counter, PDH_FMT_DOUBLE, ctypes.byref(size),
                                                   ctypes.byref(count), buf)
        if r != 0:
            return []
        return parse_counter_array(buf, count.value)

    @classmethod
    def detect(cls, labels=None):
        """[WindowsGPU] when running on Windows and PDH GPU counters work, else []."""
        if sys.platform != "win32":
            return []
        try:
            g = cls(labels)
            g._open()
            g.name = gpu_name_from_registry()
            return [g]
        except Exception:
            return []

    # -- sampling --

    def summarize(self, engine_rows):
        """[(instance, percent)] -> (util, {label: percent}).

        Instances look like pid_123_luid_0x..._phys_0_eng_0_engtype_3D.  Sum all
        processes per physical engine, then take the busiest engine of each type.
        """
        per_engine = {}
        for inst, v in engine_rows:
            if "engtype_" not in inst:
                continue
            tail = inst.split("luid_", 1)[-1]
            per_engine[tail] = per_engine.get(tail, 0.0) + v
        by_type = {}
        for tail, v in per_engine.items():
            etype = tail.rsplit("engtype_", 1)[-1]
            by_type[etype] = max(by_type.get(etype, 0.0), min(100.0, v))
        engines = {lab: 0.0 for lab in dict.fromkeys(self.labels.values())}
        for etype, v in by_type.items():
            lab = self.labels.get(etype, etype)
            engines[lab] = max(engines.get(lab, 0.0), v)
        util = min(100.0, max(by_type.values())) if by_type else None
        return util, engines

    def sample(self):
        try:
            if self._pdh.PdhCollectQueryData(self._query) != 0:
                return {}
            rows = self._array(self._counters["engine"])
            util, engines = self.summarize(rows)
            mem = {k: sum(v for _, v in self._array(self._counters[k])) if k in self._counters else 0.0
                   for k in MEM_COUNTERS}
            used = mem["dedicated"] or mem["shared"]
            return {"util": util, "engines": engines if rows else None,
                    "mem_used": used or None, "mem_total": None, "temp": None}
        except Exception:
            return {}
