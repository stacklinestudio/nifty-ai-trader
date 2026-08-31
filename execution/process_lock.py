"""Single-instance guard for `python main.py run`.

DailyLimits (trades count, realized P&L, profit target) lives in-memory
per Orchestrator instance and is not synchronized across processes -- two
concurrent `run` instances against the same database could each
independently believe they're within the daily trade/loss limits while
combined real exposure exceeds them. This exists to make that impossible,
not just unlikely.
"""

from __future__ import annotations

import os
from pathlib import Path


class AlreadyRunningError(RuntimeError):
    pass


def _pid_is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


class ProcessLock:
    """A stale lock (the recorded PID is no longer running, or the file is
    unreadable/corrupt) is treated as safe to take over -- this is what
    makes recovery from a hard crash automatic rather than requiring a
    human to manually delete a leftover lock file before the next
    scheduled run.
    """

    def __init__(self, lock_path: Path) -> None:
        self.lock_path = lock_path

    def acquire(self) -> None:
        if self.lock_path.exists():
            existing_pid: int | None
            try:
                existing_pid = int(self.lock_path.read_text().strip())
            except (ValueError, OSError):
                existing_pid = None
            if existing_pid is not None and _pid_is_alive(existing_pid):
                raise AlreadyRunningError(
                    f"Another instance is already running (pid {existing_pid}, "
                    f"lock file {self.lock_path})"
                )
        self.lock_path.write_text(str(os.getpid()))

    def release(self) -> None:
        try:
            if self.lock_path.exists() and self.lock_path.read_text().strip() == str(
                os.getpid()
            ):
                self.lock_path.unlink()
        except OSError:
            pass

    def __enter__(self) -> ProcessLock:  # noqa: PYI034 - typing.Self needs py311+; this targets py310.
        self.acquire()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.release()
