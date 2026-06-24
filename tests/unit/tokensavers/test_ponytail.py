import pytest

from janus.canonical.models import CanonicalRequest, Message, Role, SystemBlock
from janus.tokensavers.ponytail import PonytailSaver


def test_ponytail_lite_prepends_system():
    req = CanonicalRequest(model="m", messages=[Message(role=Role.USER, content="hi")])
    saver = PonytailSaver(level="lite")
    result = saver.transform(req)
    assert len(result.system) == 1
    assert "lazy" in result.system[0].text.lower() or "stdlib" in result.system[0].text.lower()


def test_ponytail_full_level():
    req = CanonicalRequest(model="m", messages=[Message(role=Role.USER, content="hi")])
    saver = PonytailSaver(level="full")
    result = saver.transform(req)
    assert len(result.system) == 1


def test_ponytail_ultra_level():
    req = CanonicalRequest(model="m", messages=[Message(role=Role.USER, content="hi")])
    saver = PonytailSaver(level="ultra")
    result = saver.transform(req)
    assert len(result.system) == 1
    assert "yagni" in result.system[0].text.lower()


def test_ponytail_invalid_level_raises():
    with pytest.raises(ValueError, match="level"):
        PonytailSaver(level="invalid")


def test_ponytail_preserves_existing_system():
    req = CanonicalRequest(
        model="m",
        system=[SystemBlock(type="text", text="existing prompt")],
        messages=[Message(role=Role.USER, content="hi")],
    )
    saver = PonytailSaver(level="full")
    result = saver.transform(req)
    assert len(result.system) == 2
    assert result.system[1].text == "existing prompt"
