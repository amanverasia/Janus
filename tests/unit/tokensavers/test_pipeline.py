from janus.canonical.models import CanonicalRequest, Message, Role, SystemBlock
from janus.tokensavers.pipeline import SaverPipeline


def test_empty_pipeline_noop():
    req = CanonicalRequest(model="m", messages=[Message(role=Role.USER, content="hi")])
    pipeline = SaverPipeline([])
    result = pipeline.apply(req)
    assert result is req


def test_pipeline_runs_savers_in_order():
    order: list[str] = []

    class SaverA:
        def transform(self, req: CanonicalRequest) -> CanonicalRequest:
            order.append("a")
            return req

    class SaverB:
        def transform(self, req: CanonicalRequest) -> CanonicalRequest:
            order.append("b")
            return req

    req = CanonicalRequest(model="m", messages=[Message(role=Role.USER, content="hi")])
    pipeline = SaverPipeline([SaverA(), SaverB()])
    pipeline.apply(req)
    assert order == ["a", "b"]


def test_pipeline_saver_exception_doesnt_break():
    class BadSaver:
        def transform(self, req: CanonicalRequest) -> CanonicalRequest:
            raise RuntimeError("boom")

    class GoodSaver:
        def transform(self, req: CanonicalRequest) -> CanonicalRequest:
            req.system.append(SystemBlock(type="text", text="ok"))
            return req

    req = CanonicalRequest(model="m", messages=[Message(role=Role.USER, content="hi")])
    pipeline = SaverPipeline([BadSaver(), GoodSaver()])
    result = pipeline.apply(req)
    assert len(result.system) == 1
    assert result.system[0].text == "ok"
