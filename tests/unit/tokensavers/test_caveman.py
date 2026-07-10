import pytest

from janus.canonical.models import CanonicalRequest, Message, Role, SystemBlock
from janus.tokensavers.caveman import CavemanSaver


def test_caveman_lite_prepends_system():
    req = CanonicalRequest(model="m", messages=[Message(role=Role.USER, content="hi")])
    saver = CavemanSaver(level="lite")
    result = saver.transform(req)
    assert len(result.system) == 1
    assert result.system[0].text


def test_caveman_full_level():
    req = CanonicalRequest(model="m", messages=[Message(role=Role.USER, content="hi")])
    saver = CavemanSaver(level="full")
    result = saver.transform(req)
    assert len(result.system) == 1
    assert "security warnings" in result.system[0].text.lower()


def test_caveman_ultra_level():
    req = CanonicalRequest(model="m", messages=[Message(role=Role.USER, content="hi")])
    saver = CavemanSaver(level="ultra")
    result = saver.transform(req)
    assert len(result.system) == 1
    assert "security warnings" in result.system[0].text.lower()


def test_caveman_default_level_is_full():
    saver = CavemanSaver()
    assert saver.level == "full"


def test_caveman_invalid_level_raises():
    with pytest.raises(ValueError, match="level"):
        CavemanSaver(level="invalid")


def test_caveman_preserves_existing_system():
    req = CanonicalRequest(
        model="m",
        system=[SystemBlock(type="text", text="You are a coder.")],
        messages=[Message(role=Role.USER, content="hi")],
    )
    saver = CavemanSaver()
    result = saver.transform(req)
    assert len(result.system) == 2
    assert result.system[1].text == "You are a coder."
