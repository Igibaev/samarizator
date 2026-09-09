"""CPU-only subprocesses and aggregate RSS watchdog (not an OS hard quota)."""

import os
import signal
import subprocess
import time
from pathlib import Path

import psutil


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
            total += p.memory_info().rss
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
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
