import requests
from bs4 import BeautifulSoup
from fastapi import HTTPException
from config.settings import settings
from typing import List
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import json
import re
import models.models as models
from scraping import cache

TIMEZONE = ZoneInfo(settings.TIMEZONE)
SLUG_PATTERN = re.compile(r'[A-Za-z0-9_-]+')
CONF_DATA_PATTERN = re.compile(r'(?:var|let|const)\s+confData\s*=\s*')
FETCHED_ON = "_fetchedOn"
PRAYERS = ("fajr", "dohr", "asr", "maghreb", "icha")


def _now():
    return datetime.now(TIMEZONE)


def _parse_conf_data(html, masjid_id):
    soup = BeautifulSoup(html, 'html.parser')
    script = soup.find('script', string=CONF_DATA_PATTERN)
    if not script:
        raise HTTPException(status_code=502, detail=f"Script containing confData not found for {masjid_id}")
    start = CONF_DATA_PATTERN.search(script.string).end()
    try:
        conf_data, _ = json.JSONDecoder().raw_decode(script.string, start)
    except ValueError:
        raise HTTPException(status_code=502, detail=f"Failed to extract confData JSON for {masjid_id}")
    return conf_data


def _download(masjid_id):
    url = f"https://mawaqit.net/fr/{masjid_id}"
    try:
        r = requests.get(url, timeout=settings.REQUEST_TIMEOUT)
    except requests.RequestException:
        raise HTTPException(status_code=502, detail=f"something went wrong fetching {url}")
    if r.status_code == 404:
        raise HTTPException(status_code=404, detail=f"{masjid_id} not found")
    if r.status_code != 200:
        raise HTTPException(status_code=502, detail=f"something went wrong fetching {url}")
    return _parse_conf_data(r.text, masjid_id)


def fetch_mawaqit(masjid_id:str):
    if not SLUG_PATTERN.fullmatch(masjid_id):
        raise HTTPException(status_code=400, detail="Invalid masjid id")

    today = _now().date().isoformat()
    cached = cache.read(masjid_id)
    if cached and cached.get(FETCHED_ON) == today:
        return cached

    try:
        conf_data = {**_download(masjid_id), FETCHED_ON: today}
    except HTTPException as error:
        if cached and error.status_code != 404:
            print(f"Serving cached data for {masjid_id}: {error.detail}")
            return cached
        raise
    cache.write(masjid_id, conf_data)
    return conf_data


def _calendar_times(confData, day):
    times = confData["calendar"][day.month - 1][str(day.day)]
    return models.PrayerTimes(fajr=times[0], sunrise=times[1], dohr=times[2], asr=times[3], maghreb=times[4], icha=times[5])


def _times_of_the_day(confData):
    # `times` ne vaut que pour le jour du scraping : hors de ce jour, seul le calendrier est fiable
    today = _now().date()
    if confData.get(FETCHED_ON) != today.isoformat():
        return _calendar_times(confData, today)
    times = confData["times"]
    return models.PrayerTimes(fajr=times[0], sunrise=confData["shuruq"], dohr=times[1], asr=times[2], maghreb=times[3], icha=times[4])


def get_prayer_times_of_the_day(masjid_id):
    return _times_of_the_day(fetch_mawaqit(masjid_id)).model_dump()


def get_next_prayer_times_of_the_day(masjid_id):
    now = _now()
    confData = fetch_mawaqit(masjid_id)
    today = _times_of_the_day(confData).model_dump()
    tomorrow = _calendar_times(confData, now.date() + timedelta(days=1)).model_dump()
    current_time = now.strftime("%H:%M")
    return {name: value if value > current_time else tomorrow[name] for name, value in today.items()}


def _next_prayer(name, day, times):
    moment = datetime.combine(day, datetime.strptime(times[name], "%H:%M").time(), TIMEZONE)
    return models.NextPrayer(
        name=name,
        time=times[name],
        date=day.isoformat(),
        timestamp=int(moment.timestamp()),
        sunrise=times["sunrise"] if name == "fajr" else None,
    )


def get_next_prayer(masjid_id):
    now = _now()
    confData = fetch_mawaqit(masjid_id)
    today = _times_of_the_day(confData).model_dump()
    current_time = now.strftime("%H:%M")
    for name in PRAYERS:
        if today[name] > current_time:
            return _next_prayer(name, now.date(), today)
    tomorrow = now.date() + timedelta(days=1)
    return _next_prayer("fajr", tomorrow, _calendar_times(confData, tomorrow).model_dump())


def get_calendar(masjid_id):
    confData = fetch_mawaqit(masjid_id)
    return confData["calendar"]

def get_month(masjid_id, month_number):
    if month_number < 1 or month_number > 12:
        raise HTTPException(status_code=400, detail=f"Month number should be between 1 and 12")
    confData = fetch_mawaqit(masjid_id)
    month = confData["calendar"][month_number - 1]
    prayer_times_list = [
        models.PrayerTimes( 
            fajr=prayer[0],
            sunrise=prayer[1],
            dohr=prayer[2],
            asr=prayer[3],
            maghreb=prayer[4],
            icha=prayer[5]
        )
        for prayer in month.values()
    ]
    return prayer_times_list

def get_month_iqama(masjid_id, month_number):
    if month_number < 1 or month_number > 12:
        raise HTTPException(status_code=400, detail=f"Month number should be between 1 and 12")
    confData = fetch_mawaqit(masjid_id)
    month = confData["iqamaCalendar"][month_number - 1]
    iqama_times_list = [
        models.IqamaPrayerTimes( 
            fajr=iqama[0],
            dohr=iqama[1],
            asr=iqama[2],
            maghreb=iqama[3],
            icha=iqama[4]
        )
        for iqama in month.values()
    ]

    return iqama_times_list

def get_announcements(masjid_id: int) -> List[models.Announcement]:
    confData = fetch_mawaqit(masjid_id)
    announcements = confData.get("announcements", [])
    return [models.Announcement(**a) for a in announcements]

def get_services(masjid_id: int) -> models.MosqueServices:
    confData = fetch_mawaqit(masjid_id)
    mosque_services = models.MosqueServices(**confData)
    return mosque_services