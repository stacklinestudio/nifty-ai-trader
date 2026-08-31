from __future__ import annotations

import inspect
import os

from dotenv import load_dotenv


def test_load_dotenv_populates_environment_from_a_file(tmp_path):
    env_file = tmp_path / ".env.local"
    env_file.write_text("SOME_TEST_ONLY_VAR=hello-from-dotenv\n")
    os.environ.pop("SOME_TEST_ONLY_VAR", None)
    try:
        load_dotenv(env_file)
        assert os.environ.get("SOME_TEST_ONLY_VAR") == "hello-from-dotenv"
    finally:
        os.environ.pop("SOME_TEST_ONLY_VAR", None)


def test_env_local_takes_precedence_over_env_when_loaded_first(tmp_path):
    # Mirrors main.py's actual call order: load_dotenv(".env.local") then
    # load_dotenv(".env") -- python-dotenv's default override=False means
    # the second call fills gaps only, never overwriting what the first
    # already set. This is what makes .env.local "win."
    local_file = tmp_path / ".env.local"
    base_file = tmp_path / ".env"
    local_file.write_text("SOME_PRECEDENCE_VAR=from-local\n")
    base_file.write_text("SOME_PRECEDENCE_VAR=from-base\n")
    os.environ.pop("SOME_PRECEDENCE_VAR", None)
    try:
        load_dotenv(local_file)
        load_dotenv(base_file)
        assert os.environ.get("SOME_PRECEDENCE_VAR") == "from-local"
    finally:
        os.environ.pop("SOME_PRECEDENCE_VAR", None)


def test_env_only_present_in_env_still_gets_picked_up(tmp_path):
    local_file = tmp_path / ".env.local"
    base_file = tmp_path / ".env"
    local_file.write_text("")
    base_file.write_text("ONLY_IN_ENV_VAR=from-base\n")
    os.environ.pop("ONLY_IN_ENV_VAR", None)
    try:
        load_dotenv(local_file)
        load_dotenv(base_file)
        assert os.environ.get("ONLY_IN_ENV_VAR") == "from-base"
    finally:
        os.environ.pop("ONLY_IN_ENV_VAR", None)


def test_main_loads_dotenv_before_importing_config():
    """The actual correctness point this whole fix hinges on: config.py's
    Settings fields default via os.getenv(...) evaluated when the config
    module is first imported, not when Settings() is instantiated -- so
    load_dotenv must run before `from config import ...`, not merely
    before any Settings() call. If a later edit reorders main.py's imports
    so config is imported first, this test catches it; the bug it guards
    against is silent (Settings just quietly falls back to defaults).
    """
    import main

    source = inspect.getsource(main)
    # Search for the real import statement, not the phrase "from config
    # import" in general -- that substring also appears inside this
    # module's own explanatory comment above the load_dotenv calls.
    load_dotenv_pos = source.index('load_dotenv(".env.local")')
    config_import_pos = source.index("\nfrom config import IST, Settings")
    assert load_dotenv_pos < config_import_pos
