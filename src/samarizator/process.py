"""Subprocesses under an aggregate memory watchdog (not an OS hard quota).

On macOS the watchdog reads each process's physical footprint, the number Activity
Monitor shows as "Memory": unlike RSS it includes Metal (GPU) allocations, so
recognition and summaries can run on the GPU and still stay inside the budget.
"""

import ctypes
import ctypes.util
import os
import signal
import struct
import subprocess
import sys
import time
from pathlib import Path

import psutil

# libproc's proc_pid_rusage(pid, RUSAGE_INFO_V2, buffer): ri_phys_footprint follows a
# 16-byte UUID and seven uint64 counters (sys/resource.h, struct rusage_info_v2).
RUSAGE_INFO_V2 = 2
RUSAGE_V2_SIZE = 16 + 18 * 8
FOOTPRINT_OFFSET = 16 + 7 * 8
_libproc = None


def phys_footprint(pid):
    """Bytes of physical memory charged to `pid` on macOS, GPU memory included; None elsewhere."""
    global _libproc
    if sys.platform != "darwin":
        return None
    try:
        if _libproc is None:
            _libproc = ctypes.CDLL(ctypes.util.find_library("proc") or "/usr/lib/libproc.dylib")
            _libproc.proc_pid_rusage.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
            _libproc.proc_pid_rusage.restype = ctypes.c_int
        buffer = ctypes.create_string_buffer(RUSAGE_V2_SIZE)
        if _libproc.proc_pid_rusage(pid, RUSAGE_INFO_V2, buffer) != 0:
            return None
        return struct.unpack_from("<Q", buffer.raw, FOOTPRINT_OFFSET)[0]
    except (OSError, AttributeError):
        return None


def process_memory(process):
    """The larger of RSS (mapped model files) and the footprint (Metal buffers)."""
    rss = process.memory_info().rss
    footprint = phys_footprint(process.pid)
    return max(rss, footprint or 0)


class BudgetExceeded(RuntimeError):
    pass


def rss_tree(pid):
    try:
        root = psutil.Process(pid)
        procs = [root, *root.children(recursive=True)]
    except psutil.NoSuchProcess:
        return 0
    total = 0
    for p in procs:
        try:
            total += process_memory(p)
        except psutil.NoSuchProcess:
            pass
        except psutil.AccessDenied:
            raise RuntimeError("Нет доступа к измерению памяти обработчика. Обработка остановлена.") from None
    return total


def kill_group(proc):
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    proc.wait()


def run_command(args, log: Path, timeout=3600):
    env = os.environ.copy()
    # Never forward corporate credentials to multimedia tools.
    env.pop("SAMARIZATOR_API_KEY", None)
    env.update(
        OMP_NUM_THREADS=env.get("SAMARIZATOR_THREADS", "4"),
        OPENBLAS_NUM_THREADS=env.get("SAMARIZATOR_THREADS", "4"),
    )
    with log.open("wb") as out:
        proc = subprocess.Popen(args, stdout=out, stderr=subprocess.STDOUT, env=env)
        try:
            proc.wait(timeout=timeout)
        except BaseException:
            proc.kill()
            proc.wait()
            raise
    if proc.returncode:
        # Do not expose paths/transcripts/tool output in UI errors.
        raise RuntimeError(f"{Path(args[0]).name}: код {proc.returncode}. Проверьте файл и модель.")


def supervise(args, budget_gb, root_pid=None, callback=None, cancelled=None):
    root_pid = root_pid or os.getpid()
    limit = budget_gb * 1024**3 * 0.90
    proc = subprocess.Popen(
        args, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    try:
        while True:
            rss = rss_tree(root_pid)
            if rss <= 0:
                raise RuntimeError("Не удалось измерить память приложения. Обработка остановлена.")
            if callback:
                callback(rss)
            if rss >= limit:
                raise BudgetExceeded(
                    "Обработка остановлена у границы бюджета памяти. "
                    "Фрагменты сохранены. Уменьшите модель или увеличьте бюджет."
                )
            if cancelled and cancelled():
                raise InterruptedError("Обработка отменена. Готовые фрагменты сохранены.")
            if proc.poll() is not None:
                return proc.returncode
            time.sleep(0.1)
    finally:
        # Also stop descendants if their worker was interrupted/crashed.
        kill_group(proc)
