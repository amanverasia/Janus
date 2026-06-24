from janus.formats.base import StreamEmitter, StreamParser


def test_protocols_importable():
    assert StreamParser is not None
    assert StreamEmitter is not None
