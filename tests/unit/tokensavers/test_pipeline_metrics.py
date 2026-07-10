from janus.canonical.models import CanonicalRequest, Message, Role, SystemBlock, ToolResult
from janus.tokensavers.pipeline import SaverPipeline, SaverStats, request_size
from janus.tokensavers.rtk import RTKSaver


def _compressible_request() -> CanonicalRequest:
    long_diff = (
        "diff --git a/f.py b/f.py\nindex 111..222 100644\n--- a/f.py\n+++ b/f.py\n" + "line\n" * 200
    )
    return CanonicalRequest(
        model="m",
        messages=[
            Message(
                role=Role.TOOL,
                content=[ToolResult(type="tool_result", tool_use_id="t1", content=long_diff)],
            ),
            Message(role=Role.USER, content="fix it"),
        ],
    )


def test_request_size_counts_messages_and_system():
    req = CanonicalRequest(model="m", messages=[Message(role=Role.USER, content="hi")])
    size_empty_system = request_size(req)
    req.system.append(SystemBlock(type="text", text="be nice"))
    size_with_system = request_size(req)
    assert size_with_system > size_empty_system


def test_pipeline_records_stats_for_rtk_shrinking_request():
    req = _compressible_request()
    pipeline = SaverPipeline([RTKSaver()])
    pipeline.apply(req)

    stats = pipeline.stats["RTKSaver"]
    assert stats["requests"] == 1
    assert stats["bytes_after"] < stats["bytes_before"]


def test_pipeline_stats_unchanged_when_saver_raises():
    class BadSaver:
        def transform(self, req: CanonicalRequest) -> CanonicalRequest:
            raise RuntimeError("boom")

    req = CanonicalRequest(model="m", messages=[Message(role=Role.USER, content="hi")])
    pipeline = SaverPipeline([BadSaver()])
    pipeline.apply(req)

    assert "BadSaver" not in pipeline.stats


def test_pipeline_accumulates_stats_across_multiple_requests():
    pipeline = SaverPipeline([RTKSaver()])
    pipeline.apply(_compressible_request())
    pipeline.apply(_compressible_request())

    stats = pipeline.stats["RTKSaver"]
    assert stats["requests"] == 2


def test_adopt_stats_carries_counters_forward():
    old_pipeline = SaverPipeline([RTKSaver()])
    old_pipeline.apply(_compressible_request())

    new_pipeline = SaverPipeline([RTKSaver()])
    new_pipeline.adopt_stats(old_pipeline)

    assert new_pipeline.stats["RTKSaver"]["requests"] == 1
    assert (
        new_pipeline.stats["RTKSaver"]["bytes_before"]
        == old_pipeline.stats["RTKSaver"]["bytes_before"]
    )

    new_pipeline.apply(_compressible_request())
    assert new_pipeline.stats["RTKSaver"]["requests"] == 2
    # old pipeline's stats remain untouched by the new pipeline's activity
    assert old_pipeline.stats["RTKSaver"]["requests"] == 1


def test_saver_stats_dataclass_fields():
    s = SaverStats(name="RTKSaver", bytes_before=100, bytes_after=40)
    assert s.name == "RTKSaver"
    assert s.bytes_before == 100
    assert s.bytes_after == 40
