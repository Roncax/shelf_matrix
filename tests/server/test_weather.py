import asyncio
import json

import httpx
import pytest

from shelf_matrix.server.hub import Hub
from shelf_matrix.server.store import Store
from shelf_matrix.server.weather import WeatherError, WeatherService, fetch_weather

GEO = {"results": [{"latitude": 45.46, "longitude": 9.19}]}
FORECAST = {
    "current": {"temperature_2m": 12.3, "weather_code": 3, "is_day": 0},
    "daily": {"temperature_2m_max": [15], "temperature_2m_min": [8.5]},
}


def make_http(handler):
    calls = []

    def wrapped(request):
        calls.append(request)
        return handler(request)

    return httpx.AsyncClient(transport=httpx.MockTransport(wrapped)), calls


def ok_handler(request):
    if "geocoding" in request.url.host:
        return httpx.Response(200, json=GEO)
    return httpx.Response(200, json=FORECAST)


async def test_fetch_by_city():
    http, calls = make_http(ok_handler)
    w = await fetch_weather(http, "Milan")
    assert w["temp_c"] == 12.3 and w["code"] == 3 and w["is_day"] is False
    assert w["min_c"] == 8.5 and w["max_c"] == 15.0
    assert w["location"] == "Milan" and w["fetched_at"]
    assert calls[0].url.params["name"] == "Milan"
    q = calls[1].url.params
    assert q["latitude"] == "45.46" and q["longitude"] == "9.19"
    assert q["current"] == "temperature_2m,weather_code,is_day"


async def test_fetch_by_latlon_skips_geocoding():
    http, calls = make_http(ok_handler)
    w = await fetch_weather(http, "45.46, 9.19")
    assert w["location"] == "45.46, 9.19"
    assert len(calls) == 1 and "api.open-meteo.com" in calls[0].url.host


async def test_unknown_city_yields_no_data():
    http, _ = make_http(lambda r: httpx.Response(200, json={}))
    assert await fetch_weather(http, "Nowhereville") is None


async def test_geocode_cache():
    http, calls = make_http(ok_handler)
    cache = {}
    await fetch_weather(http, "Milan", cache)
    await fetch_weather(http, "Milan", cache)
    assert sum("geocoding" in c.url.host for c in calls) == 1


@pytest.mark.parametrize("handler", [
    lambda r: httpx.Response(500),
    lambda r: httpx.Response(200, text="not json"),
    lambda r: httpx.Response(200, json={"current": {}}),
    lambda r: httpx.Response(200, json={"current": {"temperature_2m": "x", "weather_code": 1,
                                                    "is_day": 1},
                                        "daily": {"temperature_2m_max": [1], "temperature_2m_min": [0]}}),
    lambda r: httpx.Response(200, json=[1]),
])
async def test_errors_become_weather_error(handler):
    http, _ = make_http(handler)
    with pytest.raises(WeatherError):
        await fetch_weather(http, "1,2")


async def test_network_error_becomes_weather_error():
    def boom(request):
        raise httpx.ConnectError("down")

    http, _ = make_http(boom)
    with pytest.raises(WeatherError):
        await fetch_weather(http, "1,2")


CLOCK = {
    "id": "clock", "name": "Clock", "kind": "python", "entry": "program:Clock",
    "needs": ["weather"],
    "params": [{"name": "location", "label": "Location", "type": "text", "default": "", "max_len": 80}],
}
OTHER = {
    "id": "other", "name": "Other", "kind": "python", "entry": "program:Other",
    "params": [{"name": "location", "label": "Location", "type": "text", "default": "", "max_len": 80}],
}


def make_hub(tmp_path, params):
    store = Store(tmp_path / "store.json")
    store.catalog = [CLOCK, OTHER]
    store.params = params
    hub = Hub(store)
    published = []

    async def publish(topic, key, value):
        published.append((topic, key, value))

    hub.publish_data = publish
    return hub, published


def test_locations_from_params(tmp_path):
    hub, _ = make_hub(tmp_path, {"clock": {"location": "Milan"}, "other": {"location": "Rome"}})
    http, _ = make_http(ok_handler)
    assert WeatherService(hub, http).locations() == {"Milan"}
    hub, _ = make_hub(tmp_path, {})
    assert WeatherService(hub, http).locations() == set()


async def test_refresh_publishes_under_typed_key(tmp_path):
    hub, published = make_hub(tmp_path, {})
    http, _ = make_http(ok_handler)
    await WeatherService(hub, http).refresh("Milan")
    assert published[0][:2] == ("weather", "Milan")
    assert published[0][2]["temp_c"] == 12.3


async def test_fetch_failure_keeps_last_value(tmp_path):
    hub, published = make_hub(tmp_path, {})
    state = {"fail": False}

    def handler(request):
        return httpx.Response(500) if state["fail"] else ok_handler(request)

    http, _ = make_http(handler)
    svc = WeatherService(hub, http)
    await svc.refresh("1,2")
    state["fail"] = True
    await svc.refresh("1,2")
    assert len(published) == 2 and published[1] == published[0]


async def test_failure_without_last_value_publishes_nothing(tmp_path):
    hub, published = make_hub(tmp_path, {})
    http, _ = make_http(lambda r: httpx.Response(500))
    await WeatherService(hub, http).refresh("1,2")
    assert published == []


async def test_unknown_city_publishes_nothing(tmp_path):
    hub, published = make_hub(tmp_path, {})
    http, _ = make_http(lambda r: httpx.Response(200, json={}))
    await WeatherService(hub, http).refresh("Nowhere")
    assert published == []


async def test_params_change_only_fetches_stale_locations(tmp_path):
    hub, published = make_hub(tmp_path, {"clock": {"location": "1,2"}})
    http, calls = make_http(ok_handler)
    svc = WeatherService(hub, http)
    await hub._notify_params_changed()
    await svc._task
    await hub._notify_params_changed()  # unrelated param change: no refetch
    await svc._task
    assert len(calls) == 1 and len(published) == 1
    hub.store.params = {"clock": {"location": "3,4"}}
    await hub._notify_params_changed()
    await svc._task
    assert len(calls) == 2


async def test_run_loops_and_survives_errors(tmp_path):
    hub, published = make_hub(tmp_path, {"clock": {"location": "1,2"}})
    n = {"i": 0}

    def handler(request):
        n["i"] += 1
        if n["i"] == 1:
            return httpx.Response(500)
        return ok_handler(request)

    http, _ = make_http(handler)
    svc = WeatherService(hub, http, interval_s=0.01)
    task = asyncio.create_task(svc.run())
    for _ in range(200):
        if published:
            break
        await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert published


@pytest.mark.parametrize("loc", ["nan,1", "inf,1", "91,0", "0,181"])
async def test_invalid_coordinates_fall_through_to_geocoding(loc):
    http, calls = make_http(ok_handler)
    await fetch_weather(http, loc)
    assert "geocoding" in calls[0].url.host


async def test_city_with_country_code_retries_with_city_only():
    def handler(request):
        if "geocoding" in request.url.host:
            ok = request.url.params["name"] == "Paris"
            return httpx.Response(200, json=GEO if ok else {})
        return httpx.Response(200, json=FORECAST)

    http, _ = make_http(handler)
    w = await fetch_weather(http, "Paris, FR")
    assert w is not None and w["location"] == "Paris, FR"


async def test_negative_geocode_cache_expires(monkeypatch):
    from shelf_matrix.server import weather

    http, calls = make_http(lambda r: httpx.Response(200, json={}))
    cache = {}
    assert await fetch_weather(http, "Nowhere", cache) is None
    assert await fetch_weather(http, "Nowhere", cache) is None
    assert len(calls) == 1
    real = weather.time.monotonic
    monkeypatch.setattr(weather.time, "monotonic", lambda: real() + weather.NEGATIVE_TTL_S + 1)
    await fetch_weather(http, "Nowhere", cache)
    assert len(calls) == 2


async def test_params_callback_does_not_block_and_close_cancels(tmp_path):
    hub, published = make_hub(tmp_path, {"clock": {"location": "1,2"}})
    gate = asyncio.Event()

    class Slow(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            await gate.wait()
            return httpx.Response(200, json=FORECAST)

    svc = WeatherService(hub, httpx.AsyncClient(transport=Slow()))
    await asyncio.wait_for(hub._notify_params_changed(), 1)
    assert published == []
    gate.set()
    await svc._task
    assert len(published) == 1

    gate.clear()
    svc2 = WeatherService(hub, httpx.AsyncClient(transport=Slow()))
    await svc2._on_params_changed()
    task = svc2._task
    await asyncio.sleep(0)
    await svc2.close()
    assert task.cancelled() or task.done()
