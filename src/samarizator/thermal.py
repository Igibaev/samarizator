"""Let a hot Mac cool down between heavy steps.

macOS reports thermal pressure as NSProcessInfo.thermalState: nominal, fair, serious,
critical. From "serious" on, the system is already throttling and the fans are loud;
continuing at full load only keeps it there. Between two steps (a recognised fragment,
a summary request) the work pauses until the state drops back, so the Mac cools while
the job waits. Nothing is paused in the normal states, so a cool Mac loses no time.
"""

import ctypes
import ctypes.util
import sys
import time

NOMINAL, FAIR, SERIOUS, CRITICAL = range(4)
# The longest single pause: macOS needs a minute or two to leave "serious".
MAX_PAUSE = 180
POLL = 5
_state_call = None


def thermal_state():
    """0–3 as NSProcessInfoThermalState, or None where it cannot be read."""
    global _state_call
    if sys.platform != "darwin" or _state_call is False:
        return None
    try:
        if _state_call is None:
            ctypes.CDLL("/System/Library/Frameworks/Foundation.framework/Foundation")
            objc = ctypes.CDLL(ctypes.util.find_library("objc") or "/usr/lib/libobjc.A.dylib")
            objc.objc_getClass.restype = ctypes.c_void_p
            objc.objc_getClass.argtypes = [ctypes.c_char_p]
            objc.sel_registerName.restype = ctypes.c_void_p
            objc.sel_registerName.argtypes = [ctypes.c_char_p]
            address = ctypes.cast(objc.objc_msgSend, ctypes.c_void_p).value
            send_object = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)(address)
            send_long = ctypes.CFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p)(address)
            info = send_object(objc.objc_getClass(b"NSProcessInfo"), objc.sel_registerName(b"processInfo"))
            selector = objc.sel_registerName(b"thermalState")
            _state_call = lambda: int(send_long(info, selector))  # noqa: E731
        return _state_call()
    except (OSError, AttributeError, TypeError, ValueError):
        _state_call = False
        return None


def cool_down(progress=lambda *_: None, enabled=True, state=thermal_state, sleep=time.sleep, limit=MAX_PAUSE):
    """Wait while the Mac is under serious thermal pressure. Returns seconds waited."""
    if not enabled:
        return 0
    waited = 0
    while waited < limit:
        current = state()
        if current is None or current < SERIOUS:
            break
        if waited == 0:
            progress("Mac сильно нагрелся — пауза, чтобы он остыл. Работа продолжится сама…")
        sleep(POLL)
        waited += POLL
    return waited
