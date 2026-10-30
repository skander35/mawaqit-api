import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
import requests

from config.settings import settings
from scraping import script

PARIS = ZoneInfo("Europe/Paris")
# Jeudi 30 octobre 2026, 19:30 a Paris (18:30 UTC)
FROZEN_NOW = datetime(2026, 10, 30, 19, 30, tzinfo=PARIS)
TODAY = ["06:01", "07:02", "13:03", "16:04", "18:05", "19:40"]


def make_conf(**overrides):
    days_in_month = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    calendar = [
        {str(day): ["05:00", "06:00", "12:00", "15:00", "17:00", "18:30"] for day in range(1, count + 1)}
        for count in days_in_month
    ]
    iqama = [
        {str(day): ["+10", "+10", "+10", "+5", "+10"] for day in range(1, count + 1)}
        for count in days_in_month
    ]
    conf = {
        "times": [TODAY[0], TODAY[2], TODAY[3], TODAY[4], TODAY[5]],
        "shuruq": TODAY[1],
        "calendar": calendar,
        "iqamaCalendar": iqama,
        "announcements": [],
        "womenSpace": True,
        "janazaPrayer": True,
        "aidPrayer": True,
        "childrenCourses": False,
        "adultCourses": False,
        "ramadanMeal": False,
        "handicapAccessibility": False,
        "ablutions": True,
        "parking": False,
    }
    return {**conf, **overrides}


def make_html(conf):
    return f"<html><script>let lang = 'fr'; var confData = {json.dumps(conf)}; let other = 1;</script></html>"


class FakeResponse:
    def __init__(self, status_code=200, text=""):
        self.status_code = status_code
        self.text = text


class FakeSite:
    """Remplace requests.get : aucun test ne doit contacter mawaqit.net."""

    def __init__(self):
        self.response = FakeResponse(text=make_html(make_conf()))
        self.error = None
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.error:
            raise self.error
        return self.response


@pytest.fixture(autouse=True)
def site(monkeypatch, tmp_path):
    fake = FakeSite()
    monkeypatch.setattr(requests, "get", fake.get)
    monkeypatch.setattr(settings, "CACHE_DIR", str(tmp_path), raising=False)
    monkeypatch.setattr(script, "_now", lambda: FROZEN_NOW)
    return fake
