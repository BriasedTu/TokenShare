import os

import pytest

from tests.phase7_fixtures import make_ai_request, make_config_dict
from tokenshare.executors.ai_api import AIAPIExecutor
from tokenshare.executors.ai_api_config import load_ai_api_config
from tokenshare.executors.ai_api_transport import UrlLibSiliconFlowTransport
from tokenshare.storage.artifacts import ArtifactStore


@pytest.mark.skipif(
    os.environ.get("TOKENSHARE_RUN_SILICONFLOW_SMOKE") != "1",
    reason="real SiliconFlow smoke test is opt-in",
)
def test_real_siliconflow_smoke_returns_artifact_backed_submission(tmp_path) -> None:
    if not os.environ.get("SILICONFLOW_API_KEY"):
        pytest.skip("SILICONFLOW_API_KEY is not set")
    body = make_config_dict()
    body["entries"] = [body["entries"][0]]
    body["entries"][0]["api_key_env"] = "SILICONFLOW_API_KEY"
    body["defaults"]["max_tokens"] = 32
    store = ArtifactStore(tmp_path)
    request = make_ai_request(store, request_id="request_real_smoke")
    config = load_ai_api_config(body)
    executor = AIAPIExecutor(
        executor_id="executor_ai_api",
        executor_version="0.1.0",
        artifact_store=store,
        config=config,
        transport=UrlLibSiliconFlowTransport(),
        parser=None,
    )

    submission = executor.execute(
        request,
        submission_id="submission_real_smoke",
        submitted_at="2026-06-28T00:00:02Z",
    )

    assert submission.raw_output_ref is not None or submission.provenance_ref is not None
    assert submission.provenance_ref is not None
    assert store.verify(submission.provenance_ref)
