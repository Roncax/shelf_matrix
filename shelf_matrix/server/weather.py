"""Open-Meteo weather fetcher that publishes to the hub under topic "weather".

Values are keyed by the location string exactly as the user typed it, since
that is how the clock program looks them up. A failed refresh keeps (and
re-publishes) the last good value; this service never raises out of `run`.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone

import httpx

from shelf_matrix.server.hub import Hub

logger = logging.getLogger(__name__)

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
TIMEOUT_S = 10.0


class WeatherError(Exception):
    """The weather provider failed or answered with something unusable."""


def _parse_latlon(location: str) -> tuple[float, float] | None:
    parts = location.split(",")
    if len(parts) != 2:
        return None
    try:
        return float(parts[0]), float(parts[1])
    except ValueError:
        return None


async def _get_json(http: httpx.AsyncClient, url: str, params: dict):
    try:
        resp = await http.get(url, params=params, timeout=TIMEOUT_S)
        resp.raise_for_status()
        return resp.json()
    except (httpx.HTTPError, ValueError) as e:
        raise WeatherError(f"{url}: {e}") from e


async def _geocode(http: httpx.AsyncClient, city: str,
                   cache: dict[str, tuple[float, float] | None] | None) -> tuple[float, float] | None:
    if cache is not None and city in cache:
        return cache[city]
    data = await _get_json(http, GEOCODE_URL, {"name": city, "count": 1})
    try:
        results = data.get("results")
        coords = None
        if results:
            coords = (float(results[0]["latitude"]), float(results[0]["longitude"]))
    except (AttributeError, KeyError, IndexError, TypeError, ValueError) as e:
        raise WeatherError(f"malformed geocoding response: {e}") from e
    if cache is not None:
        cache[city] = coords
    return coords


async def fetch_weather(http: httpx.AsyncClient, location: str,
                        geo_cache: dict[str, tuple[float, float] | None] | None = None) -> dict | None:
    """Current weather for "lat,lon" or a city name; None if the city is unknown."""
    coords = _parse_latlon(location)
    if coords is None:
        coords = await _geocode(http, location.strip(), geo_cache)
        if coords is None:
            return None
    data = await _get_json(http, FORECAST_URL, {
        "latitude": coords[0],
        "longitude": coords[1],
        "current": "temperature_2m,weather_code,is_day",
        "daily": "temperature_2m_max,temperature_2m_min",
        "timezone": "auto",
        "forecast_days": 1,
    })
    try:
        cur = data["current"]
        return {
            "location": location,
            "temp_c": float(cur["temperature_2m"]),
            "code": int(cur["weather_code"]),
            "is_day": bool(cur["is_day"]),
            "min_c": float(data["daily"]["temperature_2m_min"][0]),
            "max_c": float(data["daily"]["temperature_2m_max"][0]),
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }
    except (KeyError, IndexError, TypeError, ValueError) as e:
        raise WeatherError(f"malformed forecast response: {e!r}") from e


class WeatherService:
    def __init__(self, hub: Hub, http: httpx.AsyncClient, interval_s: float = 1200) -> None:
        self._hub = hub
        self._http = http
        self._interval_s = interval_s
        self._geo_cache: dict[str, tuple[float, float] | None] = {}
        self._last: dict[str, dict] = {}
        self._fresh_at: dict[str, float] = {}  # monotonic time of last attempt
        hub.on_params_changed.append(self._on_params_changed)

    def locations(self) -> set[str]:
        state = self._hub.state_payload()
        out = set()
        for m in state["catalog"]:
            if "weather" not in m.get("needs", ()):
                continue
            loc = state["params"].get(m["id"], {}).get("location")
            if isinstance(loc, str) and loc.strip():
                out.add(loc)
        return out

    async def refresh(self, location: str) -> None:
        self._fresh_at[location] = time.monotonic()
        try:
            value = await fetch_weather(self._http, location, self._geo_cache)
        except WeatherError as e:
            logger.warning("weather fetch for %r failed: %s", location, e)
            value = self._last.get(location)
        else:
            if value is None:
                logger.warning("no weather location found for %r", location)
                return
            self._last[location] = value
        if value is not None:
            await self._hub.publish_data("weather", location, value)

    async def _refresh_safely(self, location: str) -> None:
        try:
            await self.refresh(location)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("weather refresh for %r failed", location)

    async def _on_params_changed(self) -> None:
        """Fetch only locations not attempted within the interval."""
        now = time.monotonic()
        for loc in self.locations():
            if now - self._fresh_at.get(loc, -self._interval_s - 1) >= self._interval_s:
                await self._refresh_safely(loc)

    async def run(self) -> None:
        while True:
            try:
                for loc in self.locations():
                    await self._refresh_safely(loc)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("weather loop iteration failed")
            await asyncio.sleep(self._interval_s)
