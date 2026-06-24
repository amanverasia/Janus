from janus.streaming.sse import encode_done, encode_sse, parse_sse_lines


def test_encode_sse_json():
    result = encode_sse({"foo": "bar"})
    assert result == b'data: {"foo":"bar"}\n\n'


def test_encode_sse_multiline():
    result = encode_sse({"text": "line1\nline2"})
    assert b"line1" in result
    assert b"line2" in result


def test_encode_done():
    assert encode_done() == b"data: [DONE]\n\n"


def test_parse_sse_lines_single():
    raw = b'data: {"x":1}\n\n'
    lines = list(parse_sse_lines(raw))
    assert lines == ['{"x":1}']


def test_parse_sse_lines_multiple():
    raw = b'data: {"x":1}\n\ndata: {"y":2}\n\n'
    lines = list(parse_sse_lines(raw))
    assert lines == ['{"x":1}', '{"y":2}']


def test_parse_sse_lines_done():
    raw = b"data: [DONE]\n\n"
    lines = list(parse_sse_lines(raw))
    assert lines == ["[DONE]"]


def test_parse_sse_lines_empty():
    assert list(parse_sse_lines(b"")) == []
