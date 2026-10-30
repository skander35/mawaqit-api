from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
import requests
from fastapi import HTTPException
from fastapi.testclient import TestClient
from redis.exceptions import RedisError

from main import app
from scraping import cache, script
from tests.conftest import FROZEN_NOW, FakeResponse, make_conf, make_html

client = TestClient(app)
SLUG = "ma-mosquee"


def _at(monkeypatch, moment):
    monkeypatch.setattr(script, "_now", lambda: moment)


def test_semicolon_inside_json_string_does_not_break_parsing(site):
    conf = make_conf(announcements=[{"content": "Cours; samedi"}])
    site.response = FakeResponse(text=make_html(conf))

    result = script.fetch_mawaqit(SLUG)

    assert result["announcements"][0]["content"] == "Cours; samedi"


def test_request_has_a_timeout(site):
    script.fetch_mawaqit(SLUG)

    assert site.calls[0][1]["timeout"] > 0


@pytest.mark.parametrize("status", [403, 429, 500, 503])
def test_unexpected_status_gives_502(site, status):
    site.response = FakeResponse(status_code=status)

    with pytest.raises(HTTPException) as error:
        script.fetch_mawaqit(SLUG)

    assert error.value.status_code == 502


def test_network_error_gives_502(site):
    site.error = requests.Timeout()

    with pytest.raises(HTTPException) as error:
        script.fetch_mawaqit(SLUG)

    assert error.value.status_code == 502


def test_page_without_conf_data_gives_502(site):
    site.response = FakeResponse(text="<html></html>")

    with pytest.raises(HTTPException) as error:
        script.fetch_mawaqit(SLUG)

    assert error.value.status_code == 502


def test_unknown_mosque_gives_404(site):
    site.response = FakeResponse(status_code=404)

    with pytest.raises(HTTPException) as error:
        script.fetch_mawaqit(SLUG)

    assert error.value.status_code == 404


@pytest.mark.parametrize("slug", ["..", "a.b", "a b", "../secret"])
def test_invalid_slug_is_rejected_without_network(site, slug):
    with pytest.raises(HTTPException) as error:
        script.fetch_mawaqit(slug)

    assert error.value.status_code == 400
    assert site.calls == []


def test_same_day_uses_cache(site):
    script.fetch_mawaqit(SLUG)
    script.fetch_mawaqit(SLUG)

    assert len(site.calls) == 1


def test_new_day_refreshes_cache(site, monkeypatch):
    script.fetch_mawaqit(SLUG)
    _at(monkeypatch, FROZEN_NOW + timedelta(days=1))

    script.fetch_mawaqit(SLUG)

    assert len(site.calls) == 2


def test_times_of_the_day_are_never_those_of_a_previous_day(site, monkeypatch):
    script.fetch_mawaqit(SLUG)
    _at(monkeypatch, FROZEN_NOW + timedelta(days=1))
    site.error = requests.ConnectionError()

    times = script.get_prayer_times_of_the_day(SLUG)

    # Site injoignable : le calendrier du cache remplace les `times` de la veille
    assert times["fajr"] == "05:00"


def test_mosque_removed_from_site_is_not_served_from_cache(site, monkeypatch):
    script.fetch_mawaqit(SLUG)
    _at(monkeypatch, FROZEN_NOW + timedelta(days=1))
    site.response = FakeResponse(status_code=404)

    with pytest.raises(HTTPException) as error:
        script.fetch_mawaqit(SLUG)

    assert error.value.status_code == 404


def test_corrupted_cache_file_triggers_a_new_fetch(site, tmp_path):
    (tmp_path / f"{SLUG}.json").write_text("{pas du json")

    script.fetch_mawaqit(SLUG)

    assert len(site.calls) == 1


def test_redis_failure_does_not_break_requests(site, monkeypatch):
    class BrokenRedis:
        def get(self, key):
            raise RedisError()

        def set(self, *args, **kwargs):
            raise RedisError()

    monkeypatch.setattr(cache, "redisClient", BrokenRedis())

    assert script.fetch_mawaqit(SLUG)["shuruq"] == "07:02"



def test_real_now_is_timezone_aware(monkeypatch):
    monkeypatch.undo()

    assert script._now().tzinfo == ZoneInfo("Europe/Paris")


def test_next_prayer_is_today_when_one_remains(site):
    assert script.get_next_prayer(SLUG).model_dump() == {
        "name": "icha",
        "time": "19:40",
        "date": "2026-10-30",
    }


def test_next_prayer_after_icha_is_tomorrow_fajr(site, monkeypatch):
    _at(monkeypatch, FROZEN_NOW.replace(hour=22))

    assert script.get_next_prayer(SLUG).model_dump() == {
        "name": "fajr",
        "time": "05:00",
        "date": "2026-10-31",
    }


def test_next_prayer_skips_sunrise(site, monkeypatch):
    _at(monkeypatch, FROZEN_NOW.replace(hour=6, minute=30))

    assert script.get_next_prayer(SLUG).name == "dohr"


def test_next_prayer_crosses_year_end(site, monkeypatch):
    moment = datetime(2026, 12, 31, 22, 0, tzinfo=FROZEN_NOW.tzinfo)
    _at(monkeypatch, moment)

    assert script.get_next_prayer(SLUG).date == "2027-01-01"


def test_next_prayer_times_keeps_upstream_shape(site):
    times = script.get_next_prayer_times_of_the_day(SLUG)

    assert times["fajr"] == "05:00"  # passee : remplacee par celle de demain
    assert times["icha"] == "19:40"  # a venir : conservee


def test_next_prayer_route(site):
    response = client.get(f"/api/v1/{SLUG}/next-prayer")

    assert response.status_code == 200
    assert response.json()["name"] == "icha"
