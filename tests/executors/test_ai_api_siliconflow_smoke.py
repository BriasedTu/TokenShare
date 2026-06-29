import json
import os
from dataclasses import replace
from pathlib import Path

import pytest

from tests.phase7_fixtures import make_ai_request, make_config_dict
from tokenshare.executors.ai_api import AIAPIExecutor
from tokenshare.executors.ai_api_config import load_ai_api_config
from tokenshare.executors.ai_api_local_config import (
    DEFAULT_LOCAL_AI_API_CONFIG_PATH,
    load_local_ai_api_config,
)
from tokenshare.executors.ai_api_transport import UrlLibSiliconFlowTransport
from tokenshare.storage.artifacts import ArtifactStore


@pytest.mark.skipif(
    os.environ.get("TOKENSHARE_RUN_SILICONFLOW_SMOKE") != "1",
    reason="real SiliconFlow smoke test is opt-in",
)
def test_real_siliconflow_smoke_returns_artifact_backed_submission(tmp_path) -> None:
    config_path = Path(
        os.environ.get("TOKENSHARE_AI_API_CONFIG", str(DEFAULT_LOCAL_AI_API_CONFIG_PATH))
    )
    if config_path.exists():
        config = load_local_ai_api_config(config_path)
    else:
        if not os.environ.get("SILICONFLOW_API_KEY"):
            pytest.skip(
                "local/ai_api_smoke.local.json or SILICONFLOW_API_KEY is not configured"
            )
        body = make_config_dict()
        body["entries"] = [body["entries"][0]]
        body["entries"][0]["api_key_env"] = "SILICONFLOW_API_KEY"
        body["defaults"]["max_tokens"] = 32
        config = load_ai_api_config(body)
    store = ArtifactStore(tmp_path)
    request = _make_raw_real_smoke_request(store)
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

    assert submission.result_kind == "succeeded", submission.error
    assert submission.raw_output_ref is not None
    assert submission.provenance_ref is not None
    assert submission.usage_summary["cost_estimate_status"] == "estimated"
    assert submission.usage_summary["total_tokens"] > 0
    assert submission.usage_summary["provider_attempt_count"] >= 1
    raw_output = json.loads(store.read_bytes(submission.raw_output_ref).decode("utf-8"))
    assert raw_output["usage"]["total_tokens"] > 0
    provenance = json.loads(store.read_bytes(submission.provenance_ref).decode("utf-8"))
    assert provenance["final_result_kind"] == "succeeded"
    assert provenance["final_entry_id"]
    assert any(
        attempt["result_kind"] == "succeeded" and attempt["http_status"] == 200
        for attempt in provenance["attempts"]
    )
    assert store.verify(submission.provenance_ref)


def test_real_smoke_request_does_not_require_json_mode(tmp_path) -> None:
    store = ArtifactStore(tmp_path)
    request = _make_raw_real_smoke_request(store)

    prompt_body = json.loads(store.read_bytes(request.prompt_package_ref).decode("utf-8"))

    assert prompt_body["constraints"]["requires_json_mode"] is False
    assert prompt_body["constraints"]["format"] == "text"


def _make_raw_real_smoke_request(store: ArtifactStore):
    request = make_ai_request(store, request_id="request_real_smoke")
    prompt_body = json.loads(store.read_bytes(request.prompt_package_ref).decode("utf-8"))
    prompt_body["prompt_package_id"] = "prompt_request_real_smoke_raw"
    prompt_body["prompt_text"] = "Reply with one short sentence confirming the TokenShare smoke request."
    prompt_body["output_schema"] = {}
    prompt_body["constraints"] = {"format": "text", "requires_json_mode": False}
    prompt_ref = store.save_json(
        prompt_body,
        artifact_id=prompt_body["prompt_package_id"],
        artifact_type="PromptPackage",
        artifact_schema_id="phase3.prompt_package",
        artifact_schema_version="v1",
        source={"kind": "phase7_real_smoke"},
        metadata={"mode": "raw_text"},
        created_at=str(prompt_body["created_at"]),
    )
    return replace(request, prompt_package_ref=prompt_ref, limits={**request.limits, "max_tokens": 64})
