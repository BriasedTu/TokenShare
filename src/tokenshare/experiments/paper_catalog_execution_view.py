"""正式论文计划与执行共享的冻结 catalog execution view。"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from tokenshare.experiments.paper_catalog import PaperInputCatalogManifest
from tokenshare.experiments.paper_models import JsonObject, digest_json


SCHEMA_VERSION = "tokenshare.paper_catalog_execution_view.v1"
MANIFEST_VIEW_KIND = "manifest_with_lean_readiness"
EXP3_VIEW_KIND = "exp3_prepared_catalog"
EXP4_VIEW_KIND = "exp4_prepared_catalog"
_VIEW_KINDS = frozenset(
    {MANIFEST_VIEW_KIND, EXP3_VIEW_KIND, EXP4_VIEW_KIND}
)


@dataclass(frozen=True)
class PaperCatalogExecutionView(Mapping[str, Any]):
    """带稳定 body/digest、又保留 manifest 查询能力的只读 execution view。"""

    view_kind: str
    catalog_manifest_digest: str
    view_body: JsonObject
    view_digest: str
    manifest: PaperInputCatalogManifest | None = None
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(f"schema_version must be {SCHEMA_VERSION}")
        if self.view_kind not in _VIEW_KINDS:
            raise ValueError("unsupported paper catalog execution view kind")
        if not self.catalog_manifest_digest:
            raise ValueError("catalog_manifest_digest is required")
        normalized = _json_copy(self.view_body)
        expected = _view_digest(
            view_kind=self.view_kind,
            catalog_manifest_digest=self.catalog_manifest_digest,
            view_body=normalized,
        )
        if self.view_digest != expected:
            raise ValueError("paper catalog execution view digest mismatch")
        if normalized.get("catalog_digest") != self.catalog_manifest_digest:
            raise ValueError("paper catalog execution view catalog digest mismatch")
        if self.view_kind == MANIFEST_VIEW_KIND:
            if self.manifest is None:
                raise ValueError("manifest-backed execution view requires manifest")
            if self.manifest.catalog_digest != self.catalog_manifest_digest:
                raise ValueError("execution view manifest digest mismatch")
        object.__setattr__(self, "view_body", normalized)

    def __getitem__(self, key: str) -> Any:
        if self.manifest is not None:
            if key == "factorization_cases":
                return self.factorization_cases
            if key == "lean_cases":
                return self.manifest.lean_cases
            if key == "lean_lemma_graph_cases":
                return self.manifest.lean_lemma_graph_cases
        return self.view_body[key]

    def __iter__(self) -> Iterator[str]:
        keys = list(self.view_body)
        if self.manifest is not None:
            keys.extend(
                key
                for key in (
                    "factorization_cases",
                    "lean_cases",
                    "lean_lemma_graph_cases",
                )
                if key not in self.view_body
            )
        return iter(keys)

    def __len__(self) -> int:
        return sum(1 for _key in self)

    @property
    def catalog_id(self) -> str:
        return str(self.view_body.get("catalog_id") or "")

    @property
    def catalog_version(self) -> str:
        return str(self.view_body.get("catalog_version") or "")

    @property
    def catalog_digest(self) -> str:
        return self.catalog_manifest_digest

    @property
    def factorization_cases(self) -> tuple[JsonObject, ...]:
        manifest = self._require_manifest()
        selected_ids = self.view_body.get("factorization_case_ids")
        if selected_ids is None:
            return manifest.factorization_cases
        ordered_ids = tuple(str(case_id) for case_id in selected_ids)
        if len(set(ordered_ids)) != len(ordered_ids):
            raise ValueError("execution view Factorization selection has duplicates")
        cases_by_id = {
            str(case["case_id"]): case for case in manifest.factorization_cases
        }
        try:
            selected = tuple(cases_by_id[case_id] for case_id in ordered_ids)
        except KeyError as exc:
            raise ValueError("execution view Factorization selection references unknown case")
        return selected

    @property
    def lean_cases(self) -> tuple[JsonObject, ...]:
        return self._require_manifest().lean_cases

    @property
    def lean_lemma_graph_cases(self) -> tuple[JsonObject, ...]:
        return self._require_manifest().lean_lemma_graph_cases

    @property
    def task15_budget_input(self) -> JsonObject | None:
        value = self.view_body.get("task15_budget_input")
        return _json_copy(value) if isinstance(value, Mapping) else None

    @property
    def lean_task14_readiness(self) -> JsonObject | None:
        value = self.view_body.get("lean_task14_readiness")
        return _json_copy(value) if isinstance(value, Mapping) else None

    @property
    def task14_readiness(self) -> JsonObject | None:
        return self.lean_task14_readiness

    @property
    def lean_semantic_readiness_passed(self) -> bool:
        return self.view_body.get("lean_semantic_readiness_passed") is not False

    @property
    def optional_worker_preflight(self) -> JsonObject | None:
        value = self.view_body.get("optional_worker_preflight")
        return _json_copy(value) if isinstance(value, Mapping) else None

    def cases_for(self, **kwargs: Any) -> tuple[JsonObject, ...]:
        cases = self._require_manifest().cases_for(**kwargs)
        if kwargs.get("domain") != "factorization":
            return cases
        matching = {str(case["case_id"]) for case in cases}
        return tuple(
            case
            for case in self.factorization_cases
            if str(case["case_id"]) in matching
        )

    def to_dict(self) -> JsonObject:
        return {
            "schema_version": self.schema_version,
            "view_kind": self.view_kind,
            "catalog_manifest_digest": self.catalog_manifest_digest,
            "view_body": _json_copy(self.view_body),
            "view_digest": self.view_digest,
        }

    def _require_manifest(self) -> PaperInputCatalogManifest:
        if self.manifest is None:
            raise ValueError("prepared mapping execution view has no manifest query API")
        return self.manifest


def build_manifest_execution_view(
    *,
    manifest: PaperInputCatalogManifest,
    task15_budget_input: Mapping[str, Any],
    lean_task14_readiness: Mapping[str, Any],
    optional_worker_preflight: Mapping[str, Any] | None,
    factorization_case_ids: Sequence[str] | None = None,
    paper_suite_scale_policy: Mapping[str, Any] | None = None,
) -> PaperCatalogExecutionView:
    body: JsonObject = {
        "catalog_id": manifest.catalog_id,
        "catalog_version": manifest.catalog_version,
        "catalog_digest": manifest.catalog_digest,
        "suite_version": "paper_v1",
        "lean_semantic_readiness_passed": True,
        "task15_budget_input": _json_copy(task15_budget_input),
        "lean_task14_readiness": _json_copy(lean_task14_readiness),
        "task14_readiness": _json_copy(lean_task14_readiness),
        "optional_worker_preflight": _json_copy(
            optional_worker_preflight or {}
        ),
    }
    if factorization_case_ids is not None:
        body["factorization_case_ids"] = [str(case_id) for case_id in factorization_case_ids]
    if paper_suite_scale_policy is not None:
        body["paper_suite_scale_policy"] = _json_copy(paper_suite_scale_policy)
    return _build(
        view_kind=MANIFEST_VIEW_KIND,
        catalog_manifest_digest=manifest.catalog_digest,
        view_body=body,
        manifest=manifest,
    )


def build_prepared_mapping_execution_view(
    *,
    view_kind: str,
    catalog_manifest_digest: str,
    view_body: Mapping[str, Any],
) -> PaperCatalogExecutionView:
    if view_kind not in {EXP3_VIEW_KIND, EXP4_VIEW_KIND}:
        raise ValueError("prepared mapping view kind must be Exp3 or Exp4")
    return _build(
        view_kind=view_kind,
        catalog_manifest_digest=catalog_manifest_digest,
        view_body=_json_copy(view_body),
        manifest=None,
    )


def restore_catalog_execution_view(
    frozen: Mapping[str, Any],
    *,
    catalog_manifest: PaperInputCatalogManifest,
) -> PaperCatalogExecutionView:
    body = frozen.get("view_body")
    if not isinstance(body, Mapping):
        raise ValueError("frozen catalog execution view body is required")
    view_kind = str(frozen.get("view_kind") or "")
    manifest = catalog_manifest if view_kind == MANIFEST_VIEW_KIND else None
    return PaperCatalogExecutionView(
        schema_version=str(frozen.get("schema_version") or ""),
        view_kind=view_kind,
        catalog_manifest_digest=str(
            frozen.get("catalog_manifest_digest") or ""
        ),
        view_body=_json_copy(body),
        view_digest=str(frozen.get("view_digest") or ""),
        manifest=manifest,
    )


def freeze_catalog_execution_view(value: Any) -> JsonObject | None:
    if isinstance(value, PaperCatalogExecutionView):
        return value.to_dict()
    return None


def _build(
    *,
    view_kind: str,
    catalog_manifest_digest: str,
    view_body: JsonObject,
    manifest: PaperInputCatalogManifest | None,
) -> PaperCatalogExecutionView:
    return PaperCatalogExecutionView(
        view_kind=view_kind,
        catalog_manifest_digest=catalog_manifest_digest,
        view_body=view_body,
        view_digest=_view_digest(
            view_kind=view_kind,
            catalog_manifest_digest=catalog_manifest_digest,
            view_body=view_body,
        ),
        manifest=manifest,
    )


def _view_digest(
    *,
    view_kind: str,
    catalog_manifest_digest: str,
    view_body: Mapping[str, Any],
) -> str:
    return digest_json(
        {
            "schema_version": SCHEMA_VERSION,
            "view_kind": view_kind,
            "catalog_manifest_digest": catalog_manifest_digest,
            "view_body": _json_copy(view_body),
        }
    )


def _json_copy(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, sort_keys=True))
