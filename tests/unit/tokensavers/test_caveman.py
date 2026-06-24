from janus.canonical.models import CanonicalRequest, Message, Role, SystemBlock
from janus.tokensavers.caveman import CavemanSaver


def test_caveman_prepends_system():
    req = CanonicalRequest(model="m", messages=[Message(role=Role.USER, content="hi")])
    saver = CavemanSaver()
    result = saver.transform(req)
    assert len(result.system) >= 1
    assert result.system[0].text  # non-empty prompt prepended


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
