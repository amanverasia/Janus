import asyncio

import pytest

from janus.dashboard.live import (
    RING_CAP,
    SUBSCRIBER_QUEUE_CAP,
    LiveUsageBus,
    get_bus,
    reset_bus,
)


@pytest.fixture(autouse=True)
def _fresh_bus():
    reset_bus()
    yield
    reset_bus()


def test_get_bus_singleton():
    assert get_bus() is get_bus()
    reset_bus()
    assert get_bus() is not None


def test_begin_end_tracks_inflight():
    bus = LiveUsageBus()
    rid1 = bus.begin("/v1/chat/completions")
    rid2 = bus.begin("/v1/chat/completions")
    assert bus.snapshot()["inflight"] == 2
    bus.end(rid1)
    assert bus.snapshot()["inflight"] == 1
    bus.end(rid2)
    bus.end(rid2)  # double-end is a no-op
    assert bus.snapshot()["inflight"] == 0


def test_record_completed_ring_and_user_label():
    bus = LiveUsageBus()
    bus.record_completed(model="t/m1", client_key_label="alice", status=200, cost=0.5)
    bus.record_completed(model="t/m2", client_key_id=4, status=429)
    bus.record_completed(model="t/m3", status=200)
    recent = bus.snapshot()["recent"]
    assert [r["model"] for r in recent] == ["t/m1", "t/m2", "t/m3"]
    assert recent[0]["user"] == "alice"
    assert recent[1]["user"] == "key #4"
    assert recent[2]["user"] is None
    assert recent[0]["cost"] == 0.5


def test_ring_capped():
    bus = LiveUsageBus()
    for i in range(RING_CAP + 10):
        bus.record_completed(model=f"m{i}", status=200)
    recent = bus.snapshot()["recent"]
    assert len(recent) == RING_CAP
    assert recent[0]["model"] == "m10"


async def test_subscribers_receive_events():
    bus = LiveUsageBus()
    q = bus.subscribe()
    bus.record_completed(model="t/m1", status=200)
    event = await asyncio.wait_for(q.get(), timeout=1)
    assert event["type"] == "request"
    assert event["model"] == "t/m1"
    rid = bus.begin("/v1/x")
    event = await asyncio.wait_for(q.get(), timeout=1)
    assert event == {"type": "inflight", "count": 1}
    bus.end(rid)
    bus.unsubscribe(q)
    bus.record_completed(model="t/m2", status=200)
    # after unsubscribe only the pending inflight event remains
    assert q.qsize() == 1


def test_slow_subscriber_drops_events_without_error():
    bus = LiveUsageBus()
    q = bus.subscribe()
    for i in range(SUBSCRIBER_QUEUE_CAP + 20):
        bus.record_completed(model=f"m{i}", status=200)
    assert q.qsize() == SUBSCRIBER_QUEUE_CAP
