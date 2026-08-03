"""不可变真实响应 bank 的稳定对象、索引和进程本地解析能力。"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, replace
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence


ObjectRole = Literal[
    "request_body",
    "raw_output",
    "provider_failure",
    "provenance",
    "usage",
    "latency",
    "pricing",
    "acquisition_attempt",
    "model_record",
]
TerminalKind = Literal["success", "provider_failure"]

OBJECT_ROLES = (
    "request_body",
    "raw_output",
    "provider_failure",
    "provenance",
    "usage",
    "latency",
    "pricing",
    "acquisition_attempt",
    "model_record",
)
COMMON_ROLES = {
    "request_body",
    "provenance",
    "usage",
    "latency",
    "pricing",
    "acquisition_attempt",
    "model_record",
}


class ResponseBankBlockedError(RuntimeError):
    """表示 replay 因显式外部 bank capability 不完整而阻塞。"""


def canonical_digest(value: Any) -> str:
    return _digest_bytes(_canonical_bytes(value))


def semantic_slot_key(
    *,
    case_record_digest: str,
    planned_ai_unit_id: str,
    sample_slot_index: int,
    replacement_slot: int,
    provider_config_digest: str,
    prompt_profile_digest: str,
    prompt_admission_profile_digest: str,
    plugin_version: str,
) -> str:
    return canonical_digest(
        {
            "case_record_digest": case_record_digest,
            "planned_ai_unit_id": planned_ai_unit_id,
            "sample_slot_index": sample_slot_index,
            "replacement_slot": replacement_slot,
            "provider_config_digest": provider_config_digest,
            "prompt_profile_digest": prompt_profile_digest,
            "prompt_admission_profile_digest": prompt_admission_profile_digest,
            "plugin_version": plugin_version,
        }
    )


def inventory_entry_id(row: Mapping[str, Any] | "ResponseBankInventoryRow") -> str:
    body = row.to_dict() if isinstance(row, ResponseBankInventoryRow) else dict(row)
    if "inventory_entry_id" not in body:
        raise ValueError("inventory row missing inventory_entry_id")
    return canonical_digest(
        {key: value for key, value in body.items() if key != "inventory_entry_id"}
    )


def terminal_bank_entry_id(
    *,
    semantic_slot_key: str,
    inference_request_digest: str,
) -> str:
    """派生与 provider-config entry identity 分离的稳定 terminal bank ID。"""

    return canonical_digest(
        {
            "schema_version": "tokenshare.response_bank_terminal_entry_identity.v1",
            "semantic_slot_key": semantic_slot_key,
            "inference_request_digest": inference_request_digest,
        }
    )


def canonical_inventory_rows(
    rows: Sequence["ResponseBankInventoryRow"],
) -> tuple["ResponseBankInventoryRow", ...]:
    """所有 producer/validator 共用的 inventory_entry_id 排序。"""

    values = tuple(rows)
    if any(not isinstance(row, ResponseBankInventoryRow) for row in values):
        raise TypeError("inventory rows must contain ResponseBankInventoryRow values")
    return tuple(sorted(values, key=lambda row: row.inventory_entry_id))


def response_bank_inventory_digest(
    rows: Sequence["ResponseBankInventoryRow"],
) -> str:
    return canonical_digest(
        [row.to_dict() for row in canonical_inventory_rows(rows)]
    )


@dataclass(frozen=True, kw_only=True)
class ExternalBankObjectLocator:
    bank_root_id: str
    manifest_digest: str
    entry_id: str
    object_role: ObjectRole
    object_digest: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ExternalBankObjectLocator":
        _require_exact_fields(value, cls)
        return cls(**value)


@dataclass(frozen=True, kw_only=True)
class ResponseBankManifest:
    bank_root_id: str
    manifest_digest: str
    profile_digest: str
    budget_digest: str
    inventory_digest: str
    provider_config_digest: str
    entry_ids: tuple[str, ...]
    object_role_schema: tuple[str, ...]
    terminal_entry_count: int
    created_by_paid_receipt_digest: str
    root_binding_marker_digest: str

    @classmethod
    def create(
        cls,
        *,
        bank_root_id: str,
        profile_digest: str,
        budget_digest: str,
        inventory_digest: str,
        provider_config_digest: str,
        entry_ids: Sequence[str],
        object_role_schema: Sequence[str],
        terminal_entry_count: int,
        created_by_paid_receipt_digest: str,
    ) -> "ResponseBankManifest":
        canonical_entry_ids = tuple(sorted(entry_ids))
        if len(set(canonical_entry_ids)) != len(canonical_entry_ids):
            raise ValueError("manifest entry_ids must be unique")
        value = cls(
            bank_root_id=bank_root_id,
            manifest_digest="",
            profile_digest=profile_digest,
            budget_digest=budget_digest,
            inventory_digest=inventory_digest,
            provider_config_digest=provider_config_digest,
            entry_ids=canonical_entry_ids,
            object_role_schema=tuple(object_role_schema),
            terminal_entry_count=terminal_entry_count,
            created_by_paid_receipt_digest=created_by_paid_receipt_digest,
            root_binding_marker_digest="",
        )
        return replace(value, manifest_digest=_manifest_digest(value))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ResponseBankManifest":
        _require_exact_fields(value, cls)
        converted = dict(value)
        converted["entry_ids"] = tuple(converted["entry_ids"])
        converted["object_role_schema"] = tuple(converted["object_role_schema"])
        manifest = cls(**converted)
        if manifest.manifest_digest != _manifest_digest(manifest):
            raise ValueError("manifest digest mismatch")
        return manifest


@dataclass(frozen=True, kw_only=True)
class ResponseBankInventoryRow:
    inventory_entry_id: str
    semantic_slot_key: str
    case_record_digest: str
    planned_ai_unit_id: str
    sample_slot_index: int
    replacement_slot: int
    provider_config_digest: str
    prompt_profile_digest: str
    prompt_admission_profile_digest: str
    plugin_version: str
    entry_id: str
    body_digest: str
    inference_request_digest: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ResponseBankInventoryRow":
        _require_exact_fields(value, cls)
        row = cls(**value)
        if inventory_entry_id(row) != row.inventory_entry_id:
            raise ValueError("inventory_entry_id does not match canonical row")
        expected_slot = semantic_slot_key(
            case_record_digest=row.case_record_digest,
            planned_ai_unit_id=row.planned_ai_unit_id,
            sample_slot_index=row.sample_slot_index,
            replacement_slot=row.replacement_slot,
            provider_config_digest=row.provider_config_digest,
            prompt_profile_digest=row.prompt_profile_digest,
            prompt_admission_profile_digest=row.prompt_admission_profile_digest,
            plugin_version=row.plugin_version,
        )
        if row.semantic_slot_key != expected_slot:
            raise ValueError("semantic_slot_key does not match inventory row")
        return row


@dataclass(frozen=True, kw_only=True)
class ResponseBankEntry:
    inventory_digest: str
    inventory_entry_id: str
    semantic_slot_key: str
    inference_request_digest: str
    entry_id: str
    sample_slot_index: int
    replacement_slot: int
    terminal_kind: TerminalKind
    object_locators: tuple[ExternalBankObjectLocator, ...]
    acquisition_state_ref: str

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["object_locators"] = [item.to_dict() for item in self.object_locators]
        return value

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ResponseBankEntry":
        _require_exact_fields(value, cls)
        converted = dict(value)
        converted["object_locators"] = tuple(
            ExternalBankObjectLocator.from_dict(item)
            for item in converted["object_locators"]
        )
        return cls(**converted)


@dataclass(frozen=True, kw_only=True)
class CurrentTraceWrapper:
    current_run_id: str
    current_task_id: str
    current_unit_id: str
    current_attempt_id: str
    attempt_ordinal: int
    bank_root_id: str
    manifest_digest: str
    root_binding_marker_digest: str
    inference_request_digest: str
    entry_id: str
    locator_digests: dict[str, str]
    logical_started_at: str
    logical_finished_at: str
    source_latency_ms: int
    current_parse_ref: str | None
    current_verifier_ref: str | None
    current_checker_ref: str | None
    current_canonical_ref: str | None
    current_ledger_ref: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CurrentTraceWrapper":
        _require_exact_fields(value, cls)
        return cls(**value)


class ValidatedResponseBankIndex:
    """通过 manifest、inventory、entry 交叉校验后才暴露的只读索引。"""

    def __init__(
        self,
        manifest: ResponseBankManifest,
        inventory_rows: tuple[ResponseBankInventoryRow, ...],
        entries: tuple[ResponseBankEntry, ...],
    ) -> None:
        self.manifest = manifest
        self.inventory_rows = inventory_rows
        self.entries = entries
        self._entries_by_id = {entry.entry_id: entry for entry in entries}

    @classmethod
    def build(
        cls,
        manifest: ResponseBankManifest,
        inventory_rows: Sequence[ResponseBankInventoryRow],
        entries: Sequence[ResponseBankEntry],
    ) -> "ValidatedResponseBankIndex":
        rows = tuple(inventory_rows)
        bank_entries = tuple(entries)
        if manifest.manifest_digest != _manifest_digest(manifest):
            raise ValueError("manifest digest mismatch")
        if tuple(sorted(manifest.entry_ids)) != manifest.entry_ids:
            raise ValueError("manifest entry_ids must use canonical sort")
        if len(set(manifest.entry_ids)) != len(manifest.entry_ids):
            raise ValueError("manifest entry_ids must be unique")
        if manifest.object_role_schema != OBJECT_ROLES:
            raise ValueError("manifest object role schema mismatch")
        if manifest.terminal_entry_count != len(bank_entries):
            raise ValueError("manifest terminal entry count mismatch")

        canonical_rows = canonical_inventory_rows(rows)
        if response_bank_inventory_digest(canonical_rows) != manifest.inventory_digest:
            raise ValueError("inventory digest mismatch")
        row_by_id: dict[str, ResponseBankInventoryRow] = {}
        slot_requests: dict[str, tuple[str, str]] = {}
        for row in canonical_rows:
            validated = ResponseBankInventoryRow.from_dict(row.to_dict())
            if validated.inventory_entry_id in row_by_id:
                raise ValueError("duplicate inventory_entry_id")
            row_by_id[validated.inventory_entry_id] = validated
            request_identity = (validated.body_digest, validated.inference_request_digest)
            previous = slot_requests.setdefault(validated.semantic_slot_key, request_identity)
            if previous != request_identity:
                raise ValueError("semantic slot has conflicting request digest")

        entry_ids = tuple(sorted(entry.entry_id for entry in bank_entries))
        if len(set(entry_ids)) != len(entry_ids):
            raise ValueError("duplicate response bank entry_id")
        if entry_ids != manifest.entry_ids:
            raise ValueError("manifest entry_ids do not match entries")
        inventory_keys: set[tuple[str, str]] = set()
        for entry in bank_entries:
            inventory_key = (entry.inventory_digest, entry.inventory_entry_id)
            if inventory_key in inventory_keys:
                raise ValueError("duplicate response bank inventory key")
            inventory_keys.add(inventory_key)
            row = row_by_id.get(entry.inventory_entry_id)
            if row is None:
                raise ValueError("entry has no preregistered inventory row")
            _validate_entry(manifest, row, entry)
        return cls(manifest, canonical_rows, tuple(sorted(bank_entries, key=lambda item: item.entry_id)))

    def entry(self, entry_id: str) -> ResponseBankEntry:
        try:
            return self._entries_by_id[entry_id]
        except KeyError as exc:
            raise KeyError(f"response bank entry not found: {entry_id}") from exc


class ResponseBankResolver:
    """把外部 root path 保留在当前进程，并验证后解析 opaque locator。"""

    def __init__(self, root_path: Path, index: ValidatedResponseBankIndex) -> None:
        self.root_path = root_path
        self.index = index

    @classmethod
    def open(cls, root_path: str | Path) -> "ResponseBankResolver":
        root = Path(root_path).resolve()
        manifest_path = root / "manifest.v1.json"
        inventory_path = root / "inventory.v1.json"
        entries_path = root / "entries.v1.json"
        marker_path = root / "response_bank_root_marker.v1.json"
        try:
            manifest = ResponseBankManifest.from_dict(_read_json(manifest_path))
            inventory_body = _read_json(inventory_path)
            entries_body = _read_json(entries_path)
            marker = _read_json(marker_path)
        except FileNotFoundError as exc:
            raise ResponseBankBlockedError(f"missing response bank object: {exc.filename}") from exc
        if not isinstance(inventory_body, list) or not isinstance(entries_body, list):
            raise ValueError("response bank index documents must be arrays")
        _validate_root_marker(root, manifest, marker)
        rows = tuple(ResponseBankInventoryRow.from_dict(item) for item in inventory_body)
        entries = tuple(ResponseBankEntry.from_dict(item) for item in entries_body)
        return cls(root, ValidatedResponseBankIndex.build(manifest, rows, entries))

    def entry(self, entry_id: str) -> ResponseBankEntry:
        return self.index.entry(entry_id)

    def read_verified(
        self,
        locator: ExternalBankObjectLocator,
        *,
        chunk_size: int = 64 * 1024,
    ) -> bytes:
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        manifest = self.index.manifest
        if (
            locator.bank_root_id != manifest.bank_root_id
            or locator.manifest_digest != manifest.manifest_digest
        ):
            raise ResponseBankBlockedError("locator root binding does not match resolver")
        if locator.object_role not in manifest.object_role_schema:
            raise ValueError("locator object role is not in manifest schema")
        target = self.root_path / "objects" / _digest_hex(locator.object_digest)
        digest = sha256()
        chunks: list[bytes] = []
        try:
            with target.open("rb") as stream:
                while True:
                    chunk = stream.read(chunk_size)
                    if not chunk:
                        break
                    digest.update(chunk)
                    chunks.append(chunk)
        except FileNotFoundError as exc:
            raise ResponseBankBlockedError(
                f"missing response bank object: {locator.object_digest}"
            ) from exc
        if f"sha256:{digest.hexdigest()}" != locator.object_digest:
            raise ValueError("response bank object digest mismatch")
        return b"".join(chunks)


def initialize_response_bank(
    root_path: str | Path,
    *,
    manifest: ResponseBankManifest,
    inventory_rows: Sequence[ResponseBankInventoryRow],
    entries: Sequence[ResponseBankEntry],
    objects: Mapping[str, bytes],
) -> ResponseBankManifest:
    """以 create-new 方式写入一份不可变、可重放的最小 bank。"""

    root = Path(root_path).resolve()
    if root.exists():
        raise FileExistsError(f"response bank root already exists: {root}")
    root.mkdir(parents=True)
    (root / "objects").mkdir()
    marker = _root_marker(root, manifest.bank_root_id, manifest.manifest_digest)
    bound_manifest = replace(
        manifest,
        root_binding_marker_digest=marker["marker_digest"],
    )
    ValidatedResponseBankIndex.build(bound_manifest, inventory_rows, entries)
    _write_json(root / "manifest.v1.json", bound_manifest.to_dict())
    _write_json(
        root / "inventory.v1.json",
        [row.to_dict() for row in canonical_inventory_rows(inventory_rows)],
    )
    _write_json(
        root / "entries.v1.json",
        [entry.to_dict() for entry in sorted(entries, key=lambda item: item.entry_id)],
    )
    _write_json(root / "response_bank_root_marker.v1.json", marker)
    for object_digest, data in objects.items():
        if _digest_bytes(data) != object_digest:
            raise ValueError("response bank object digest mismatch before write")
        (root / "objects" / _digest_hex(object_digest)).write_bytes(data)
    # 重开一次确保磁盘内容而不是调用方对象通过所有 binding/index 检查。
    return ResponseBankResolver.open(root).index.manifest


def _validate_entry(
    manifest: ResponseBankManifest,
    row: ResponseBankInventoryRow,
    entry: ResponseBankEntry,
) -> None:
    if entry.terminal_kind not in {"success", "provider_failure"}:
        raise ValueError("invalid response bank terminal kind")
    if entry.inventory_digest != manifest.inventory_digest:
        raise ValueError("entry inventory digest mismatch")
    if (
        entry.semantic_slot_key != row.semantic_slot_key
        or entry.inference_request_digest != row.inference_request_digest
        or entry.sample_slot_index != row.sample_slot_index
        or entry.replacement_slot != row.replacement_slot
        or entry.entry_id != row.entry_id
    ):
        raise ValueError("entry does not match preregistered inventory row")
    roles = [locator.object_role for locator in entry.object_locators]
    role_set = set(roles)
    if len(roles) != len(role_set):
        raise ValueError("duplicate object role")
    if "raw_output" in role_set and "provider_failure" in role_set:
        raise ValueError("conflicting terminal object roles")
    expected = COMMON_ROLES | ({"raw_output"} if entry.terminal_kind == "success" else {"provider_failure"})
    missing = expected - role_set
    if missing:
        raise ValueError(f"missing object roles: {sorted(missing)}")
    unknown = role_set - expected
    if unknown:
        raise ValueError(f"unexpected object roles: {sorted(unknown)}")
    if entry.terminal_kind == "success" and "raw_output" not in role_set:
        raise ValueError("success entry requires raw_output")
    if entry.terminal_kind == "provider_failure" and "provider_failure" not in role_set:
        raise ValueError("provider failure entry requires provider_failure")
    for locator in entry.object_locators:
        if (
            locator.bank_root_id != manifest.bank_root_id
            or locator.manifest_digest != manifest.manifest_digest
            or locator.entry_id != entry.entry_id
        ):
            raise ValueError("entry locator binding mismatch")
    request_locator = next(item for item in entry.object_locators if item.object_role == "request_body")
    if request_locator.object_digest != row.body_digest:
        raise ValueError("entry request body digest does not match inventory row")


def _manifest_digest(manifest: ResponseBankManifest) -> str:
    # manifest 自身 digest 与依赖它的 marker digest 都不进入 preimage，避免自指。
    return canonical_digest(
        {
            key: value
            for key, value in manifest.to_dict().items()
            if key not in {"manifest_digest", "root_binding_marker_digest"}
        }
    )


def _root_marker(root: Path, bank_root_id: str, manifest_digest: str) -> dict[str, str]:
    preimage = {
        "bank_root_id": bank_root_id,
        "manifest_digest": manifest_digest,
        "root_identity_digest": canonical_digest(str(root)),
    }
    return {**preimage, "marker_digest": canonical_digest(preimage)}


def _validate_root_marker(
    root: Path,
    manifest: ResponseBankManifest,
    marker: Any,
) -> None:
    if not isinstance(marker, dict):
        raise ValueError("response bank root marker must be an object")
    expected = _root_marker(root, manifest.bank_root_id, manifest.manifest_digest)
    if marker != expected or manifest.root_binding_marker_digest != expected["marker_digest"]:
        raise ValueError("response bank root binding mismatch")


def _require_exact_fields(value: Mapping[str, Any], cls: type[Any]) -> None:
    expected = set(cls.__dataclass_fields__)
    actual = set(value)
    if actual != expected:
        raise ValueError(
            f"{cls.__name__} fields mismatch: missing={sorted(expected - actual)}, "
            f"unknown={sorted(actual - expected)}"
        )


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def _digest_bytes(value: bytes) -> str:
    return f"sha256:{sha256(value).hexdigest()}"


def _digest_hex(value: str) -> str:
    prefix = "sha256:"
    if not value.startswith(prefix) or len(value) != len(prefix) + 64:
        raise ValueError("response bank object digest must be sha256")
    return value.removeprefix(prefix)


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: Any) -> None:
    path.write_bytes(_canonical_bytes(value))
