"""Memoria maxima del propio proceso; no requiere dependencias."""
import os


def memoria_maxima_mb():
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        class Contadores(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
                (k, ctypes.c_size_t) for k in (
                    "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
                    "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage",
                    "PagefileUsage", "PeakPagefileUsage")]

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Contadores), wintypes.DWORD]
        datos = Contadores()
        datos.cb = ctypes.sizeof(datos)
        if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(datos), datos.cb):
            raise ctypes.WinError(ctypes.get_last_error())
        return datos.PeakWorkingSetSize / 1024 ** 2
    import resource
    import sys
    pico = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return pico / (1024 ** 2 if sys.platform == "darwin" else 1024)
