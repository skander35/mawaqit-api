import json
import os
import tempfile
from pathlib import Path

from redis.exceptions import RedisError

from config.redisClient import redisClient
from config.settings import settings

REDIS_TTL_SECONDS = 2592000


def _read_redis(masjid_id):
    if redisClient is None:
        return None
    try:
        return redisClient.get(masjid_id)
    except RedisError:
        print("Error when reading from cache")
        return None


def _read_file(masjid_id):
    try:
        return (Path(settings.CACHE_DIR) / f"{masjid_id}.json").read_text()
    except OSError:
        return None


def read(masjid_id):
    raw = _read_redis(masjid_id) or _read_file(masjid_id)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        print(f"Ignoring corrupted cache entry for {masjid_id}")
        return None


def _write_file(masjid_id, payload):
    directory = Path(settings.CACHE_DIR)
    directory.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=directory)
    try:
        with os.fdopen(fd, "w") as file:
            file.write(payload)
        os.replace(temporary, directory / f"{masjid_id}.json")
    except OSError:
        os.unlink(temporary)
        raise


def write(masjid_id, conf):
    payload = json.dumps(conf)
    if redisClient is not None:
        try:
            redisClient.set(masjid_id, payload, ex=REDIS_TTL_SECONDS)
        except RedisError:
            print("Error when writing to cache")
    try:
        _write_file(masjid_id, payload)
    except OSError as error:
        print(f"Error when writing cache file: {error}")
