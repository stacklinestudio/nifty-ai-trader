from __future__ import annotations

import os

import pytest

from execution.process_lock import AlreadyRunningError, ProcessLock


def test_acquire_writes_own_pid(tmp_path):
    lock_path = tmp_path / "run.lock"
    lock = ProcessLock(lock_path)
    lock.acquire()
    try:
        assert lock_path.read_text().strip() == str(os.getpid())
    finally:
        lock.release()


def test_second_acquire_by_a_live_process_is_rejected(tmp_path):
    lock_path = tmp_path / "run.lock"
    first = ProcessLock(lock_path)
    first.acquire()
    try:
        second = ProcessLock(lock_path)
        with pytest.raises(AlreadyRunningError):
            second.acquire()
    finally:
        first.release()


def test_release_removes_the_lock_file(tmp_path):
    lock_path = tmp_path / "run.lock"
    lock = ProcessLock(lock_path)
    lock.acquire()
    lock.release()
    assert not lock_path.exists()


def test_stale_lock_from_a_dead_pid_is_safely_taken_over(tmp_path):
    lock_path = tmp_path / "run.lock"
    # A PID essentially guaranteed not to correspond to a live process.
    lock_path.write_text("999999")

    lock = ProcessLock(lock_path)
    lock.acquire()  # must not raise
    try:
        assert lock_path.read_text().strip() == str(os.getpid())
    finally:
        lock.release()


def test_corrupt_lock_file_is_safely_taken_over(tmp_path):
    lock_path = tmp_path / "run.lock"
    lock_path.write_text("not-a-pid")

    lock = ProcessLock(lock_path)
    lock.acquire()  # must not raise
    try:
        assert lock_path.read_text().strip() == str(os.getpid())
    finally:
        lock.release()


def test_context_manager_releases_on_exit(tmp_path):
    lock_path = tmp_path / "run.lock"
    with ProcessLock(lock_path):
        assert lock_path.exists()
    assert not lock_path.exists()


def test_release_does_not_remove_a_lock_now_owned_by_someone_else(tmp_path):
    # If this process's lock went stale and another process took it over,
    # release() must not delete the new owner's lock out from under it.
    lock_path = tmp_path / "run.lock"
    lock = ProcessLock(lock_path)
    lock.acquire()
    lock_path.write_text("123456")  # simulate another process having taken over

    lock.release()

    assert lock_path.exists() and lock_path.read_text().strip() == "123456"


def test_main_run_command_rejects_a_concurrent_second_invocation(tmp_path, monkeypatch):
    """CLI-level check that `python main.py run` actually uses the lock,
    without ever touching the real blocking scheduler loop -- run_scheduled_day
    is replaced with a fast fake so this can't hang outside market hours.

    Settings.database_path defaults via os.getenv(...) evaluated at config
    module import time, so monkeypatching the DATABASE_PATH env var here
    would have no effect on an already-imported Settings class -- this
    instead patches main_module.Settings itself to a callable returning a
    Settings pointed at tmp_path, which main()'s own `Settings()` call
    picks up via normal module-global name resolution.
    """
    import sys

    import main as main_module
    from config import Settings as RealSettings

    db_path = tmp_path / "paper.db"
    monkeypatch.setattr(main_module, "Settings", lambda: RealSettings(database_path=db_path))
    monkeypatch.setattr(main_module, "run_scheduled_day", lambda settings: {"faked": True})
    monkeypatch.setattr(sys, "argv", ["main.py", "run"])

    first_exit = main_module.main()
    assert first_exit == 0

    # The first run already released its lock on completion (the `finally`
    # block) -- to actually exercise "concurrent," hold the lock open like a
    # real still-running process would.
    lock_path = tmp_path / "paper.db.lock"
    held_lock = ProcessLock(lock_path)
    held_lock.acquire()
    try:
        second_exit = main_module.main()
        assert second_exit == 1
    finally:
        held_lock.release()
