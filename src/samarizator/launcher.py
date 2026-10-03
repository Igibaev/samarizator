"""Entry point of the frozen app.

The window and the background worker are the same executable: the window starts
workers as `Samarizator --run-module samarizator.worker ...`, because a frozen app has
no separate interpreter to call with `-m`.
"""

import multiprocessing
import runpy
import sys

ALLOWED = {
    "samarizator.worker",
    "samarizator.screencapture",
    "samarizator.live",
    "samarizator.setup_models",
}


def main():
    multiprocessing.freeze_support()
    if len(sys.argv) > 2 and sys.argv[1] == "--run-module":
        module = sys.argv[2]
        if module not in ALLOWED:
            print(f"Неизвестный модуль: {module}", file=sys.stderr)
            return 2
        sys.argv = [module, *sys.argv[3:]]
        try:
            runpy.run_module(module, run_name="__main__", alter_sys=True)
        except SystemExit as exc:
            return exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
        return 0
    from samarizator.app import main as app_main

    return app_main()


if __name__ == "__main__":
    sys.exit(main())
