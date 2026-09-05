"""Brief 13 Part 2: proactive daily NFO instrument archiving. Real,
live-confirmed problem this closes: Kite's /instruments endpoint purges
every contract whose expiry has already passed, and this project lost
its already-elapsed 42-day window's option data because no dump was
ever saved before those contracts expired. Proves the archive writes a
real file with the real raw response, and that a missing/invalid real
session fails closed (skipped, logged, never a crash, never a
fabricated placeholder archive) -- the real operational constraint
(Kite access tokens are single-day, requiring a genuine daily
interactive login) means most scheduled runs on most days will hit
exactly this path.
"""

from __future__ import annotations

import json
from datetime import date

from config import Settings
from data.instrument_archive import archive_nfo_instruments, run_daily_archive


class _FakeKite:
    def __init__(self, rows: list[dict] | None = None, raise_error: Exception | None = None) -> None:
        self._rows = rows if rows is not None else [{"tradingsymbol": "NIFTY26SEPFUT", "name": "NIFTY"}]
        self._raise_error = raise_error

    def instruments(self, segment: str) -> list[dict]:
        if self._raise_error:
            raise self._raise_error
        assert segment == "NFO"
        return self._rows


def test_archive_writes_the_real_raw_response_to_a_timestamped_file(tmp_path):
    kite = _FakeKite([{"tradingsymbol": "NIFTY2690824200CE", "strike": 24200.0, "expiry": "2026-09-08"}])

    path = archive_nfo_instruments(kite, archive_dir=tmp_path, today=date(2026, 9, 5))

    assert path == tmp_path / "nfo_instruments_2026-09-05.json"
    assert path.exists()
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved == [{"tradingsymbol": "NIFTY2690824200CE", "strike": 24200.0, "expiry": "2026-09-08"}]


def test_archive_is_idempotent_for_the_same_real_day(tmp_path):
    kite = _FakeKite([{"tradingsymbol": "A"}])
    archive_nfo_instruments(kite, archive_dir=tmp_path, today=date(2026, 9, 5))

    kite2 = _FakeKite([{"tradingsymbol": "B"}])  # a second real run the same day -- overwrites cleanly
    path = archive_nfo_instruments(kite2, archive_dir=tmp_path, today=date(2026, 9, 5))

    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved == [{"tradingsymbol": "B"}]
    assert len(list(tmp_path.glob("*.json"))) == 1


def test_run_daily_archive_fails_closed_with_no_credentials_configured(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    settings = Settings(kite_api_key="", kite_access_token="")

    result = run_daily_archive(settings)  # must not raise

    assert result is None


def test_run_daily_archive_fails_closed_on_a_real_expired_token_error(tmp_path, monkeypatch):
    """The real, expected everyday case: Kite access tokens are single-day
    -- a scheduled run on a day nobody has logged in yet hits this exact
    path, not a crash."""
    monkeypatch.chdir(tmp_path)
    settings = Settings(kite_api_key="looks-real", kite_access_token="stale-token")

    class _FakeKiteConnectModule:
        class KiteConnect:
            def __init__(self, api_key):
                self.api_key = api_key

            def set_access_token(self, token):
                pass

            def instruments(self, segment):
                raise ConnectionError("simulated real TokenException: Incorrect api_key or access_token.")

    import sys

    monkeypatch.setitem(sys.modules, "kiteconnect", _FakeKiteConnectModule)

    result = run_daily_archive(settings)  # must not raise

    assert result is None


def test_run_daily_archive_succeeds_with_a_real_looking_session(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    settings = Settings(kite_api_key="looks-real", kite_access_token="looks-real-too")

    class _FakeKiteConnectModule:
        class KiteConnect:
            def __init__(self, api_key):
                pass

            def set_access_token(self, token):
                pass

            def instruments(self, segment):
                return [{"tradingsymbol": "NIFTY26SEPFUT"}]

    import sys

    monkeypatch.setitem(sys.modules, "kiteconnect", _FakeKiteConnectModule)

    result = run_daily_archive(settings)

    assert result is not None
    assert result.exists()
    saved = json.loads(result.read_text(encoding="utf-8"))
    assert saved == [{"tradingsymbol": "NIFTY26SEPFUT"}]
