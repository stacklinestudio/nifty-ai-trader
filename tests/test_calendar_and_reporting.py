from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from data.calendar import NseCalendar
from monitoring.daily_report import write_daily_report


def test_calendar_fails_closed_on_weekend_and_holiday():
    holiday = date(2025, 1, 1)
    calendar = NseCalendar({holiday})
    assert not calendar.is_trading_day(holiday)
    assert not calendar.is_trading_day(date(2025, 1, 4))
    assert calendar.is_market_open(datetime(2025, 1, 2, 10, tzinfo=ZoneInfo("Asia/Kolkata")))


def test_daily_report_is_written(tmp_path: Path):
    report = write_daily_report(
        tmp_path / "daily.md", "2025-01-01", "Test", None, None, "unavailable"
    )
    assert report.exists() and "NO TRADE" in report.read_text()
