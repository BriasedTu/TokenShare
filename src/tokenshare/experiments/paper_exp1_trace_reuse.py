"""Experiment 1 attempt-history bank and zero-provider reuse helpers.

This module is deliberately narrow.  It establishes the immutable evidence seam
used by the paper Experiment 2--4 paths without teaching the generic executor
about paper-specific replay semantics.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field, replace
from decimal import Decimal, ROUND_DOWN
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence


_DIGEST_PREFIX = "sha256:"
_COST_QUANTUM = Decimal("0.000000000001")
_ALLOCATION_FORMULA = "largest_remainder_by_interval_overlap.v1"
_OUTCOMES = frozenset(
    {
        "success",
        "wrong_answer",
        "parse_failure",
        "checker_failure",
        "timeout",
        "missingness",
        "provider_failure",
        "executor_error",
        "unclassified_successful_transport",
    }
)


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: object) -> str:
    return _DIGEST_PREFIX + hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _bytes_digest(value: bytes) -> str:
    return _DIGEST_PREFIX + hashlib.sha256(value).hexdigest()


def _require_digest(name: str, value: str) -> None:
    if not isinstance(value, str) or not value.startswith(_DIGEST_PREFIX) or len(value) != 71:
        raise ValueError(f"{name} must be a sha256 digest")
    try:
        int(value[len(_DIGEST_PREFIX) :], 16)
    except ValueError as exc:
        raise ValueError(f"{name} must be a sha256 digest") from exc


def _require_exact_keys(body: Mapping[str, object], expected: set[str], name: str) -> None:
    if set(body) != expected:
        raise ValueError(f"{name} keys drifted")


def _source_manifest_authorization(manifest: object) -> dict[str, str]:
    """保留 source bank 的精确授权类型，绝不把 facility 授权伪装成 paid receipt。"""

    from tokenshare.executors.response_bank import (
        ResponseBankManifest,
        ResultsFirstResponseBankManifest,
    )

    if type(manifest) is ResponseBankManifest:
        digest = manifest.created_by_paid_receipt_digest
        _require_digest("source paid receipt digest", digest)
        return {"receipt_digest": digest}
    if type(manifest) is ResultsFirstResponseBankManifest:
        kind = manifest.authorization_kind
        digest = manifest.created_by_results_first_authorization_digest
        if not isinstance(kind, str) or not kind:
            raise ValueError("source results-first authorization kind is invalid")
        _require_digest("source results-first authorization digest", digest)
        return {
            "authorization_kind": kind,
            "authorization_digest": digest,
        }
    raise TypeError("exact response bank manifest is required")


def _strict_str(body: Mapping[str, object], key: str) -> str:
    value = body[key]
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} must be a non-empty string")
    return value


def _strict_int(body: Mapping[str, object], key: str) -> int:
    value = body[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{key} must be an integer")
    return value


def _strict_bool(body: Mapping[str, object], key: str) -> bool:
    value = body[key]
    if type(value) is not bool:
        raise ValueError(f"{key} must be a boolean")
    return value


def _strict_optional_bool(body: Mapping[str, object], key: str) -> bool | None:
    value = body[key]
    if value is not None and type(value) is not bool:
        raise ValueError(f"{key} must be a boolean or null")
    return value


def _strict_optional_str(body: Mapping[str, object], key: str) -> str | None:
    value = body[key]
    if value is not None and (not isinstance(value, str) or not value):
        raise ValueError(f"{key} must be a non-empty string or null")
    return value


def _strict_optional_int(body: Mapping[str, object], key: str) -> int | None:
    value = body[key]
    if value is not None and (type(value) is not int or value < 0):
        raise ValueError(f"{key} must be a non-negative integer or null")
    return value


def _strict_optional_decimal(body: Mapping[str, object], key: str) -> Decimal | None:
    value = body[key]
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a decimal string or null")
    return Decimal(value)


@dataclass(frozen=True)
class Exp1AttemptTrace:
    attempt_id: str
    attempt_index: int
    current_attempt_id: str
    current_attempt_ordinal: int
    source_entry_id: str
    source_sample_slot_index: int
    source_replacement_slot: int
    replacement_slot: int
    source_acquisition_attempt_id: str
    request_identity_digest: str
    request_artifact_ref: str
    request_artifact_digest: str
    response_artifact_ref: str
    response_artifact_digest: str
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    api_latency_ms: int | None
    cost_cny: Decimal | None
    outcome: str
    success: bool | None
    wrong_answer: bool | None
    parse_failure: bool | None
    checker_failure: bool | None
    timeout: bool | None
    missingness: bool | None
    terminal_selected: bool
    retry_reason: str | None
    ledger_ref: str
    ledger_record_digest: str
    provider_family: str
    model_id: str
    provider_config_digest: str
    protocol_current_attempt_id: str | None = None
    protocol_parser_ref: str | None = None
    protocol_verifier_checker_ref: str | None = None
    protocol_ledger_ref: str | None = None

    def __post_init__(self) -> None:
        for name in (
            "attempt_id",
            "current_attempt_id",
            "source_entry_id",
            "source_acquisition_attempt_id",
            "request_artifact_ref",
            "response_artifact_ref",
            "ledger_ref",
            "provider_family",
            "model_id",
        ):
            if not getattr(self, name):
                raise ValueError(f"{name} must be non-empty")
        for name in (
            "request_identity_digest",
            "request_artifact_digest",
            "response_artifact_digest",
            "ledger_record_digest",
            "provider_config_digest",
        ):
            _require_digest(name, getattr(self, name))
        if (
            self.attempt_index < 0
            or self.current_attempt_ordinal < 0
            or self.source_sample_slot_index < 0
            or self.source_replacement_slot < 0
            or self.replacement_slot < 0
        ):
            raise ValueError("attempt ordinals/slots must be non-negative")
        if (
            self.current_attempt_id != self.attempt_id
            or self.current_attempt_ordinal != self.attempt_index
            or self.source_acquisition_attempt_id != self.attempt_id
            or self.source_replacement_slot != self.replacement_slot
        ):
            raise ValueError("current/source attempt identity aliases drifted")
        token_values = (self.input_tokens, self.output_tokens, self.total_tokens)
        if all(value is None for value in token_values):
            if self.cost_cny is not None:
                raise ValueError("missing token usage cannot have a derived cost")
        elif (
            any(type(value) is not int or value < 0 for value in token_values)
            or self.total_tokens != self.input_tokens + self.output_tokens
            or self.cost_cny is None
        ):
            raise ValueError("token usage/cost must be complete or entirely unknown")
        if self.api_latency_ms is not None and (
            type(self.api_latency_ms) is not int or self.api_latency_ms < 0
        ):
            raise ValueError("api latency must be a non-negative integer or null")
        if self.cost_cny is not None and (
            self.cost_cny < 0
            or self.cost_cny != self.cost_cny.quantize(_COST_QUANTUM)
        ):
            raise ValueError("cost_cny must be non-negative with at most 12 decimal places")
        if self.outcome not in _OUTCOMES:
            raise ValueError(f"unsupported attempt outcome: {self.outcome}")
        flags = (
            self.success,
            self.wrong_answer,
            self.parse_failure,
            self.checker_failure,
            self.timeout,
            self.missingness,
        )
        if any(value is not None and type(value) is not bool for value in flags):
            raise ValueError("attempt classifications must be booleans or null")
        for name in (
            "protocol_current_attempt_id",
            "protocol_parser_ref",
            "protocol_verifier_checker_ref",
            "protocol_ledger_ref",
        ):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value):
                raise ValueError(f"{name} must be a non-empty string or null")

    def to_dict(self) -> dict[str, object]:
        return {
            "attempt_id": self.attempt_id,
            "attempt_index": self.attempt_index,
            "current_attempt_id": self.current_attempt_id,
            "current_attempt_ordinal": self.current_attempt_ordinal,
            "source_entry_id": self.source_entry_id,
            "source_sample_slot_index": self.source_sample_slot_index,
            "source_replacement_slot": self.source_replacement_slot,
            "replacement_slot": self.replacement_slot,
            "source_acquisition_attempt_id": self.source_acquisition_attempt_id,
            "request_identity_digest": self.request_identity_digest,
            "request_artifact_ref": self.request_artifact_ref,
            "request_artifact_digest": self.request_artifact_digest,
            "response_artifact_ref": self.response_artifact_ref,
            "response_artifact_digest": self.response_artifact_digest,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "api_latency_ms": self.api_latency_ms,
            "cost_cny": None if self.cost_cny is None else format(self.cost_cny, "f"),
            "outcome": self.outcome,
            "success": self.success,
            "wrong_answer": self.wrong_answer,
            "parse_failure": self.parse_failure,
            "checker_failure": self.checker_failure,
            "timeout": self.timeout,
            "missingness": self.missingness,
            "terminal_selected": self.terminal_selected,
            "retry_reason": self.retry_reason,
            "ledger_ref": self.ledger_ref,
            "ledger_record_digest": self.ledger_record_digest,
            "provider_family": self.provider_family,
            "model_id": self.model_id,
            "provider_config_digest": self.provider_config_digest,
            "protocol_current_attempt_id": self.protocol_current_attempt_id,
            "protocol_parser_ref": self.protocol_parser_ref,
            "protocol_verifier_checker_ref": self.protocol_verifier_checker_ref,
            "protocol_ledger_ref": self.protocol_ledger_ref,
        }

    @classmethod
    def from_dict(cls, body: Mapping[str, object]) -> "Exp1AttemptTrace":
        expected = {
            "attempt_id",
            "attempt_index",
            "current_attempt_id",
            "current_attempt_ordinal",
            "source_entry_id",
            "source_sample_slot_index",
            "source_replacement_slot",
            "replacement_slot",
            "source_acquisition_attempt_id",
            "request_identity_digest",
            "request_artifact_ref",
            "request_artifact_digest",
            "response_artifact_ref",
            "response_artifact_digest",
            "input_tokens",
            "output_tokens",
            "total_tokens",
            "api_latency_ms",
            "cost_cny",
            "outcome",
            "success",
            "wrong_answer",
            "parse_failure",
            "checker_failure",
            "timeout",
            "missingness",
            "terminal_selected",
            "retry_reason",
            "ledger_ref",
            "ledger_record_digest",
            "provider_family",
            "model_id",
            "provider_config_digest",
            "protocol_current_attempt_id",
            "protocol_parser_ref",
            "protocol_verifier_checker_ref",
            "protocol_ledger_ref",
        }
        _require_exact_keys(body, expected, "attempt")
        try:
            return cls(
                attempt_id=_strict_str(body, "attempt_id"),
                attempt_index=_strict_int(body, "attempt_index"),
                current_attempt_id=_strict_str(body, "current_attempt_id"),
                current_attempt_ordinal=_strict_int(body, "current_attempt_ordinal"),
                source_entry_id=_strict_str(body, "source_entry_id"),
                source_sample_slot_index=_strict_int(body, "source_sample_slot_index"),
                source_replacement_slot=_strict_int(body, "source_replacement_slot"),
                replacement_slot=_strict_int(body, "replacement_slot"),
                source_acquisition_attempt_id=_strict_str(
                    body, "source_acquisition_attempt_id"
                ),
                request_identity_digest=_strict_str(body, "request_identity_digest"),
                request_artifact_ref=_strict_str(body, "request_artifact_ref"),
                request_artifact_digest=_strict_str(body, "request_artifact_digest"),
                response_artifact_ref=_strict_str(body, "response_artifact_ref"),
                response_artifact_digest=_strict_str(body, "response_artifact_digest"),
                input_tokens=_strict_optional_int(body, "input_tokens"),
                output_tokens=_strict_optional_int(body, "output_tokens"),
                total_tokens=_strict_optional_int(body, "total_tokens"),
                api_latency_ms=_strict_optional_int(body, "api_latency_ms"),
                cost_cny=_strict_optional_decimal(body, "cost_cny"),
                outcome=_strict_str(body, "outcome"),
                success=_strict_optional_bool(body, "success"),
                wrong_answer=_strict_optional_bool(body, "wrong_answer"),
                parse_failure=_strict_optional_bool(body, "parse_failure"),
                checker_failure=_strict_optional_bool(body, "checker_failure"),
                timeout=_strict_optional_bool(body, "timeout"),
                missingness=_strict_optional_bool(body, "missingness"),
                terminal_selected=_strict_bool(body, "terminal_selected"),
                retry_reason=_strict_optional_str(body, "retry_reason"),
                ledger_ref=_strict_str(body, "ledger_ref"),
                ledger_record_digest=_strict_str(body, "ledger_record_digest"),
                provider_family=_strict_str(body, "provider_family"),
                model_id=_strict_str(body, "model_id"),
                provider_config_digest=_strict_str(body, "provider_config_digest"),
                protocol_current_attempt_id=_strict_optional_str(
                    body, "protocol_current_attempt_id"
                ),
                protocol_parser_ref=_strict_optional_str(
                    body, "protocol_parser_ref"
                ),
                protocol_verifier_checker_ref=_strict_optional_str(
                    body, "protocol_verifier_checker_ref"
                ),
                protocol_ledger_ref=_strict_optional_str(
                    body, "protocol_ledger_ref"
                ),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("invalid attempt trace") from exc


@dataclass(frozen=True)
class Exp1NodeTrace:
    question_id: str
    node_id: str
    interval_start: int | None
    interval_end: int | None
    attempts: tuple[Exp1AttemptTrace, ...]
    node_digest: str

    def __post_init__(self) -> None:
        if not self.question_id or not self.node_id:
            raise ValueError("question_id and node_id must be non-empty")
        if (self.interval_start is None) != (self.interval_end is None):
            raise ValueError("node interval must be complete or null")
        if self.interval_start is not None and (
            isinstance(self.interval_start, bool)
            or isinstance(self.interval_end, bool)
            or not isinstance(self.interval_start, int)
            or not isinstance(self.interval_end, int)
            or self.interval_start > self.interval_end
        ):
            raise ValueError("node interval must be non-empty")
        if not self.attempts:
            raise ValueError("a node trace must contain at least one attempt")
        if tuple(attempt.attempt_index for attempt in self.attempts) != tuple(
            range(len(self.attempts))
        ):
            raise ValueError("attempt history order/index drifted")
        if len({attempt.attempt_id for attempt in self.attempts}) != len(self.attempts):
            raise ValueError("attempt_id must be unique within a node")
        selected = [index for index, attempt in enumerate(self.attempts) if attempt.terminal_selected]
        if selected != [len(self.attempts) - 1]:
            raise ValueError("exactly the final attempt must be terminal-selected")
        _require_digest("node_digest", self.node_digest)
        if self.node_digest != _digest(self._body()):
            raise ValueError("node digest drifted")

    def _body(self) -> dict[str, object]:
        return {
            "question_id": self.question_id,
            "node_id": self.node_id,
            "interval_start": self.interval_start,
            "interval_end": self.interval_end,
            "attempts": [attempt.to_dict() for attempt in self.attempts],
        }

    @classmethod
    def create(
        cls,
        *,
        question_id: str,
        node_id: str,
        interval_start: int | None,
        interval_end: int | None,
        attempts: Sequence[Exp1AttemptTrace],
    ) -> "Exp1NodeTrace":
        attempts_tuple = tuple(attempts)
        body = {
            "question_id": question_id,
            "node_id": node_id,
            "interval_start": interval_start,
            "interval_end": interval_end,
            "attempts": [attempt.to_dict() for attempt in attempts_tuple],
        }
        return cls(
            question_id=question_id,
            node_id=node_id,
            interval_start=interval_start,
            interval_end=interval_end,
            attempts=attempts_tuple,
            node_digest=_digest(body),
        )

    @property
    def source_attempt_count(self) -> int:
        return len(self.attempts)

    @property
    def source_tokens_known_total(self) -> int:
        return sum(
            attempt.total_tokens
            for attempt in self.attempts
            if attempt.total_tokens is not None
        )

    @property
    def source_tokens_missing_attempt_count(self) -> int:
        return sum(attempt.total_tokens is None for attempt in self.attempts)

    @property
    def source_tokens_total(self) -> int | None:
        if self.source_tokens_missing_attempt_count:
            return None
        return self.source_tokens_known_total

    @property
    def source_api_latency_known_total_ms(self) -> int:
        return sum(
            attempt.api_latency_ms
            for attempt in self.attempts
            if attempt.api_latency_ms is not None
        )

    @property
    def source_api_latency_missing_attempt_count(self) -> int:
        return sum(attempt.api_latency_ms is None for attempt in self.attempts)

    @property
    def source_api_latency_total_ms(self) -> int | None:
        if self.source_api_latency_missing_attempt_count:
            return None
        return self.source_api_latency_known_total_ms

    @property
    def source_cost_known_total_cny(self) -> Decimal:
        return sum(
            (
                attempt.cost_cny
                for attempt in self.attempts
                if attempt.cost_cny is not None
            ),
            Decimal(),
        )

    @property
    def source_cost_missing_attempt_count(self) -> int:
        return sum(attempt.cost_cny is None for attempt in self.attempts)

    @property
    def source_cost_total_cny(self) -> Decimal | None:
        if self.source_cost_missing_attempt_count:
            return None
        return self.source_cost_known_total_cny

    @property
    def terminal_attempt(self) -> Exp1AttemptTrace:
        return self.attempts[-1]

    @property
    def terminal_outcome(self) -> str:
        return self.terminal_attempt.outcome

    @property
    def terminal_response_artifact_digest(self) -> str:
        return self.terminal_attempt.response_artifact_digest

    def to_dict(self) -> dict[str, object]:
        return {**self._body(), "node_digest": self.node_digest}

    @classmethod
    def from_dict(cls, body: Mapping[str, object]) -> "Exp1NodeTrace":
        expected = {
            "question_id",
            "node_id",
            "interval_start",
            "interval_end",
            "attempts",
            "node_digest",
        }
        _require_exact_keys(body, expected, "node")
        attempts_body = body["attempts"]
        if not isinstance(attempts_body, list) or any(
            not isinstance(item, Mapping) for item in attempts_body
        ):
            raise ValueError("node attempts must be a list")
        return cls(
            question_id=_strict_str(body, "question_id"),
            node_id=_strict_str(body, "node_id"),
            interval_start=(
                None
                if body["interval_start"] is None
                else _strict_int(body, "interval_start")
            ),
            interval_end=(
                None
                if body["interval_end"] is None
                else _strict_int(body, "interval_end")
            ),
            attempts=tuple(
                Exp1AttemptTrace.from_dict(item)
                for item in attempts_body
                if isinstance(item, Mapping)
            ),
            node_digest=_strict_str(body, "node_digest"),
        )


@dataclass(frozen=True)
class Exp1QuestionTrace:
    question_id: str
    case_record_digest: str
    sample_slot_index: int
    nodes: tuple[Exp1NodeTrace, ...]
    question_digest: str

    def __post_init__(self) -> None:
        if not self.question_id or not self.nodes:
            raise ValueError("question trace must have an id and nodes")
        if (
            isinstance(self.sample_slot_index, bool)
            or not isinstance(self.sample_slot_index, int)
            or self.sample_slot_index < 0
        ):
            raise ValueError("question sample slot must be non-negative")
        _require_digest("case_record_digest", self.case_record_digest)
        _require_digest("question_digest", self.question_digest)
        if any(node.question_id != self.question_id for node in self.nodes):
            raise ValueError("node question identity drifted")
        if len({node.node_id for node in self.nodes}) != len(self.nodes):
            raise ValueError("node_id must be unique within a question")
        def node_key(item: Exp1NodeTrace) -> tuple[bool, int, int, str]:
            return (
                item.interval_start is None,
                -1 if item.interval_start is None else item.interval_start,
                -1 if item.interval_end is None else item.interval_end,
                item.node_id,
            )

        ordered = tuple(sorted(self.nodes, key=node_key))
        if ordered != self.nodes:
            raise ValueError("question nodes are not in canonical interval order")
        interval_nodes = tuple(
            node for node in self.nodes if node.interval_start is not None
        )
        for left, right in zip(interval_nodes, interval_nodes[1:]):
            assert left.interval_end is not None and right.interval_start is not None
            if left.interval_end >= right.interval_start:
                raise ValueError("source node intervals overlap")
        if self.question_digest != _digest(self._body()):
            raise ValueError("question digest drifted")

    def _body(self) -> dict[str, object]:
        return {
            "question_id": self.question_id,
            "case_record_digest": self.case_record_digest,
            "sample_slot_index": self.sample_slot_index,
            "nodes": [node.to_dict() for node in self.nodes],
        }

    @classmethod
    def create(
        cls,
        *,
        question_id: str,
        case_record_digest: str,
        nodes: Sequence[Exp1NodeTrace],
        sample_slot_index: int = 0,
    ) -> "Exp1QuestionTrace":
        nodes_tuple = tuple(
            sorted(
                nodes,
                key=lambda item: (
                    item.interval_start is None,
                    -1 if item.interval_start is None else item.interval_start,
                    -1 if item.interval_end is None else item.interval_end,
                    item.node_id,
                ),
            )
        )
        body = {
            "question_id": question_id,
            "case_record_digest": case_record_digest,
            "sample_slot_index": sample_slot_index,
            "nodes": [node.to_dict() for node in nodes_tuple],
        }
        return cls(
            question_id=question_id,
            case_record_digest=case_record_digest,
            sample_slot_index=sample_slot_index,
            nodes=nodes_tuple,
            question_digest=_digest(body),
        )

    def to_dict(self) -> dict[str, object]:
        return {**self._body(), "question_digest": self.question_digest}

    @classmethod
    def from_dict(cls, body: Mapping[str, object]) -> "Exp1QuestionTrace":
        expected = {
            "question_id",
            "case_record_digest",
            "sample_slot_index",
            "nodes",
            "question_digest",
        }
        _require_exact_keys(body, expected, "question")
        nodes_body = body["nodes"]
        if not isinstance(nodes_body, list) or any(
            not isinstance(item, Mapping) for item in nodes_body
        ):
            raise ValueError("question nodes must be a list")
        return cls(
            question_id=_strict_str(body, "question_id"),
            case_record_digest=_strict_str(body, "case_record_digest"),
            sample_slot_index=_strict_int(body, "sample_slot_index"),
            nodes=tuple(
                Exp1NodeTrace.from_dict(item)
                for item in nodes_body
                if isinstance(item, Mapping)
            ),
            question_digest=_strict_str(body, "question_digest"),
        )


@dataclass(frozen=True)
class Exp1TraceBank:
    questions: tuple[Exp1QuestionTrace, ...]
    bank_digest: str
    schema_version: str = "tokenshare.paper.exp1-attempt-history-bank.v1"

    def __post_init__(self) -> None:
        if not self.questions:
            raise ValueError("trace bank must contain at least one question")
        if tuple(
            sorted(
                self.questions,
                key=lambda item: (item.question_id, item.sample_slot_index),
            )
        ) != self.questions:
            raise ValueError("questions are not in canonical order")
        if len(
            {
                (question.question_id, question.sample_slot_index)
                for question in self.questions
            }
        ) != len(self.questions):
            raise ValueError("question/sample identity must be unique")
        attempt_ids = [
            attempt.attempt_id
            for question in self.questions
            for node in question.nodes
            for attempt in node.attempts
        ]
        if len(set(attempt_ids)) != len(attempt_ids):
            raise ValueError("attempt_id must be globally unique")
        _require_digest("bank_digest", self.bank_digest)
        if self.bank_digest != _digest(self._body()):
            raise ValueError("bank digest drifted")

    def _body(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "questions": [question.to_dict() for question in self.questions],
        }

    @classmethod
    def create(cls, *, questions: Sequence[Exp1QuestionTrace]) -> "Exp1TraceBank":
        questions_tuple = tuple(
            sorted(questions, key=lambda item: (item.question_id, item.sample_slot_index))
        )
        body = {
            "schema_version": "tokenshare.paper.exp1-attempt-history-bank.v1",
            "questions": [question.to_dict() for question in questions_tuple],
        }
        return cls(questions=questions_tuple, bank_digest=_digest(body))

    @property
    def attempts(self) -> tuple[Exp1AttemptTrace, ...]:
        return tuple(
            attempt
            for question in self.questions
            for node in question.nodes
            for attempt in node.attempts
        )

    def question(
        self, question_id: str, *, sample_slot_index: int = 0
    ) -> Exp1QuestionTrace:
        matches = [
            question
            for question in self.questions
            if question.question_id == question_id
            and question.sample_slot_index == sample_slot_index
        ]
        if len(matches) != 1:
            raise ValueError(f"question trace not found: {question_id}")
        return matches[0]

    def to_dict(self) -> dict[str, object]:
        return {**self._body(), "bank_digest": self.bank_digest}

    @classmethod
    def from_dict(cls, body: Mapping[str, object]) -> "Exp1TraceBank":
        expected = {"schema_version", "questions", "bank_digest"}
        _require_exact_keys(body, expected, "trace bank")
        if body["schema_version"] != "tokenshare.paper.exp1-attempt-history-bank.v1":
            raise ValueError("unsupported trace-bank schema")
        questions_body = body["questions"]
        if not isinstance(questions_body, list) or any(
            not isinstance(item, Mapping) for item in questions_body
        ):
            raise ValueError("trace-bank questions must be a list")
        return cls(
            questions=tuple(
                Exp1QuestionTrace.from_dict(item)
                for item in questions_body
                if isinstance(item, Mapping)
            ),
            bank_digest=_strict_str(body, "bank_digest"),
        )


_EXP1_ATTEMPT_HISTORY_NAME = "exp1_attempt_history_authority.v1.json"
_EXP1_PROTOCOL_CLASSIFIED_ATTEMPT_HISTORY_NAME = (
    "exp1_protocol_classified_attempt_history_authority.v1.json"
)


@dataclass(frozen=True)
class Exp1AttemptHistoryAuthority:
    source_bank_root_id: str
    source_manifest_digest: str
    source_inventory_digest: str
    source_inventory_entry_ids: tuple[str, ...]
    trace_bank: Exp1TraceBank
    global_paid_summary: Mapping[str, object]
    authority_digest: str
    schema_version: str = "tokenshare.paper.exp1-attempt-history-authority.v1"

    def __post_init__(self) -> None:
        if not self.source_bank_root_id or not self.source_inventory_entry_ids:
            raise ValueError("attempt history source identity is missing")
        _require_digest("source_manifest_digest", self.source_manifest_digest)
        _require_digest("source_inventory_digest", self.source_inventory_digest)
        _require_digest("authority_digest", self.authority_digest)
        if (
            len(set(self.source_inventory_entry_ids))
            != len(self.source_inventory_entry_ids)
        ):
            raise ValueError("attempt history source entries are not unique")
        expected_summary = {
            "source_provider_attempt_count",
            "source_tokens_total",
            "source_tokens_known_total",
            "source_tokens_missing_attempt_count",
            "source_api_latency_total_ms",
            "source_api_latency_known_total_ms",
            "source_api_latency_missing_attempt_count",
            "source_cost_total_cny",
            "source_cost_known_total_cny",
            "source_cost_missing_attempt_count",
            "current_provider_calls",
        }
        _require_exact_keys(
            self.global_paid_summary,
            expected_summary,
            "global paid summary",
        )
        count = self.global_paid_summary["source_provider_attempt_count"]
        tokens = self.global_paid_summary["source_tokens_total"]
        known_tokens = self.global_paid_summary["source_tokens_known_total"]
        missing_tokens = self.global_paid_summary[
            "source_tokens_missing_attempt_count"
        ]
        latency = self.global_paid_summary["source_api_latency_total_ms"]
        known_latency = self.global_paid_summary[
            "source_api_latency_known_total_ms"
        ]
        missing_latency = self.global_paid_summary[
            "source_api_latency_missing_attempt_count"
        ]
        current = self.global_paid_summary["current_provider_calls"]
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in (
                count,
                known_tokens,
                missing_tokens,
                known_latency,
                missing_latency,
                current,
            )
        ):
            raise ValueError("global paid summary counts must be non-negative integers")
        if current != 0 or count != len(self.trace_bank.attempts):
            raise ValueError("global paid provider accounting drifted")
        known_token_values = tuple(
            attempt.total_tokens
            for attempt in self.trace_bank.attempts
            if attempt.total_tokens is not None
        )
        if (
            known_tokens != sum(known_token_values)
            or missing_tokens != count - len(known_token_values)
            or tokens != (known_tokens if missing_tokens == 0 else None)
        ):
            raise ValueError("global paid token accounting drifted")
        known_latency_values = tuple(
            attempt.api_latency_ms
            for attempt in self.trace_bank.attempts
            if attempt.api_latency_ms is not None
        )
        if (
            known_latency != sum(known_latency_values)
            or missing_latency != count - len(known_latency_values)
            or latency != (known_latency if missing_latency == 0 else None)
        ):
            raise ValueError("global paid latency accounting drifted")
        cost_text = self.global_paid_summary["source_cost_total_cny"]
        known_cost_text = self.global_paid_summary["source_cost_known_total_cny"]
        missing_cost = self.global_paid_summary["source_cost_missing_attempt_count"]
        known_costs = tuple(
            attempt.cost_cny
            for attempt in self.trace_bank.attempts
            if attempt.cost_cny is not None
        )
        if (
            not isinstance(known_cost_text, str)
            or type(missing_cost) is not int
            or missing_cost < 0
            or Decimal(known_cost_text) != sum(known_costs, Decimal())
            or missing_cost != count - len(known_costs)
            or cost_text != (known_cost_text if missing_cost == 0 else None)
        ):
            raise ValueError("global paid cost accounting drifted")
        object.__setattr__(self, "global_paid_summary", dict(self.global_paid_summary))
        if self.authority_digest != _digest(self._body()):
            raise ValueError("attempt history authority digest drifted")

    def _body(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "source_bank_root_id": self.source_bank_root_id,
            "source_manifest_digest": self.source_manifest_digest,
            "source_inventory_digest": self.source_inventory_digest,
            "source_inventory_entry_ids": list(self.source_inventory_entry_ids),
            "trace_bank": self.trace_bank.to_dict(),
            "global_paid_summary": dict(self.global_paid_summary),
        }

    @classmethod
    def create(
        cls,
        *,
        source_bank_root_id: str,
        source_manifest_digest: str,
        source_inventory_digest: str,
        source_inventory_entry_ids: Sequence[str],
        trace_bank: Exp1TraceBank,
    ) -> "Exp1AttemptHistoryAuthority":
        known_tokens = tuple(
            attempt.total_tokens
            for attempt in trace_bank.attempts
            if attempt.total_tokens is not None
        )
        known_latency = tuple(
            attempt.api_latency_ms
            for attempt in trace_bank.attempts
            if attempt.api_latency_ms is not None
        )
        known_costs = tuple(
            attempt.cost_cny
            for attempt in trace_bank.attempts
            if attempt.cost_cny is not None
        )
        token_missing = len(trace_bank.attempts) - len(known_tokens)
        latency_missing = len(trace_bank.attempts) - len(known_latency)
        cost_missing = len(trace_bank.attempts) - len(known_costs)
        known_cost_text = format(
            sum(known_costs, Decimal()).quantize(_COST_QUANTUM), "f"
        )
        summary = {
            "source_provider_attempt_count": len(trace_bank.attempts),
            "source_tokens_total": None if token_missing else sum(known_tokens),
            "source_tokens_known_total": sum(known_tokens),
            "source_tokens_missing_attempt_count": token_missing,
            "source_api_latency_total_ms": (
                None if latency_missing else sum(known_latency)
            ),
            "source_api_latency_known_total_ms": sum(known_latency),
            "source_api_latency_missing_attempt_count": latency_missing,
            "source_cost_total_cny": None if cost_missing else known_cost_text,
            "source_cost_known_total_cny": known_cost_text,
            "source_cost_missing_attempt_count": cost_missing,
            "current_provider_calls": 0,
        }
        values = {
            "schema_version": "tokenshare.paper.exp1-attempt-history-authority.v1",
            "source_bank_root_id": source_bank_root_id,
            "source_manifest_digest": source_manifest_digest,
            "source_inventory_digest": source_inventory_digest,
            "source_inventory_entry_ids": list(source_inventory_entry_ids),
            "trace_bank": trace_bank.to_dict(),
            "global_paid_summary": summary,
        }
        return cls(
            source_bank_root_id=source_bank_root_id,
            source_manifest_digest=source_manifest_digest,
            source_inventory_digest=source_inventory_digest,
            source_inventory_entry_ids=tuple(source_inventory_entry_ids),
            trace_bank=trace_bank,
            global_paid_summary=summary,
            authority_digest=_digest(values),
        )

    def to_dict(self) -> dict[str, object]:
        return {**self._body(), "authority_digest": self.authority_digest}

    @classmethod
    def from_dict(cls, body: Mapping[str, object]) -> "Exp1AttemptHistoryAuthority":
        expected = {
            "schema_version",
            "source_bank_root_id",
            "source_manifest_digest",
            "source_inventory_digest",
            "source_inventory_entry_ids",
            "trace_bank",
            "global_paid_summary",
            "authority_digest",
        }
        _require_exact_keys(body, expected, "attempt history authority")
        if body["schema_version"] != "tokenshare.paper.exp1-attempt-history-authority.v1":
            raise ValueError("unsupported attempt history authority schema")
        entry_ids = body["source_inventory_entry_ids"]
        trace_bank = body["trace_bank"]
        summary = body["global_paid_summary"]
        if (
            not isinstance(entry_ids, list)
            or any(not isinstance(item, str) or not item for item in entry_ids)
            or not isinstance(trace_bank, Mapping)
            or not isinstance(summary, Mapping)
        ):
            raise ValueError("attempt history authority body type drifted")
        return cls(
            source_bank_root_id=_strict_str(body, "source_bank_root_id"),
            source_manifest_digest=_strict_str(body, "source_manifest_digest"),
            source_inventory_digest=_strict_str(body, "source_inventory_digest"),
            source_inventory_entry_ids=tuple(entry_ids),
            trace_bank=Exp1TraceBank.from_dict(trace_bank),
            global_paid_summary=dict(summary),
            authority_digest=_strict_str(body, "authority_digest"),
        )


def _ledger_record_body(record: object) -> dict[str, object]:
    fields = (
        "inventory_digest",
        "inventory_entry_id",
        "semantic_slot_key",
        "state",
        "terminal_ref",
        "terminal_kind",
        "charged_tokens",
        "cost_estimate",
        "usage_missing",
        "revision",
    )
    body: dict[str, object] = {}
    for name in fields:
        if not hasattr(record, name):
            raise ValueError(f"ledger record is missing {name}")
        value = getattr(record, name)
        body[name] = format(value, "f") if isinstance(value, Decimal) else value
    return body


def build_exp1_attempt_history_authority(
    *,
    resolver: Any,
    source_exp1_inventory_plan: Any,
    budget_ledger: Any,
) -> Exp1AttemptHistoryAuthority:
    """Build the immutable question->node->ordered-attempt authority."""

    from tokenshare.executors.response_bank import ResponseBankResolver
    from tokenshare.executors.trace_backed import (
        _validate_response_bank_evidence_role_bundle,
    )
    from tokenshare.experiments.paper_response_bank import SemanticInventoryPlan
    from tokenshare.experiments.paper_response_bank import (
        load_ordered_existing_settled_attempts,
    )

    if type(resolver) is not ResponseBankResolver:
        raise TypeError("attempt history requires an exact response bank resolver")
    if type(source_exp1_inventory_plan) is not SemanticInventoryPlan:
        raise TypeError("attempt history requires the typed Exp1 inventory plan")
    manifest = resolver.index.manifest
    plan = source_exp1_inventory_plan
    if (
        plan.inventory_digest != manifest.inventory_digest
        or {row.inventory_entry_id for row in plan.rows}
        != {row.inventory_entry_id for row in resolver.index.inventory_rows}
        or any(
            ref.get("experiment_id") != "exp1_real_ai_feasibility"
            for ref in plan.condition_refs
        )
    ):
        raise ValueError("attempt history Exp1 inventory identity drifted")
    reacquisitions = tuple(budget_ledger.list_reacquisitions())
    if reacquisitions:
        raise ValueError("ambiguous/reacquired provider attempts require manual closure")
    reservations = tuple(budget_ledger.list_reservations())
    ledger_by_entry = {
        str(record.inventory_entry_id): record for record in reservations
    }
    if len(ledger_by_entry) != len(reservations):
        raise ValueError("attempt history ledger entry identity drifted")
    slot_case: dict[str, tuple[str, str]] = {}
    for condition_ref in plan.condition_refs:
        case_refs = condition_ref.get("case_refs")
        if not isinstance(case_refs, list) or not case_refs:
            raise ValueError("attempt history case references are missing")
        for case_ref in case_refs:
            if not isinstance(case_ref, Mapping):
                raise ValueError("attempt history case reference type drifted")
            case_id = case_ref.get("case_id")
            case_digest = case_ref.get("case_record_digest")
            slots = case_ref.get("semantic_slot_keys")
            if (
                not isinstance(case_id, str)
                or not case_id
                or not isinstance(case_digest, str)
                or not isinstance(slots, list)
                or not slots
            ):
                raise ValueError("attempt history case reference identity is incomplete")
            _require_digest("case_record_digest", case_digest)
            for slot in slots:
                if not isinstance(slot, str) or not slot:
                    raise ValueError("attempt history semantic slot is invalid")
                previous = slot_case.setdefault(slot, (case_id, case_digest))
                if previous != (case_id, case_digest):
                    raise ValueError("attempt history semantic slot case drifted")
    entry_by_inventory = {
        entry.inventory_entry_id: entry for entry in resolver.index.entries
    }
    grouped: dict[tuple[str, str, int, str], list[tuple[Any, Any]]] = {}
    for row in plan.rows:
        case = slot_case.get(row.semantic_slot_key)
        entry = entry_by_inventory.get(row.inventory_entry_id)
        ledger_record = ledger_by_entry.get(row.inventory_entry_id)
        if (
            case is None
            or entry is None
            or ledger_record is None
            or case[1] != row.case_record_digest
        ):
            raise ValueError("attempt history source row evidence is incomplete")
        ledger_body = _ledger_record_body(ledger_record)
        if (
            ledger_body["inventory_digest"] != plan.inventory_digest
            or ledger_body["semantic_slot_key"] != row.semantic_slot_key
            or ledger_body["state"] != "settled"
            or ledger_body["terminal_kind"] != entry.terminal_kind
        ):
            raise ValueError("attempt history ledger terminal identity drifted")
        grouped.setdefault(
            (case[0], case[1], row.sample_slot_index, row.planned_ai_unit_id),
            [],
        ).append((row, entry))
    if set(ledger_by_entry) != {row.inventory_entry_id for row in plan.rows}:
        raise ValueError("attempt history ledger coverage drifted")

    nodes_by_question: dict[tuple[str, str, int], list[Exp1NodeTrace]] = {}
    for (case_id, case_digest, sample_slot, unit_id), pairs in grouped.items():
        ordered = tuple(sorted(pairs, key=lambda item: item[0].replacement_slot))
        if tuple(row.replacement_slot for row, _ in ordered) != tuple(
            range(len(ordered))
        ):
            raise ValueError("attempt history source attempt order drifted")
        existing_attempts = load_ordered_existing_settled_attempts(
            resolver=resolver,
            budget_ledger=budget_ledger,
            case_record_digest=case_digest,
            planned_ai_unit_id=unit_id,
            sample_slot_index=sample_slot,
        )
        existing_by_inventory = {
            item.source_inventory_entry_id: item for item in existing_attempts
        }
        if (
            len(existing_by_inventory) != len(existing_attempts)
            or set(existing_by_inventory)
            != {row.inventory_entry_id for row, _entry in ordered}
        ):
            raise ValueError("ordered settled Exp1 attempt coverage drifted")
        attempts: list[Exp1AttemptTrace] = []
        interval: tuple[int, int] | None = None
        for attempt_index, (row, entry) in enumerate(ordered):
            settled = existing_by_inventory[row.inventory_entry_id]
            objects = _entry_objects(resolver, entry)
            parsed = {
                role: _json_object_bytes(data, role)
                for role, data in objects.items()
                if role != "request_body"
            }
            try:
                request_meta = _factor_request_metadata(objects["request_body"])
            except ValueError:
                request_meta = None
            current_interval = (
                None
                if request_meta is None
                else (request_meta["range_start"], request_meta["range_end"])
            )
            if interval is None:
                interval = current_interval
            elif current_interval != interval:
                raise ValueError("attempt history node request interval drifted")
            acquisition = parsed.get("acquisition_attempt")
            usage_body = parsed.get("usage")
            latency_body = parsed.get("latency")
            pricing = parsed.get("pricing")
            provenance = parsed.get("provenance")
            model = parsed.get("model_record")
            if not all(
                isinstance(item, Mapping)
                for item in (
                    acquisition,
                    usage_body,
                    latency_body,
                    pricing,
                    provenance,
                    model,
                )
            ):
                raise ValueError("attempt history evidence role is missing")
            _validate_response_bank_evidence_role_bundle(
                terminal_kind=entry.terminal_kind,
                roles=parsed,
            )
            attempt_id = acquisition.get("attempt_id")
            encoded_index = acquisition.get("attempt_index", row.replacement_slot)
            if (
                not isinstance(attempt_id, str)
                or not attempt_id
                or isinstance(encoded_index, bool)
                or not isinstance(encoded_index, int)
                or encoded_index != attempt_index
            ):
                raise ValueError("attempt history acquisition order drifted")
            if (
                settled.attempt_id != attempt_id
                or settled.attempt_index != attempt_index
                or settled.source_entry_id != entry.entry_id
                or settled.source_sample_slot_index != sample_slot
                or settled.source_replacement_slot != row.replacement_slot
                or settled.request_identity_digest != row.inference_request_digest
            ):
                raise ValueError("ordered settled Exp1 attempt identity drifted")
            usage = usage_body.get("usage")
            input_tokens = settled.input_tokens
            output_tokens = settled.output_tokens
            total_tokens = settled.total_tokens
            latency_ms = settled.api_latency_ms
            cost = settled.cost_cny
            if input_tokens is None:
                if (
                    usage_body.get("usage_status") != "usage_missing"
                    or usage is not None
                    or output_tokens is not None
                    or total_tokens is not None
                    or cost is not None
                ):
                    raise ValueError("attempt history missing usage identity drifted")
            else:
                if (
                    not isinstance(usage, Mapping)
                    or usage.get("prompt_tokens") != input_tokens
                    or usage.get("completion_tokens") != output_tokens
                    or usage.get("total_tokens") != total_tokens
                    or cost
                    != _source_cost_cny(
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        pricing=pricing,
                    )
                ):
                    raise ValueError("attempt history resource accounting drifted")
            if latency_body.get("latency_ms") != latency_ms:
                raise ValueError("attempt history latency accounting drifted")
            ledger_record = ledger_by_entry[row.inventory_entry_id]
            charged = getattr(ledger_record, "charged_tokens")
            estimated = getattr(ledger_record, "cost_estimate")
            if input_tokens is not None and (
                charged is not None
                and charged != total_tokens
                or estimated is not None
                and Decimal(estimated) != cost
            ):
                raise ValueError("attempt history ledger resource accounting drifted")
            if (
                settled.input_tokens != input_tokens
                or settled.output_tokens != output_tokens
                or settled.total_tokens != total_tokens
                or settled.api_latency_ms != latency_ms
                or settled.cost_cny != cost
            ):
                raise ValueError("ordered settled Exp1 resource evidence drifted")
            terminal_locator = next(
                locator
                for locator in entry.object_locators
                if locator.object_role in {"raw_output", "provider_failure"}
            )
            request_locator = next(
                locator
                for locator in entry.object_locators
                if locator.object_role == "request_body"
            )
            failure_kind = acquisition.get("failure_kind")
            if entry.terminal_kind == "success":
                outcome = "unclassified_successful_transport"
                flags = (None, None, None, None, None, None)
            else:
                outcome = "timeout" if failure_kind == "timeout" else "provider_failure"
                flags = (False, None, None, None, outcome == "timeout", True)
            ledger_body = _ledger_record_body(ledger_record)
            provider_family = provenance.get("provider_family", "unknown")
            model_id = model.get("configured_model", model.get("model", "unknown"))
            if not isinstance(provider_family, str) or not isinstance(model_id, str):
                raise ValueError("attempt history provider/model identity drifted")
            attempts.append(
                Exp1AttemptTrace(
                    attempt_id=attempt_id,
                    attempt_index=attempt_index,
                    current_attempt_id=attempt_id,
                    current_attempt_ordinal=attempt_index,
                    source_entry_id=entry.entry_id,
                    source_sample_slot_index=row.sample_slot_index,
                    source_replacement_slot=row.replacement_slot,
                    replacement_slot=row.replacement_slot,
                    source_acquisition_attempt_id=attempt_id,
                    request_identity_digest=row.inference_request_digest,
                    request_artifact_ref=settled.request_artifact_ref,
                    request_artifact_digest=settled.request_artifact_digest,
                    response_artifact_ref=settled.response_artifact_ref,
                    response_artifact_digest=settled.response_artifact_digest,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    total_tokens=total_tokens,
                    api_latency_ms=latency_ms,
                    cost_cny=cost,
                    outcome=outcome,
                    success=flags[0],
                    wrong_answer=flags[1],
                    parse_failure=flags[2],
                    checker_failure=flags[3],
                    timeout=flags[4],
                    missingness=flags[5],
                    terminal_selected=settled.terminal_selected,
                    retry_reason=settled.retry_reason,
                    ledger_ref=settled.ledger_ref,
                    ledger_record_digest=settled.ledger_record_digest,
                    provider_family=provider_family,
                    model_id=model_id,
                    provider_config_digest=row.provider_config_digest,
                )
            )
        nodes_by_question.setdefault((case_id, case_digest, sample_slot), []).append(
            Exp1NodeTrace.create(
                question_id=case_id,
                node_id=unit_id,
                interval_start=None if interval is None else interval[0],
                interval_end=None if interval is None else interval[1],
                attempts=attempts,
            )
        )
    questions = tuple(
        Exp1QuestionTrace.create(
            question_id=case_id,
            case_record_digest=case_digest,
            sample_slot_index=sample_slot,
            nodes=nodes,
        )
        for (case_id, case_digest, sample_slot), nodes in nodes_by_question.items()
    )
    bank = Exp1TraceBank.create(questions=questions)
    return Exp1AttemptHistoryAuthority.create(
        source_bank_root_id=manifest.bank_root_id,
        source_manifest_digest=manifest.manifest_digest,
        source_inventory_digest=manifest.inventory_digest,
        source_inventory_entry_ids=tuple(
            row.inventory_entry_id for row in plan.rows
        ),
        trace_bank=bank,
    )


def persist_exp1_attempt_history_authority(
    *, resolver_root: str | Path, authority: Exp1AttemptHistoryAuthority
) -> Path:
    if type(authority) is not Exp1AttemptHistoryAuthority:
        raise TypeError("typed Exp1 attempt history authority is required")
    path = Path(resolver_root).resolve() / _EXP1_ATTEMPT_HISTORY_NAME
    encoded = (_canonical_json(authority.to_dict()) + "\n").encode("utf-8")
    if path.exists():
        if path.read_bytes() != encoded:
            raise ValueError("existing attempt history authority identity drifted")
        return path
    with path.open("xb") as stream:
        stream.write(encoded)
        stream.flush()
    return path


def load_exp1_attempt_history_authority(
    *,
    resolver_root: str | Path,
    expected_source_manifest_digest: str,
    expected_source_inventory_digest: str,
) -> Exp1AttemptHistoryAuthority:
    path = Path(resolver_root).resolve() / _EXP1_ATTEMPT_HISTORY_NAME
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(body, Mapping):
            raise ValueError("authority root must be an object")
        authority = Exp1AttemptHistoryAuthority.from_dict(body)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError("invalid Exp1 attempt history authority") from exc
    if (
        authority.source_manifest_digest != expected_source_manifest_digest
        or authority.source_inventory_digest != expected_source_inventory_digest
    ):
        raise ValueError("Exp1 attempt history authority source identity drifted")
    return authority


_EXP1_PROTOCOL_OUTCOME_KEYS = {
    "schema_version",
    "source_entry_id",
    "source_response_artifact_digest",
    "current_attempt_id",
    "attempt_status",
    "accepted_validity",
    "failure_stage",
    "failure_kind",
    "terminal_selected",
    "parser_ref",
    "verifier_checker_ref",
    "protocol_ledger_ref",
}


def _exp1_protocol_outcome_flags(
    evidence: Mapping[str, object],
) -> tuple[str, tuple[bool, bool, bool, bool, bool, bool]]:
    status = _strict_str(evidence, "attempt_status")
    accepted = evidence["accepted_validity"]
    if accepted is not None and type(accepted) is not bool:
        raise ValueError("accepted_validity must be a boolean or null")
    failure_stage = evidence["failure_stage"]
    failure_kind = evidence["failure_kind"]
    if failure_stage is not None and (
        not isinstance(failure_stage, str) or not failure_stage
    ):
        raise ValueError("failure_stage must be a non-empty string or null")
    if failure_kind is not None and (
        not isinstance(failure_kind, str) or not failure_kind
    ):
        raise ValueError("failure_kind must be a non-empty string or null")
    if status == "succeeded" and accepted is True:
        return "success", (True, False, False, False, False, False)
    if status == "verification_rejected" and accepted is False:
        return "wrong_answer", (False, True, False, False, False, False)
    if status == "parse_failed":
        return "parse_failure", (False, False, True, False, False, False)
    if status == "checker_rejected":
        return "checker_failure", (False, False, False, True, False, False)
    if status in {"lease_expired", "timeout"}:
        return "timeout", (False, False, False, False, True, True)
    if status in {"provider_error", "missing", "not_started"}:
        return "missingness", (False, False, False, False, False, True)
    if status == "executor_error":
        return "executor_error", (False, False, False, False, False, True)
    raise ValueError("unsupported Exp1 protocol attempt terminal classification")


def apply_exp1_protocol_outcome_overlay(
    *,
    authority: Exp1AttemptHistoryAuthority,
    protocol_attempt_evidence: Sequence[Mapping[str, object]],
) -> Exp1AttemptHistoryAuthority:
    """Overlay actual protocol/parser/verifier/checker outcomes without touching AI bytes."""

    if type(authority) is not Exp1AttemptHistoryAuthority:
        raise TypeError("typed Exp1 attempt history authority is required")
    evidence_by_entry: dict[str, Mapping[str, object]] = {}
    for evidence in protocol_attempt_evidence:
        if not isinstance(evidence, Mapping):
            raise TypeError("Exp1 protocol attempt evidence must be an object")
        _require_exact_keys(evidence, _EXP1_PROTOCOL_OUTCOME_KEYS, "protocol outcome")
        if evidence["schema_version"] != "tokenshare.exp1_protocol_attempt_outcome.v1":
            raise ValueError("unsupported Exp1 protocol outcome schema")
        source_entry_id = _strict_str(evidence, "source_entry_id")
        if source_entry_id in evidence_by_entry:
            raise ValueError("Exp1 protocol outcome source entry is ambiguous")
        evidence_by_entry[source_entry_id] = evidence
    # source authority 仍是合法性查表；本轮 trace 只需覆盖实际出现的 entries。
    # 未进入本轮 trace 的 source attempt 保留 acquisition 原始的未分类状态。
    expected_entries = {
        attempt.source_entry_id for attempt in authority.trace_bank.attempts
    }
    if not evidence_by_entry or not set(evidence_by_entry).issubset(expected_entries):
        raise ValueError("Exp1 protocol outcome coverage is incomplete")

    questions: list[Exp1QuestionTrace] = []
    for question in authority.trace_bank.questions:
        nodes: list[Exp1NodeTrace] = []
        for node in question.nodes:
            attempts: list[Exp1AttemptTrace] = []
            for attempt in node.attempts:
                evidence = evidence_by_entry.get(attempt.source_entry_id)
                if evidence is None:
                    # 没有 committed protocol evidence 时，不伪造 parser/verifier/ledger 结果。
                    attempts.append(attempt)
                    continue
                response_digest = _strict_str(
                    evidence, "source_response_artifact_digest"
                )
                _require_digest("source_response_artifact_digest", response_digest)
                if response_digest != attempt.response_artifact_digest:
                    raise ValueError("Exp1 protocol outcome response digest drifted")
                outcome, flags = _exp1_protocol_outcome_flags(evidence)
                terminal_selected = evidence["terminal_selected"]
                if type(terminal_selected) is not bool:
                    raise ValueError("terminal_selected must be a boolean")
                attempts.append(
                    replace(
                        attempt,
                        outcome=outcome,
                        success=flags[0],
                        wrong_answer=flags[1],
                        parse_failure=flags[2],
                        checker_failure=flags[3],
                        timeout=flags[4],
                        missingness=flags[5],
                        terminal_selected=terminal_selected,
                        protocol_current_attempt_id=_strict_str(
                            evidence, "current_attempt_id"
                        ),
                        protocol_parser_ref=_strict_optional_str(
                            evidence, "parser_ref"
                        ),
                        protocol_verifier_checker_ref=_strict_optional_str(
                            evidence, "verifier_checker_ref"
                        ),
                        protocol_ledger_ref=_strict_str(
                            evidence, "protocol_ledger_ref"
                        ),
                    )
                )
            nodes.append(
                Exp1NodeTrace.create(
                    question_id=node.question_id,
                    node_id=node.node_id,
                    interval_start=node.interval_start,
                    interval_end=node.interval_end,
                    attempts=attempts,
                )
            )
        questions.append(
            Exp1QuestionTrace.create(
                question_id=question.question_id,
                case_record_digest=question.case_record_digest,
                sample_slot_index=question.sample_slot_index,
                nodes=nodes,
            )
        )
    return Exp1AttemptHistoryAuthority.create(
        source_bank_root_id=authority.source_bank_root_id,
        source_manifest_digest=authority.source_manifest_digest,
        source_inventory_digest=authority.source_inventory_digest,
        source_inventory_entry_ids=authority.source_inventory_entry_ids,
        trace_bank=Exp1TraceBank.create(questions=questions),
    )


def _read_strict_jsonl(path: Path, *, label: str) -> tuple[Mapping[str, object], ...]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError(f"{label} is unreadable") from exc
    rows: list[Mapping[str, object]] = []
    for line in lines:
        if not line.strip():
            raise ValueError(f"{label} contains a blank record")
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{label} contains invalid JSON") from exc
        if not isinstance(row, Mapping):
            raise ValueError(f"{label} records must be objects")
        rows.append(row)
    return tuple(rows)


def _persisted_trace_wrapper_body(
    *,
    trace_root: Path,
    generation_root: Path,
    task_id: str,
    native_ref: Mapping[str, object],
) -> Mapping[str, object]:
    artifact_rows = _read_strict_jsonl(
        generation_root / "artifacts" / "artifact_index.jsonl",
        label="Exp1 committed artifact index",
    )
    matches = tuple(
        row
        for row in artifact_rows
        if row.get("task_id") == task_id
        and (
            row.get("source_artifact_ref") == native_ref
            or (
                isinstance(row.get("source_artifact_ref"), Mapping)
                and isinstance(row["source_artifact_ref"].get("source"), Mapping)
                and row["source_artifact_ref"]["source"].get(
                    "source_artifact_ref"
                )
                == native_ref
            )
        )
    )
    if len(matches) != 1:
        raise ValueError("Exp1 committed trace wrapper artifact is not unique")
    record = matches[0]
    relative = record.get("path")
    if not isinstance(relative, str) or not relative:
        raise ValueError("Exp1 committed trace wrapper path is invalid")
    path = (trace_root / relative).resolve(strict=False)
    if not path.is_file() or trace_root not in path.parents:
        # Generation-local focused fixtures and the canonical store both remain
        # below the immutable trace root; never follow a source URI directly.
        path = (generation_root / relative).resolve(strict=False)
    if not path.is_file() or trace_root not in path.parents:
        raise ValueError("Exp1 committed trace wrapper escaped trace root")
    payload = path.read_bytes()
    digest = _bytes_digest(payload)
    if (
        record.get("content_hash") != digest
        or record.get("size_bytes") != len(payload)
        or native_ref.get("content_hash") != digest
        or native_ref.get("size_bytes") != len(payload)
    ):
        raise ValueError("Exp1 committed trace wrapper identity drifted")
    try:
        body = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Exp1 committed trace wrapper is invalid JSON") from exc
    if not isinstance(body, Mapping):
        raise ValueError("Exp1 committed trace wrapper must be an object")
    return body


def _formal_attempt_ref(
    *, relative_path: str, attempt_id: str, role: str, value: object = None
) -> str:
    suffix = "" if value is None else f":{_digest(value)}"
    return f"formal:{relative_path}#{attempt_id}:{role}{suffix}"


def _committed_exp1_task_protocol_identity(
    task_rows: Sequence[Mapping[str, object]],
) -> tuple[str, str]:
    """验证已提交 case 标识与正式 runtime task 标识的权威映射。"""
    if len(task_rows) != 1:
        raise ValueError("Exp1 committed task protocol mapping is ambiguous")
    task_record = task_rows[0]
    case_task_id = task_record.get("task_id")
    declared_case_id = task_record.get("case_id")
    domain = task_record.get("domain")
    protocol_task_id = task_record.get("protocol_task_id")
    if (
        not isinstance(case_task_id, str)
        or not case_task_id
        or (declared_case_id is not None and declared_case_id != case_task_id)
        or not isinstance(domain, str)
        or not domain
        or not isinstance(protocol_task_id, str)
        or not protocol_task_id
    ):
        raise ValueError("Exp1 committed task protocol mapping is invalid")
    from tokenshare.experiments.paper_experiment_contracts import (
        formal_runtime_task_id,
    )

    try:
        expected_protocol_task_id = formal_runtime_task_id(domain, case_task_id)
    except ValueError as exc:
        raise ValueError("Exp1 committed task protocol mapping is invalid") from exc
    if expected_protocol_task_id != protocol_task_id:
        raise ValueError("Exp1 committed task protocol mapping drifted")
    return case_task_id, protocol_task_id


def collect_exp1_protocol_outcomes_from_formal_trace(
    *,
    authority: Exp1AttemptHistoryAuthority,
    trace_context: object,
    trace_output_root: str | Path,
) -> tuple[Mapping[str, object], ...]:
    """Join committed Exp1 deliveries, attempts, verifier events and selection."""

    if type(authority) is not Exp1AttemptHistoryAuthority:
        raise TypeError("typed Exp1 attempt history authority is required")
    trace_root = Path(trace_output_root).resolve(strict=False)
    pointer_paths = tuple(
        sorted(
            trace_root.glob(
                "experiments/exp1_real_ai_feasibility/runs/*/*/CURRENT.json"
            )
        )
    )
    if not pointer_paths:
        raise ValueError("committed Exp1 formal trace is missing")
    source_attempts = {
        attempt.source_entry_id: attempt for attempt in authority.trace_bank.attempts
    }
    if len(source_attempts) != len(authority.trace_bank.attempts):
        raise ValueError("Exp1 source entry identity is ambiguous")
    evidence_by_entry: dict[str, Mapping[str, object]] = {}
    for pointer_path in pointer_paths:
        try:
            pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Exp1 CURRENT pointer is invalid") from exc
        if not isinstance(pointer, Mapping) or pointer.get("schema_version") != (
            "tokenshare.paper_checkpoint_current.v1"
        ):
            raise ValueError("Exp1 CURRENT pointer schema drifted")
        generation_id = pointer.get("generation_id")
        generation_manifest_digest = pointer.get("generation_manifest_digest")
        if not isinstance(generation_id, str) or not generation_id:
            raise ValueError("Exp1 CURRENT generation identity is missing")
        if not isinstance(generation_manifest_digest, str):
            raise ValueError("Exp1 CURRENT generation digest is missing")
        _require_digest("generation_manifest_digest", generation_manifest_digest)
        generation_root = pointer_path.parent / ".generations" / generation_id
        attempt_path = generation_root / "per_attempt_results.jsonl"
        task_path = generation_root / "per_task_results.jsonl"
        event_path = generation_root / "events" / "event_log.jsonl"
        attempt_rows = _read_strict_jsonl(
            attempt_path, label="Exp1 committed per-attempt evidence"
        )
        task_rows = _read_strict_jsonl(
            task_path, label="Exp1 committed per-task evidence"
        )
        event_rows = _read_strict_jsonl(
            event_path, label="Exp1 committed protocol event ledger"
        )
        case_task_id, task_protocol_task_id = (
            _committed_exp1_task_protocol_identity(task_rows)
        )
        attempts_by_id: dict[str, Mapping[str, object]] = {}
        for row in attempt_rows:
            attempt_id = row.get("attempt_id")
            if not isinstance(attempt_id, str) or not attempt_id:
                raise ValueError("Exp1 formal attempt identity is invalid")
            if attempt_id in attempts_by_id:
                raise ValueError("Exp1 formal attempt identity is ambiguous")
            attempts_by_id[attempt_id] = row
        commits_by_attempt: dict[str, Mapping[str, object]] = {}
        verification_by_attempt: dict[str, Mapping[str, object]] = {}
        selected_events_by_attempt: dict[str, Mapping[str, object]] = {}
        for event in event_rows:
            event_type = event.get("event_type")
            payload = event.get("payload")
            if not isinstance(payload, Mapping):
                continue
            if event_type == "TRACE_DELIVERY_COMMITTED.v1":
                attempt_id = payload.get("attempt_id")
                if (
                    not isinstance(attempt_id, str)
                    or not attempt_id
                    or attempt_id in commits_by_attempt
                ):
                    raise ValueError("Exp1 trace commit identity is ambiguous")
                commits_by_attempt[attempt_id] = event
            elif event_type == "VERIFICATION_RECORDED":
                attempt_id = payload.get("attempt_id")
                if isinstance(attempt_id, str) and attempt_id:
                    if attempt_id in verification_by_attempt:
                        raise ValueError("Exp1 verification identity is ambiguous")
                    verification_by_attempt[attempt_id] = event
            elif event_type == "CANONICAL_OUTPUTS_BOUND":
                selected_attempt_id = payload.get("selected_attempt_id")
                if not isinstance(selected_attempt_id, str) or not selected_attempt_id:
                    raise ValueError("Exp1 canonical selection identity is invalid")
                if selected_attempt_id in selected_events_by_attempt:
                    raise ValueError("Exp1 canonical selection identity is ambiguous")
                selected_events_by_attempt[selected_attempt_id] = event

        condition_id = pointer_path.parent.parent.name
        relative_attempt = attempt_path.relative_to(trace_root).as_posix()
        relative_event = event_path.relative_to(trace_root).as_posix()
        for current_attempt_id, commit in commits_by_attempt.items():
            row = attempts_by_id.get(current_attempt_id)
            if row is None:
                raise ValueError("Exp1 committed delivery has no formal attempt")
            task_id = row.get("task_id")
            protocol_task_id = row.get("protocol_task_id")
            planned_ai_unit_id = row.get("planned_ai_unit_id")
            if (
                not isinstance(task_id, str)
                or not task_id
                or not isinstance(protocol_task_id, str)
                or not protocol_task_id
                or task_id != case_task_id
                or protocol_task_id != task_protocol_task_id
                or not isinstance(planned_ai_unit_id, str)
                or not planned_ai_unit_id
            ):
                raise ValueError("Exp1 formal attempt protocol task identity drifted")
            runtime = trace_context.runtime_for(
                condition_id=condition_id, case_id=task_id
            )
            bindings = tuple(getattr(runtime, "bindings", ()))
            payload = commit.get("payload")
            assert isinstance(payload, Mapping)
            if commit.get("task_id") != protocol_task_id:
                raise ValueError("Exp1 committed trace event task identity drifted")
            binding_digest = payload.get("binding_digest")
            matches = tuple(
                binding
                for binding in bindings
                if getattr(binding, "planned_ai_unit_id", None)
                == planned_ai_unit_id
                and getattr(binding, "binding_digest", None) == binding_digest
            )
            if len(matches) != 1:
                raise ValueError("Exp1 committed delivery binding is unavailable")
            binding = matches[0]
            native_ref = payload.get("current_wrapper_ref")
            if not isinstance(native_ref, Mapping):
                raise ValueError("Exp1 committed trace wrapper ref is invalid")
            wrapper = _persisted_trace_wrapper_body(
                trace_root=trace_root,
                generation_root=generation_root,
                task_id=task_id,
                native_ref=native_ref,
            )
            from tokenshare.local_runtime.contracts import PreparedTraceDelivery

            delivery = PreparedTraceDelivery.from_dict(wrapper)
            if delivery.bank_root_id != authority.source_bank_root_id:
                raise ValueError("Exp1 committed delivery bank root drifted")
            if delivery.manifest_digest != authority.source_manifest_digest:
                raise ValueError("Exp1 committed delivery manifest drifted")
            if (
                delivery.attempt_id != current_attempt_id
                or delivery.task_id != protocol_task_id
                or delivery.unit_id != row.get("unit_id")
                or delivery.binding_digest != binding_digest
            ):
                raise ValueError("Exp1 committed delivery current identity drifted")
            deliveries = tuple(getattr(binding, "attempt_deliveries", ()))
            delivery_matches = tuple(
                item
                for item in deliveries
                if getattr(item, "current_attempt_ordinal", None)
                == delivery.attempt_ordinal
            )
            if len(delivery_matches) != 1:
                raise ValueError("Exp1 committed delivery ordinal is not frozen")
            frozen = delivery_matches[0]
            source_entry_id = getattr(frozen, "source_entry_id", None)
            source = source_attempts.get(source_entry_id)
            if (
                source is None
                or delivery.entry_id != source.source_entry_id
                or getattr(frozen, "source_attempt_index", None)
                != source.attempt_index
                or getattr(frozen, "source_replacement_slot", None)
                != source.source_replacement_slot
                or getattr(frozen, "delivery_kind", None) != "ordinary_attempt"
            ):
                raise ValueError("Exp1 committed delivery source attempt drifted")
            terminal_locator_role = (
                "raw_output"
                if delivery.source_terminal_kind == "success"
                else "provider_failure"
            )
            terminal_locators = tuple(
                locator
                for locator in delivery.source_bank_object_locators
                if isinstance(locator, Mapping)
                and locator.get("object_role") == terminal_locator_role
            )
            if len(terminal_locators) != 1:
                raise ValueError("Exp1 committed delivery response locator is invalid")
            if (
                terminal_locators[0].get("object_digest")
                != source.response_artifact_digest
            ):
                raise ValueError("Exp1 committed delivery response digest drifted")
            row_binding_digests = tuple(
                row.get(name)
                for name in ("source_binding_digest", "trace_source_binding_digest")
                if row.get(name) is not None
            )
            if row_binding_digests and any(
                value != binding_digest for value in row_binding_digests
            ):
                raise ValueError("Exp1 formal attempt binding digest drifted")
            status = row.get("attempt_status")
            accepted = row.get("accepted_validity")
            error_kind = row.get("error_kind")
            raw_ref = row.get("raw_output_ref")
            if raw_ref is None:
                raise ValueError("Exp1 formal attempt raw response ref is missing")
            elif not isinstance(raw_ref, Mapping):
                raise ValueError("Exp1 formal attempt raw wrapper ref is invalid")
            elif raw_ref != native_ref:
                raise ValueError("Exp1 formal attempt raw wrapper ref drifted")
            selected = current_attempt_id in selected_events_by_attempt
            if selected:
                canonical_event = selected_events_by_attempt[current_attempt_id]
                canonical_payload = canonical_event.get("payload")
                if (
                    canonical_event.get("task_id") != protocol_task_id
                    or not isinstance(canonical_payload, Mapping)
                    or canonical_payload.get("task_id") != protocol_task_id
                ):
                    raise ValueError("Exp1 canonical event task identity drifted")
                if canonical_payload.get("unit_id") != row.get("unit_id"):
                    raise ValueError("Exp1 canonical event unit identity drifted")
            parsed_ref = row.get("parsed_output_ref")
            parse_failure_ref = row.get("parse_failure_ref")
            if status == "succeeded":
                if not selected or not isinstance(parsed_ref, Mapping):
                    raise ValueError("Exp1 succeeded attempt lacks canonical evidence")
                accepted = True
            elif status == "verification_rejected":
                if selected or not isinstance(parsed_ref, Mapping):
                    raise ValueError("Exp1 rejected answer evidence is contradictory")
                accepted = False
            elif status == "parse_failed":
                if selected or not isinstance(parse_failure_ref, Mapping):
                    raise ValueError("Exp1 parse failure evidence is incomplete")
                accepted = False
            elif status == "checker_rejected":
                if selected or not isinstance(parsed_ref, Mapping):
                    raise ValueError("Exp1 checker failure evidence is incomplete")
                accepted = False
            elif status == "provider_error" and isinstance(error_kind, str) and (
                "timeout" in error_kind.lower()
            ):
                status = "timeout"
                accepted = False
            elif status in {"provider_error", "lease_expired", "executor_error"}:
                accepted = False
            else:
                raise ValueError("unsupported committed Exp1 attempt status")
            if source.terminal_selected and selected is False and status == "succeeded":
                raise ValueError("Exp1 terminal source attempt was not canonical")
            verification_event = verification_by_attempt.get(current_attempt_id)
            if status in {"succeeded", "verification_rejected", "checker_rejected"}:
                if verification_event is None:
                    raise ValueError("Exp1 verifier/checker event is missing")
                verification_payload = verification_event.get("payload")
                if (
                    verification_event.get("task_id") != protocol_task_id
                    or not isinstance(verification_payload, Mapping)
                    or verification_payload.get("task_id") != protocol_task_id
                ):
                    raise ValueError("Exp1 verification event task identity drifted")
                if verification_payload.get("unit_id") != row.get("unit_id"):
                    raise ValueError("Exp1 verification event unit identity drifted")
                expected_verification = (
                    ("passed", True)
                    if status == "succeeded"
                    else ("failed", False)
                )
                if (
                    verification_payload.get("status") != expected_verification[0]
                    or verification_payload.get("eligible_for_canonical")
                    is not expected_verification[1]
                ):
                    raise ValueError("Exp1 verification event verdict drifted")
                verifier_ref = _formal_attempt_ref(
                    relative_path=relative_event,
                    attempt_id=current_attempt_id,
                    role=str(verification_event.get("event_id")),
                )
            else:
                verifier_ref = None
            parser_value = parse_failure_ref if status == "parse_failed" else parsed_ref
            parser_ref = (
                None
                if parser_value is None
                else _formal_attempt_ref(
                    relative_path=relative_attempt,
                    attempt_id=current_attempt_id,
                    role="parser",
                    value=parser_value,
                )
            )
            commit_event_id = commit.get("event_id")
            if not isinstance(commit_event_id, str) or not commit_event_id:
                raise ValueError("Exp1 trace commit ledger ref is invalid")
            evidence = {
                "schema_version": "tokenshare.exp1_protocol_attempt_outcome.v1",
                "source_entry_id": source.source_entry_id,
                "source_response_artifact_digest": source.response_artifact_digest,
                "current_attempt_id": current_attempt_id,
                "attempt_status": status,
                "accepted_validity": accepted,
                "failure_stage": (
                    None
                    if status == "succeeded"
                    else "parser" if status == "parse_failed"
                    else "checker" if status == "checker_rejected"
                    else "verification" if status == "verification_rejected"
                    else "provider" if status in {"provider_error", "timeout"}
                    else "executor"
                ),
                "failure_kind": error_kind,
                "terminal_selected": source.terminal_selected,
                "parser_ref": parser_ref,
                "verifier_checker_ref": verifier_ref,
                "protocol_ledger_ref": _formal_attempt_ref(
                    relative_path=relative_event,
                    attempt_id=current_attempt_id,
                    role=commit_event_id,
                ),
            }
            if source.source_entry_id in evidence_by_entry:
                raise ValueError("Exp1 source attempt outcome is ambiguous")
            evidence_by_entry[source.source_entry_id] = evidence
    # 允许 partial trace，但不允许任何 committed delivery 越过 source authority 边界。
    if not evidence_by_entry or not set(evidence_by_entry).issubset(source_attempts):
        raise ValueError("Exp1 protocol outcome coverage is incomplete")
    return tuple(evidence_by_entry[key] for key in sorted(evidence_by_entry))


def finalize_exp1_protocol_outcomes_from_formal_trace(
    *,
    authority: Exp1AttemptHistoryAuthority,
    trace_context: object,
    trace_output_root: str | Path,
    authority_root: str | Path,
) -> tuple[Exp1AttemptHistoryAuthority, Path]:
    evidence = collect_exp1_protocol_outcomes_from_formal_trace(
        authority=authority,
        trace_context=trace_context,
        trace_output_root=trace_output_root,
    )
    classified = apply_exp1_protocol_outcome_overlay(
        authority=authority, protocol_attempt_evidence=evidence
    )
    root = Path(authority_root).resolve(strict=False)
    root.mkdir(parents=True, exist_ok=True)
    path = root / _EXP1_PROTOCOL_CLASSIFIED_ATTEMPT_HISTORY_NAME
    encoded = (_canonical_json(classified.to_dict()) + "\n").encode("utf-8")
    if path.exists():
        if path.read_bytes() != encoded:
            raise ValueError("existing classified Exp1 authority identity drifted")
    else:
        with path.open("xb") as stream:
            stream.write(encoded)
            stream.flush()
    return classified, path


def load_exp1_protocol_classified_attempt_history_authority(
    *,
    authority_root: str | Path,
    expected_source_manifest_digest: str,
    expected_source_inventory_digest: str,
) -> Exp1AttemptHistoryAuthority:
    path = (
        Path(authority_root).resolve(strict=False)
        / _EXP1_PROTOCOL_CLASSIFIED_ATTEMPT_HISTORY_NAME
    )
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(body, Mapping):
            raise ValueError("classified authority must be an object")
        authority = Exp1AttemptHistoryAuthority.from_dict(body)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError("invalid classified Exp1 attempt history authority") from exc
    if (
        authority.source_manifest_digest != expected_source_manifest_digest
        or authority.source_inventory_digest != expected_source_inventory_digest
    ):
        raise ValueError("classified Exp1 authority source identity drifted")
    for attempt in authority.trace_bank.attempts:
        protocol_refs = (
            attempt.protocol_current_attempt_id,
            attempt.protocol_parser_ref,
            attempt.protocol_verifier_checker_ref,
            attempt.protocol_ledger_ref,
        )
        if all(value is None for value in protocol_refs):
            continue
        if (
            attempt.outcome == "unclassified_successful_transport"
            or
            attempt.protocol_current_attempt_id is None
            or attempt.protocol_ledger_ref is None
        ):
            raise ValueError("classified Exp1 authority outcome coverage is incomplete")
    return authority


@dataclass(frozen=True)
class TargetShard:
    shard_id: str
    interval_start: int
    interval_end: int

    def __post_init__(self) -> None:
        if not self.shard_id or self.interval_start > self.interval_end:
            raise ValueError("target shard identity/interval is invalid")


@dataclass(frozen=True)
class SourceAttemptAllocationRef:
    source_node_id: str
    source_attempt_id: str
    source_attempt_index: int
    source_response_artifact_digest: str
    overlap_start: int
    overlap_end: int
    allocated_tokens: int | None
    allocated_api_latency_ms: int | None
    allocated_cost_cny: Decimal | None
    coverage_numerator: int
    coverage_denominator: int
    formula: str = _ALLOCATION_FORMULA

    @property
    def unknown_resource_fields(self) -> tuple[str, ...]:
        return tuple(
            name
            for name, value in (
                ("tokens", self.allocated_tokens),
                ("api_latency", self.allocated_api_latency_ms),
                ("cost", self.allocated_cost_cny),
            )
            if value is None
        )


@dataclass(frozen=True)
class DerivedExp2Shard:
    shard_id: str
    interval_start: int
    interval_end: int
    source_attempt_refs: tuple[SourceAttemptAllocationRef, ...]
    source_tokens_total: int | None
    source_tokens_known_total: int
    source_tokens_missing_attempt_count: int
    source_api_latency_total_ms: int | None
    source_api_latency_known_total_ms: int
    source_api_latency_missing_attempt_count: int
    source_cost_total_cny: Decimal | None
    source_cost_known_total_cny: Decimal
    source_cost_missing_attempt_count: int
    terminal_outcome: str
    terminal_response_artifact_digest: str
    current_provider_calls: int = 0
    current_protocol_scheduling_latency_ms: int = 0
    injected_fault_or_recovery_delay_ms: int = 0
    resource_origin: str = "simulated_from_exp1_trace"

    @property
    def source_tokens_total_unknown(self) -> bool:
        return self.source_tokens_total is None

    @property
    def source_api_latency_total_unknown(self) -> bool:
        return self.source_api_latency_total_ms is None

    @property
    def source_cost_total_unknown(self) -> bool:
        return self.source_cost_total_cny is None

    @property
    def derived_total_latency_ms(self) -> int | None:
        if self.source_api_latency_total_ms is None:
            return None
        return (
            self.source_api_latency_total_ms
            + self.current_protocol_scheduling_latency_ms
            + self.injected_fault_or_recovery_delay_ms
        )


@dataclass(frozen=True)
class Exp2RepartitionResult:
    question_id: str
    source_trace_digest: str
    shards: tuple[DerivedExp2Shard, ...]
    worker_completion_ms: tuple[int | None, ...]
    experiment_makespan_ms: int | None
    derivation_digest: str
    current_provider_calls: int = 0
    resource_origin: str = "simulated_from_exp1_trace"


def _largest_remainder(total: int, weights: Sequence[int]) -> tuple[int, ...]:
    if total < 0 or not weights or any(weight <= 0 for weight in weights):
        raise ValueError("largest-remainder inputs are invalid")
    denominator = sum(weights)
    base = [(total * weight) // denominator for weight in weights]
    remainder = total - sum(base)
    priority = sorted(
        range(len(weights)),
        key=lambda index: (-((total * weights[index]) % denominator), index),
    )
    for index in priority[:remainder]:
        base[index] += 1
    return tuple(base)


def _optional_largest_remainder(
    total: int | None, weights: Sequence[int]
) -> tuple[int | None, ...]:
    if total is None:
        if not weights or any(weight <= 0 for weight in weights):
            raise ValueError("largest-remainder weights are invalid")
        return tuple(None for _ in weights)
    return _largest_remainder(total, weights)


def _optional_resource_summary(
    *,
    token_values: Sequence[int | None],
    latency_values: Sequence[int | None],
    cost_values: Sequence[Decimal | None],
) -> dict[str, object]:
    if not (len(token_values) == len(latency_values) == len(cost_values)):
        raise ValueError("resource observation sequences drifted")
    known_tokens = tuple(value for value in token_values if value is not None)
    known_latency = tuple(value for value in latency_values if value is not None)
    known_cost = tuple(value for value in cost_values if value is not None)
    missing_tokens = len(token_values) - len(known_tokens)
    missing_latency = len(latency_values) - len(known_latency)
    missing_cost = len(cost_values) - len(known_cost)
    token_total = sum(known_tokens)
    latency_total = sum(known_latency)
    cost_total = sum(known_cost, Decimal()).quantize(_COST_QUANTUM)
    return {
        "source_tokens_total": None if missing_tokens else token_total,
        "source_tokens_known_total": token_total,
        "source_tokens_missing_attempt_count": missing_tokens,
        "source_tokens_total_unknown": bool(missing_tokens),
        "source_api_latency_total_ms": (
            None if missing_latency else latency_total
        ),
        "source_api_latency_known_total_ms": latency_total,
        "source_api_latency_missing_attempt_count": missing_latency,
        "source_api_latency_total_unknown": bool(missing_latency),
        "source_cost_total_cny": (
            None if missing_cost else format(cost_total, "f")
        ),
        "source_cost_known_total_cny": format(cost_total, "f"),
        "source_cost_missing_attempt_count": missing_cost,
        "source_cost_total_unknown": bool(missing_cost),
    }


def _validate_partition(
    source_nodes: Sequence[Exp1NodeTrace], target_shards: Sequence[TargetShard]
) -> tuple[TargetShard, ...]:
    if not source_nodes or any(
        node.interval_start is None or node.interval_end is None
        for node in source_nodes
    ):
        raise ValueError("Exp2 repartition requires interval-mapped source nodes")
    ordered_sources = tuple(sorted(source_nodes, key=lambda item: (item.interval_start, item.interval_end)))
    ordered_targets = tuple(sorted(target_shards, key=lambda item: (item.interval_start, item.interval_end, item.shard_id)))
    if not ordered_targets or len({item.shard_id for item in ordered_targets}) != len(ordered_targets):
        raise ValueError("target shards must be non-empty with unique identities")
    for values, name in ((ordered_sources, "source"), (ordered_targets, "target")):
        for left, right in zip(values, values[1:]):
            if left.interval_end + 1 != right.interval_start:
                raise ValueError(f"{name} intervals must form one contiguous partition")
    if (
        ordered_sources[0].interval_start != ordered_targets[0].interval_start
        or ordered_sources[-1].interval_end != ordered_targets[-1].interval_end
    ):
        raise ValueError("target partition does not cover the source interval exactly")
    return ordered_targets


def derive_exp2_repartition(
    *,
    bank: Exp1TraceBank,
    question_id: str,
    target_shards: Sequence[TargetShard],
    worker_count: int,
) -> Exp2RepartitionResult:
    """Project every source attempt by interval and simulate deterministic workers."""

    if worker_count <= 0:
        raise ValueError("worker_count must be positive")
    question = bank.question(question_id)
    targets = _validate_partition(question.nodes, target_shards)
    refs_by_shard: dict[str, list[SourceAttemptAllocationRef]] = {
        target.shard_id: [] for target in targets
    }
    terminals_by_shard: dict[str, list[Exp1AttemptTrace]] = {
        target.shard_id: [] for target in targets
    }

    for node in question.nodes:
        overlaps = [
            (
                target,
                max(node.interval_start, target.interval_start),
                min(node.interval_end, target.interval_end),
            )
            for target in targets
            if max(node.interval_start, target.interval_start)
            <= min(node.interval_end, target.interval_end)
        ]
        weights = tuple(end - start + 1 for _, start, end in overlaps)
        if sum(weights) != node.interval_end - node.interval_start + 1:
            raise ValueError("source node coverage drifted during repartition")
        for attempt in node.attempts:
            token_parts = _optional_largest_remainder(attempt.total_tokens, weights)
            latency_parts = _optional_largest_remainder(
                attempt.api_latency_ms, weights
            )
            cost_units = (
                None
                if attempt.cost_cny is None
                else int(
                    (attempt.cost_cny / _COST_QUANTUM).to_integral_exact(
                        rounding=ROUND_DOWN
                    )
                )
            )
            cost_parts = _optional_largest_remainder(cost_units, weights)
            coverage_denominator = sum(weights)
            for allocation_index, (target, start, end) in enumerate(overlaps):
                cost_part = cost_parts[allocation_index]
                refs_by_shard[target.shard_id].append(
                    SourceAttemptAllocationRef(
                        source_node_id=node.node_id,
                        source_attempt_id=attempt.attempt_id,
                        source_attempt_index=attempt.attempt_index,
                        source_response_artifact_digest=attempt.response_artifact_digest,
                        overlap_start=start,
                        overlap_end=end,
                        allocated_tokens=token_parts[allocation_index],
                        allocated_api_latency_ms=latency_parts[allocation_index],
                        allocated_cost_cny=(
                            None
                            if cost_part is None
                            else Decimal(cost_part) * _COST_QUANTUM
                        ),
                        coverage_numerator=weights[allocation_index],
                        coverage_denominator=coverage_denominator,
                    )
                )
                if attempt.terminal_selected:
                    terminals_by_shard[target.shard_id].append(attempt)

    derived_shards: list[DerivedExp2Shard] = []
    for target in targets:
        refs = tuple(refs_by_shard[target.shard_id])
        terminals = terminals_by_shard[target.shard_id]
        outcomes = tuple(attempt.outcome for attempt in terminals)
        digests = tuple(attempt.response_artifact_digest for attempt in terminals)
        known_tokens = tuple(
            ref.allocated_tokens
            for ref in refs
            if ref.allocated_tokens is not None
        )
        known_latency = tuple(
            ref.allocated_api_latency_ms
            for ref in refs
            if ref.allocated_api_latency_ms is not None
        )
        known_cost = tuple(
            ref.allocated_cost_cny
            for ref in refs
            if ref.allocated_cost_cny is not None
        )
        missing_tokens = len(refs) - len(known_tokens)
        missing_latency = len(refs) - len(known_latency)
        missing_cost = len(refs) - len(known_cost)
        token_total = sum(known_tokens)
        latency_total = sum(known_latency)
        cost_total = sum(known_cost, Decimal()).quantize(_COST_QUANTUM)
        derived_shards.append(
            DerivedExp2Shard(
                shard_id=target.shard_id,
                interval_start=target.interval_start,
                interval_end=target.interval_end,
                source_attempt_refs=refs,
                source_tokens_total=(None if missing_tokens else token_total),
                source_tokens_known_total=token_total,
                source_tokens_missing_attempt_count=missing_tokens,
                source_api_latency_total_ms=(
                    None if missing_latency else latency_total
                ),
                source_api_latency_known_total_ms=latency_total,
                source_api_latency_missing_attempt_count=missing_latency,
                source_cost_total_cny=(None if missing_cost else cost_total),
                source_cost_known_total_cny=cost_total,
                source_cost_missing_attempt_count=missing_cost,
                terminal_outcome=(outcomes[0] if len(set(outcomes)) == 1 else "mixed"),
                terminal_response_artifact_digest=(
                    digests[0] if len(set(digests)) == 1 else _digest(list(digests))
                ),
            )
        )

    worker_totals: list[int | None] = [0 for _ in range(worker_count)]
    for shard in derived_shards:
        worker_index = min(
            range(worker_count),
            key=lambda index: (
                worker_totals[index] is None,
                0 if worker_totals[index] is None else worker_totals[index],
                index,
            ),
        )
        worker_start = worker_totals[worker_index]
        worker_totals[worker_index] = (
            None
            if worker_start is None or shard.source_api_latency_total_ms is None
            else worker_start + shard.source_api_latency_total_ms
        )
    derivation_body = {
        "question_id": question_id,
        "source_trace_digest": bank.bank_digest,
        "worker_count": worker_count,
        "formula": _ALLOCATION_FORMULA,
        "shards": [
            {
                "shard_id": shard.shard_id,
                "interval_start": shard.interval_start,
                "interval_end": shard.interval_end,
                "source_attempt_refs": [
                    {
                        "source_node_id": ref.source_node_id,
                        "source_attempt_id": ref.source_attempt_id,
                        "source_attempt_index": ref.source_attempt_index,
                        "source_response_artifact_digest": ref.source_response_artifact_digest,
                        "overlap_start": ref.overlap_start,
                        "overlap_end": ref.overlap_end,
                        "allocated_tokens": ref.allocated_tokens,
                        "allocated_api_latency_ms": ref.allocated_api_latency_ms,
                        "allocated_cost_cny": (
                            None
                            if ref.allocated_cost_cny is None
                            else format(ref.allocated_cost_cny, "f")
                        ),
                        "unknown_resource_fields": list(
                            ref.unknown_resource_fields
                        ),
                        "coverage_numerator": ref.coverage_numerator,
                        "coverage_denominator": ref.coverage_denominator,
                        "formula": ref.formula,
                    }
                    for ref in shard.source_attempt_refs
                ],
            }
            for shard in derived_shards
        ],
        "worker_completion_ms": worker_totals,
    }
    return Exp2RepartitionResult(
        question_id=question_id,
        source_trace_digest=bank.bank_digest,
        shards=tuple(derived_shards),
        worker_completion_ms=tuple(worker_totals),
        experiment_makespan_ms=(
            None if any(value is None for value in worker_totals) else max(worker_totals)
        ),
        derivation_digest=_digest(derivation_body),
    )


@dataclass(frozen=True)
class RedeliveredAttempt:
    current_attempt_id: str
    source_attempt_id: str
    response_artifact_ref: str
    response_artifact_digest: str
    source_outcome: str
    inherited_source_api_latency_ms: int
    protocol_recovery_latency_ms: int
    current_provider_calls: int = 0
    current_provider_tokens: int = 0

    @property
    def derived_total_latency_ms(self) -> int:
        return self.inherited_source_api_latency_ms + self.protocol_recovery_latency_ms


def redeliver_saved_attempt(
    *,
    attempt: Exp1AttemptTrace,
    current_attempt_id: str,
    protocol_recovery_latency_ms: int,
) -> RedeliveredAttempt:
    if not current_attempt_id or protocol_recovery_latency_ms < 0:
        raise ValueError("redelivery identity/latency is invalid")
    return RedeliveredAttempt(
        current_attempt_id=current_attempt_id,
        source_attempt_id=attempt.attempt_id,
        response_artifact_ref=attempt.response_artifact_ref,
        response_artifact_digest=attempt.response_artifact_digest,
        source_outcome=attempt.outcome,
        inherited_source_api_latency_ms=attempt.api_latency_ms,
        protocol_recovery_latency_ms=protocol_recovery_latency_ms,
    )


@dataclass
class GlobalPaidAttemptLedger:
    _attempts: dict[str, Exp1AttemptTrace] = field(default_factory=dict)

    def record(self, attempt: Exp1AttemptTrace) -> None:
        previous = self._attempts.get(attempt.attempt_id)
        if previous is not None and previous != attempt:
            raise ValueError("source attempt identity/accounting drifted")
        self._attempts[attempt.attempt_id] = attempt

    @property
    def real_provider_call_count(self) -> int:
        return len(self._attempts)

    @property
    def real_tokens_known_total(self) -> int:
        return sum(
            attempt.total_tokens
            for attempt in self._attempts.values()
            if attempt.total_tokens is not None
        )

    @property
    def real_tokens_missing_attempt_count(self) -> int:
        return sum(
            attempt.total_tokens is None for attempt in self._attempts.values()
        )

    @property
    def real_tokens_total(self) -> int | None:
        if self.real_tokens_missing_attempt_count:
            return None
        return self.real_tokens_known_total

    @property
    def real_api_latency_known_total_ms(self) -> int:
        return sum(
            attempt.api_latency_ms
            for attempt in self._attempts.values()
            if attempt.api_latency_ms is not None
        )

    @property
    def real_api_latency_missing_attempt_count(self) -> int:
        return sum(
            attempt.api_latency_ms is None for attempt in self._attempts.values()
        )

    @property
    def real_api_latency_total_ms(self) -> int | None:
        if self.real_api_latency_missing_attempt_count:
            return None
        return self.real_api_latency_known_total_ms

    @property
    def real_cost_known_total_cny(self) -> Decimal:
        return sum(
            (
                attempt.cost_cny
                for attempt in self._attempts.values()
                if attempt.cost_cny is not None
            ),
            Decimal(),
        )

    @property
    def real_cost_missing_attempt_count(self) -> int:
        return sum(attempt.cost_cny is None for attempt in self._attempts.values())

    @property
    def real_cost_total_cny(self) -> Decimal | None:
        if self.real_cost_missing_attempt_count:
            return None
        return self.real_cost_known_total_cny


@dataclass(frozen=True)
class Exp4ConditionRun:
    condition_id: str
    source_trace_digest: str
    engine_run_ref: str
    preregistered_root_count: int
    completed_root_count: int
    current_provider_calls: int = 0


@dataclass(frozen=True)
class Exp4ReplayResult:
    runs: tuple[Exp4ConditionRun, ...]
    global_paid_ledger: GlobalPaidAttemptLedger


def run_exp4_independent_replays(
    *,
    condition_ids: Sequence[str],
    bank: Exp1TraceBank,
    protocol_runner: Callable[[str, str], str],
    preregistered_root_count: int,
    completed_root_count_by_condition: Mapping[str, int],
) -> Exp4ReplayResult:
    if preregistered_root_count < 0 or not condition_ids:
        raise ValueError("Experiment 4 denominator/conditions are invalid")
    if len(set(condition_ids)) != len(condition_ids):
        raise ValueError("Experiment 4 conditions must be unique")
    ledger = GlobalPaidAttemptLedger()
    for attempt in bank.attempts:
        ledger.record(attempt)
    runs: list[Exp4ConditionRun] = []
    engine_refs: set[str] = set()
    for condition_id in condition_ids:
        if condition_id not in completed_root_count_by_condition:
            raise ValueError("fixed-denominator completion evidence is missing")
        completed = completed_root_count_by_condition[condition_id]
        if completed < 0 or completed > preregistered_root_count:
            raise ValueError("completed roots cannot change the fixed denominator")
        engine_ref = protocol_runner(condition_id, bank.bank_digest)
        if not engine_ref or engine_ref in engine_refs:
            raise ValueError("each ablation must have an independent engine run")
        engine_refs.add(engine_ref)
        runs.append(
            Exp4ConditionRun(
                condition_id=condition_id,
                source_trace_digest=bank.bank_digest,
                engine_run_ref=engine_ref,
                preregistered_root_count=preregistered_root_count,
                completed_root_count=completed,
            )
        )
    return Exp4ReplayResult(runs=tuple(runs), global_paid_ledger=ledger)


def select_exp1_acquisition_candidates(candidates: Iterable[Any]) -> tuple[Any, ...]:
    """Fail-closed selection for the only paid DeepSeek experiment."""

    selected: list[Any] = []
    for candidate in candidates:
        experiment_id = (
            candidate.get("experiment_id")
            if isinstance(candidate, Mapping)
            else getattr(getattr(candidate, "condition", None), "experiment_id", None)
        )
        if not isinstance(experiment_id, str):
            raise ValueError("acquisition candidate experiment_id is missing")
        if experiment_id == "exp1_real_ai_feasibility":
            selected.append(candidate)
    return tuple(selected)


def select_exp1_source_roots(
    *,
    full_roots: Iterable[Any],
    selected_roots: Iterable[Any],
) -> tuple[Any, ...]:
    """Select the matching Exp1 source root for every question/sample slot."""

    replay_experiments = {
        "exp1_real_ai_feasibility",
        "exp2_real_ai_scalability",
        "exp3_real_ai_fault_recovery",
        "exp4_real_ai_protocol_ablation",
    }

    def root_key(root: Any) -> tuple[str, str, int]:
        case_id = getattr(root, "case_id", None)
        case_digest = getattr(root, "case_record_digest", None)
        sample_slot = getattr(root, "repeat_id", None)
        if (
            not isinstance(case_id, str)
            or not case_id
            or not isinstance(case_digest, str)
            or isinstance(sample_slot, bool)
            or not isinstance(sample_slot, int)
            or sample_slot < 0
        ):
            raise ValueError("selected replay question/sample identity is missing")
        _require_digest("case_record_digest", case_digest)
        return case_id, case_digest, sample_slot

    selected_keys = tuple(
        sorted(
            {
                root_key(root)
                for root in selected_roots
                if getattr(getattr(root, "condition", None), "experiment_id", None)
                in replay_experiments
            }
        )
    )
    if not selected_keys:
        raise ValueError("selected replay questions are missing")
    exp1_by_key: dict[tuple[str, str, int], list[Any]] = {}
    for root in full_roots:
        if (
            getattr(getattr(root, "condition", None), "experiment_id", None)
            == "exp1_real_ai_feasibility"
        ):
            exp1_by_key.setdefault(root_key(root), []).append(root)
    result: list[Any] = []
    for key in selected_keys:
        matches = exp1_by_key.get(key, [])
        if len(matches) != 1:
            raise ValueError(
                "selected question/sample requires one canonical Exp1 source root: "
                f"{key[0]}@{key[2]}"
            )
        result.append(matches[0])
    return tuple(result)


@dataclass(frozen=True)
class _Exp1FixedTraceReplayCase:
    condition_id: str
    case_id: str
    case_record_digest: str
    runtime: Any
    source_inventory_entry_ids: tuple[str, ...]


@dataclass(frozen=True)
class FixedTraceAttemptSequence:
    condition_id: str
    case_id: str
    planned_ai_unit_id: str
    sample_slot_index: int
    ordinary_source_entry_ids: tuple[str, ...]
    fault_redelivery_source_entry_ids: tuple[str, ...]
    current_source_entry_ids: tuple[str, ...]
    sequence_digest: str

    def __post_init__(self) -> None:
        if (
            not self.condition_id
            or not self.case_id
            or not self.planned_ai_unit_id
            or not self.ordinary_source_entry_ids
            or self.sample_slot_index < 0
        ):
            raise ValueError("fixed trace attempt sequence identity is incomplete")
        expected = (
            self.ordinary_source_entry_ids
            + self.fault_redelivery_source_entry_ids
        )
        if self.current_source_entry_ids != expected:
            raise ValueError("ordinary attempt/fault redelivery sequence drifted")
        if any(
            item != self.ordinary_source_entry_ids[-1]
            for item in self.fault_redelivery_source_entry_ids
        ):
            raise ValueError("fault redelivery must reuse the terminal source artifact")
        _require_digest("sequence_digest", self.sequence_digest)
        if self.sequence_digest != _digest(self._body()):
            raise ValueError("fixed trace attempt sequence digest drifted")

    def _body(self) -> dict[str, object]:
        return {
            "condition_id": self.condition_id,
            "case_id": self.case_id,
            "planned_ai_unit_id": self.planned_ai_unit_id,
            "sample_slot_index": self.sample_slot_index,
            "ordinary_source_entry_ids": list(self.ordinary_source_entry_ids),
            "fault_redelivery_source_entry_ids": list(
                self.fault_redelivery_source_entry_ids
            ),
            "current_source_entry_ids": list(self.current_source_entry_ids),
        }

    @classmethod
    def create(
        cls,
        *,
        condition_id: str,
        case_id: str,
        planned_ai_unit_id: str,
        sample_slot_index: int,
        ordinary_source_entry_ids: Sequence[str],
        fault_redelivery_count: int,
    ) -> "FixedTraceAttemptSequence":
        ordinary = tuple(ordinary_source_entry_ids)
        if not ordinary or fault_redelivery_count < 0:
            raise ValueError("fixed trace source sequence is invalid")
        redeliveries = tuple(ordinary[-1] for _ in range(fault_redelivery_count))
        body = {
            "condition_id": condition_id,
            "case_id": case_id,
            "planned_ai_unit_id": planned_ai_unit_id,
            "sample_slot_index": sample_slot_index,
            "ordinary_source_entry_ids": list(ordinary),
            "fault_redelivery_source_entry_ids": list(redeliveries),
            "current_source_entry_ids": list(ordinary + redeliveries),
        }
        return cls(
            condition_id=condition_id,
            case_id=case_id,
            planned_ai_unit_id=planned_ai_unit_id,
            sample_slot_index=sample_slot_index,
            ordinary_source_entry_ids=ordinary,
            fault_redelivery_source_entry_ids=redeliveries,
            current_source_entry_ids=ordinary + redeliveries,
            sequence_digest=_digest(body),
        )


@dataclass(frozen=True)
class Exp1FixedTraceReplayContext:
    """Duck-typed formal-runner context backed only by immutable Exp1 entries."""

    cases: tuple[_Exp1FixedTraceReplayCase, ...]
    source_bank_root_id: str
    source_manifest_digest: str
    attempt_sequences: tuple[FixedTraceAttemptSequence, ...]
    current_provider_call_count: int = 0
    resource_origin: str = "simulated_from_exp1_trace"

    def __post_init__(self) -> None:
        keys = tuple((case.condition_id, case.case_id) for case in self.cases)
        if not keys or len(set(keys)) != len(keys):
            raise ValueError("fixed-trace replay cases must be non-empty and unique")
        if self.current_provider_call_count != 0:
            raise ValueError("fixed-trace replay cannot call a provider")
        _require_digest("source_manifest_digest", self.source_manifest_digest)
        sequence_keys = tuple(
            (
                item.condition_id,
                item.case_id,
                item.planned_ai_unit_id,
            )
            for item in self.attempt_sequences
        )
        if not sequence_keys or len(set(sequence_keys)) != len(sequence_keys):
            raise ValueError("fixed trace attempt sequences are missing or duplicated")

    def runtime_for(self, *, condition_id: str, case_id: str) -> Any:
        matches = tuple(
            case.runtime
            for case in self.cases
            if case.condition_id == condition_id and case.case_id == case_id
        )
        if len(matches) != 1:
            raise KeyError("fixed Exp1 replay runtime is missing for condition/case")
        return matches[0]

    def attempt_sequence_for(
        self, *, condition_id: str, case_id: str, planned_ai_unit_id: str
    ) -> FixedTraceAttemptSequence:
        matches = tuple(
            item
            for item in self.attempt_sequences
            if item.condition_id == condition_id
            and item.case_id == case_id
            and item.planned_ai_unit_id == planned_ai_unit_id
        )
        if len(matches) != 1:
            raise KeyError("fixed trace attempt sequence is missing or ambiguous")
        return matches[0]

    @property
    def source_inventory_entry_ids(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                entry_id
                for case in self.cases
                for entry_id in case.source_inventory_entry_ids
            )
        )


def build_exp1_fixed_trace_replay_context(
    *,
    resolver: Any,
    target_roots: Sequence[Any],
    source_exp1_inventory_entry_ids: Sequence[str],
) -> Exp1FixedTraceReplayContext:
    """Bind Exp3/4 target attempts to Exp1 entries without acquiring responses."""

    from tokenshare.executors.response_bank import ResponseBankResolver
    from tokenshare.executors.trace_backed import (
        freeze_projected_trace_source_binding,
    )
    from tokenshare.experiments.paper_response_bank import (
        PaperTraceRuntimeContext,
        replacement_slots_for,
    )

    if type(resolver) is not ResponseBankResolver:
        raise TypeError("fixed-trace resolver must be an exact ResponseBankResolver")
    allowed_ids = tuple(dict.fromkeys(str(item) for item in source_exp1_inventory_entry_ids))
    resolver_ids = tuple(row.inventory_entry_id for row in resolver.index.inventory_rows)
    if (
        not allowed_ids
        or any(not item for item in allowed_ids)
        or set(allowed_ids) != set(resolver_ids)
    ):
        raise ValueError("Exp1 source inventory identity drifted")
    entry_by_inventory_id = {
        entry.inventory_entry_id: entry for entry in resolver.index.entries
    }
    rows_by_case_sample_unit: dict[tuple[str, int, str], list[Any]] = {}
    for row in resolver.index.inventory_rows:
        if row.inventory_entry_id not in entry_by_inventory_id:
            raise ValueError("Exp1 source entry is missing")
        rows_by_case_sample_unit.setdefault(
            (
                row.case_record_digest,
                row.sample_slot_index,
                row.planned_ai_unit_id,
            ), []
        ).append(row)
    cases: list[_Exp1FixedTraceReplayCase] = []
    attempt_sequences: list[FixedTraceAttemptSequence] = []
    for root in target_roots:
        condition = getattr(root, "condition", None)
        experiment_id = str(getattr(condition, "experiment_id", ""))
        if experiment_id == "exp2_real_ai_scalability":
            raise ValueError("Experiment 2 requires deterministic interval projection")
        if experiment_id not in {
            "exp3_real_ai_fault_recovery",
            "exp4_real_ai_protocol_ablation",
        }:
            raise ValueError("fixed-trace context accepts only Experiment 3/4 targets")
        condition_id = str(getattr(condition, "condition_id", ""))
        case_id = str(getattr(root, "case_id", ""))
        case_record_digest = str(getattr(root, "case_record_digest", ""))
        planned_ai_unit_ids = tuple(getattr(root, "planned_ai_unit_ids", ()))
        sample_slot_index = getattr(root, "repeat_id", None)
        if (
            not condition_id
            or not case_id
            or not planned_ai_unit_ids
            or isinstance(sample_slot_index, bool)
            or not isinstance(sample_slot_index, int)
            or sample_slot_index < 0
        ):
            raise ValueError("fixed-trace target root identity is incomplete")
        _require_digest("case_record_digest", case_record_digest)
        current_slots = replacement_slots_for(
            experiment_id=experiment_id,
            fault_type=str(getattr(condition, "fault_type", "none")),
            ablation_mode=str(getattr(condition, "ablation_mode", "FULL")),
        )
        if current_slots != tuple(range(len(current_slots))):
            raise ValueError("current replay slots are not canonical")
        bindings: list[Any] = []
        source_rows_for_case: list[Any] = []
        for planned_ai_unit_id in planned_ai_unit_ids:
            source_rows = tuple(
                sorted(
                    rows_by_case_sample_unit.get(
                        (
                            case_record_digest,
                            sample_slot_index,
                            str(planned_ai_unit_id),
                        ), []
                    ),
                    key=lambda row: (row.replacement_slot, row.inventory_entry_id),
                )
            )
            if (
                not source_rows
                or tuple(row.replacement_slot for row in source_rows)
                != tuple(range(len(source_rows)))
                or any(
                    row.sample_slot_index != sample_slot_index
                    for row in source_rows
                )
            ):
                raise ValueError("canonical Exp1 attempt history is missing or out of order")
            source_entries = tuple(
                entry_by_inventory_id[row.inventory_entry_id] for row in source_rows
            )
            sequence = FixedTraceAttemptSequence.create(
                condition_id=condition_id,
                case_id=case_id,
                planned_ai_unit_id=str(planned_ai_unit_id),
                sample_slot_index=sample_slot_index,
                ordinary_source_entry_ids=tuple(
                    entry.entry_id for entry in source_entries
                ),
                fault_redelivery_count=max(0, len(current_slots) - 1),
            )
            attempt_sequences.append(sequence)
            redelivery_reason = (
                f"{experiment_id}:{condition_id}:"
                f"{str(getattr(condition, 'fault_type', 'none'))}:"
                f"{str(getattr(condition, 'ablation_mode', 'FULL'))}:"
                "saved_terminal_artifact_redelivery"
            )
            delivery_kinds = (
                ("ordinary_attempt",) * len(sequence.ordinary_source_entry_ids)
                + ("fault_redelivery",)
                * len(sequence.fault_redelivery_source_entry_ids)
            )
            redelivery_reasons = (
                (None,) * len(sequence.ordinary_source_entry_ids)
                + (redelivery_reason,)
                * len(sequence.fault_redelivery_source_entry_ids)
            )
            bindings.append(
                freeze_projected_trace_source_binding(
                    resolver,
                    current_planned_ai_unit_id=str(planned_ai_unit_id),
                    current_sample_slot_index=sample_slot_index,
                    source_entry_ids_by_current_attempt=(
                        sequence.current_source_entry_ids
                    ),
                    delivery_kinds_by_current_attempt=delivery_kinds,
                    redelivery_reasons_by_current_attempt=redelivery_reasons,
                )
            )
            source_rows_for_case.extend(source_rows)
        unique_rows = tuple(
            {
                row.inventory_entry_id: row for row in source_rows_for_case
            }.values()
        )
        runtime = PaperTraceRuntimeContext(
            resolver=resolver,
            bindings=tuple(bindings),
            inventory_rows=unique_rows,
        )
        cases.append(
            _Exp1FixedTraceReplayCase(
                condition_id=condition_id,
                case_id=case_id,
                case_record_digest=case_record_digest,
                runtime=runtime,
                source_inventory_entry_ids=tuple(
                    row.inventory_entry_id for row in unique_rows
                ),
            )
        )
    manifest = resolver.index.manifest
    return Exp1FixedTraceReplayContext(
        cases=tuple(cases),
        source_bank_root_id=manifest.bank_root_id,
        source_manifest_digest=manifest.manifest_digest,
        attempt_sequences=tuple(attempt_sequences),
    )


@dataclass(frozen=True)
class Exp2ProjectedTraceContext:
    formal_trace_context: Any
    derived_bank_root: Path
    source_bank_root_id: str
    derivation_digest: str
    repeat_worker_schedules: tuple[Mapping[str, object], ...] = ()
    current_provider_call_count: int = 0
    resource_origin: str = "simulated_from_exp1_trace"

    def runtime_for(self, *, condition_id: str, case_id: str) -> Any:
        return self.formal_trace_context.runtime_for(
            condition_id=condition_id,
            case_id=case_id,
        )


@dataclass(frozen=True)
class _Exp2FormalTraceContextRouter:
    contexts: tuple[Any, ...]

    @property
    def cases(self) -> tuple[Any, ...]:
        return tuple(
            case for context in self.contexts for case in context.cases
        )

    def runtime_for(self, *, condition_id: str, case_id: str) -> Any:
        matches: list[Any] = []
        for context in self.contexts:
            try:
                matches.append(
                    context.runtime_for(
                        condition_id=condition_id,
                        case_id=case_id,
                    )
                )
            except KeyError:
                continue
        if len(matches) != 1:
            raise KeyError("Exp2 projected runtime is missing or ambiguous")
        return matches[0]


@dataclass(frozen=True)
class ResultsFirstTraceContextRouter:
    """One formal-runner context that routes each experiment to its fixed source."""

    inventory_plan: Any
    exp1_context: Any
    exp2_context: Exp2ProjectedTraceContext
    fixed_context: Exp1FixedTraceReplayContext
    experiment_by_condition_id: Mapping[str, str]
    available_inventory_entry_ids: tuple[str, ...]
    source_manifest_digest: str
    source_attempt_history_digest: str
    router_authority_digest: str
    current_provider_call_count: int = 0
    resource_origin: str = "simulated_from_exp1_trace"

    def __post_init__(self) -> None:
        experiments = set(self.experiment_by_condition_id.values())
        if experiments not in (
            {
                "exp1_real_ai_feasibility",
                "exp2_real_ai_scalability",
                "exp3_real_ai_fault_recovery",
                "exp4_real_ai_protocol_ablation",
            },
            {
                "exp1_real_ai_feasibility",
                "exp2_real_ai_scalability",
                "exp3_real_ai_fault_recovery",
            },
        ):
            raise ValueError("trace router experiment coverage is incomplete")
        if self.current_provider_call_count != 0:
            raise ValueError("trace router cannot contain current provider calls")
        _require_digest("source_manifest_digest", self.source_manifest_digest)
        _require_digest(
            "source_attempt_history_digest", self.source_attempt_history_digest
        )
        _require_digest("router_authority_digest", self.router_authority_digest)
        expected_ids = tuple(
            row.inventory_entry_id for row in self.inventory_plan.rows
        )
        if (
            len(set(expected_ids)) != len(expected_ids)
            or set(expected_ids) != set(self.available_inventory_entry_ids)
        ):
            raise ValueError("trace router semantic inventory coverage drifted")

    def runtime_for(self, *, condition_id: str, case_id: str) -> Any:
        try:
            experiment_id = self.experiment_by_condition_id[condition_id]
        except KeyError as exc:
            raise KeyError("trace router condition is missing") from exc
        if experiment_id == "exp1_real_ai_feasibility":
            context = self.exp1_context
        elif experiment_id == "exp2_real_ai_scalability":
            context = self.exp2_context
        else:
            context = self.fixed_context
        return context.runtime_for(condition_id=condition_id, case_id=case_id)


def _build_condition_source_resource_attribution(
    *,
    condition_id: str,
    experiment_id: str,
    attempts: Sequence[Exp1AttemptTrace],
) -> dict[str, object]:
    ordered_attempts = tuple(attempts)
    if not condition_id or not experiment_id or not ordered_attempts:
        raise ValueError("condition source attribution identity is incomplete")
    resource_summary = _optional_resource_summary(
        token_values=tuple(attempt.total_tokens for attempt in ordered_attempts),
        latency_values=tuple(
            attempt.api_latency_ms for attempt in ordered_attempts
        ),
        cost_values=tuple(attempt.cost_cny for attempt in ordered_attempts),
    )
    return {
        "condition_id": condition_id,
        "experiment_id": experiment_id,
        "source_attempt_ids": [
            attempt.attempt_id for attempt in ordered_attempts
        ],
        "source_provider_attempt_count": len(ordered_attempts),
        **resource_summary,
        "current_provider_calls": 0,
    }


def build_results_first_trace_context_router(
    *,
    resolver: Any,
    source_exp1_inventory_plan: Any,
    replay_semantic_authority: Any,
    target_roots: Sequence[Any],
    exp2_projection_root: str | Path,
) -> ResultsFirstTraceContextRouter:
    """Bind Exp1 source, Exp2 projection and Exp3/4 replay without transport."""

    from tokenshare.executors.response_bank import ResponseBankResolver
    from tokenshare.experiments.paper_response_bank import (
        ResultsFirstReplaySemanticAuthority,
        SemanticInventoryPlan,
        build_paper_formal_trace_context,
    )

    if type(resolver) is not ResponseBankResolver:
        raise TypeError("trace router requires an exact Exp1 response bank")
    if type(source_exp1_inventory_plan) is not SemanticInventoryPlan:
        raise TypeError("trace router requires the Exp1 acquisition inventory")
    if type(replay_semantic_authority) is not ResultsFirstReplaySemanticAuthority:
        raise TypeError("trace router requires typed replay semantic authority")
    if any(
        condition_ref["experiment_id"] != "exp1_real_ai_feasibility"
        for condition_ref in source_exp1_inventory_plan.condition_refs
    ):
        raise ValueError("paid source inventory contains Experiment 2--4 slots")
    source_inventory_ids = tuple(
        row.inventory_entry_id for row in source_exp1_inventory_plan.rows
    )
    if set(source_inventory_ids) != {
        entry.inventory_entry_id for entry in resolver.index.entries
    }:
        raise ValueError("Exp1 source inventory/bank identity drifted")
    attempt_history = load_exp1_attempt_history_authority(
        resolver_root=resolver.root_path,
        expected_source_manifest_digest=resolver.index.manifest.manifest_digest,
        expected_source_inventory_digest=source_exp1_inventory_plan.inventory_digest,
    )
    _persist_exact_json_once(
        Path(exp2_projection_root).resolve().parent
        / "exp1_attempt_history_authority.final.v1.json",
        attempt_history.to_dict(),
    )
    attempts_by_source_entry = {
        attempt.source_entry_id: attempt
        for attempt in attempt_history.trace_bank.attempts
    }
    if (
        len(attempts_by_source_entry) != len(attempt_history.trace_bank.attempts)
        or set(attempts_by_source_entry)
        != {entry.entry_id for entry in resolver.index.entries}
    ):
        raise ValueError("Exp1 attempt history source entry coverage drifted")
    rows_by_entry_id = {row.entry_id: row for row in resolver.index.inventory_rows}
    for entry_id, attempt in attempts_by_source_entry.items():
        row = rows_by_entry_id.get(entry_id)
        entry = next(
            (item for item in resolver.index.entries if item.entry_id == entry_id),
            None,
        )
        acquisition_locator = None if entry is None else next(
            (
                locator
                for locator in entry.object_locators
                if locator.object_role == "acquisition_attempt"
            ),
            None,
        )
        if row is None or entry is None or acquisition_locator is None:
            raise ValueError("Exp1 attempt history source entry is missing")
        acquisition_body = _json_object_bytes(
            resolver.read_verified(acquisition_locator), "acquisition_attempt"
        )
        if (
            attempt.source_sample_slot_index != row.sample_slot_index
            or attempt.source_replacement_slot != row.replacement_slot
            or attempt.replacement_slot != attempt.source_replacement_slot
            or attempt.source_acquisition_attempt_id
            != acquisition_body.get("attempt_id")
        ):
            raise ValueError("Exp1 attempt history source slot identity drifted")

    exp1_context = build_paper_formal_trace_context(
        inventory_plan=source_exp1_inventory_plan,
        resolver=resolver,
    )
    exp2_context = build_exp2_projected_trace_context(
        resolver=resolver,
        replay_semantic_authority=replay_semantic_authority,
        output_root=exp2_projection_root,
        source_attempt_history_authority=attempt_history,
    )
    roots = tuple(target_roots)
    fixed_roots = tuple(
        root
        for root in roots
        if getattr(getattr(root, "condition", None), "experiment_id", None)
        in {
            "exp3_real_ai_fault_recovery",
            "exp4_real_ai_protocol_ablation",
        }
    )
    fixed_context = build_exp1_fixed_trace_replay_context(
        resolver=resolver,
        target_roots=fixed_roots,
        source_exp1_inventory_entry_ids=source_inventory_ids,
    )
    experiment_by_condition: dict[str, str] = {}
    case_ids_by_condition: dict[str, set[str]] = {}
    for candidate in replay_semantic_authority.candidates:
        previous = experiment_by_condition.setdefault(
            candidate.condition_id, candidate.experiment_id
        )
        if previous != candidate.experiment_id:
            raise ValueError("trace router condition experiment identity drifted")
        case_ids_by_condition.setdefault(candidate.condition_id, set()).add(
            candidate.case_id
        )
    attempts_by_case_sample = {
        (question.case_record_digest, question.sample_slot_index): tuple(
            attempt
            for node in question.nodes
            for attempt in node.attempts
        )
        for question in attempt_history.trace_bank.questions
    }
    attributed_ids_by_condition: dict[str, set[str]] = {}
    for candidate in replay_semantic_authority.candidates:
        attributed_attempts = attempts_by_case_sample.get(
            (candidate.case_record_digest, candidate.sample_slot_index)
        )
        if not attributed_attempts:
            raise ValueError("trace condition source attempt attribution is missing")
        attributed_ids_by_condition.setdefault(candidate.condition_id, set()).update(
            attempt.attempt_id for attempt in attributed_attempts
        )
    ordered_source_attempts = attempt_history.trace_bank.attempts
    condition_attributions = []
    for condition_id in sorted(experiment_by_condition):
        source_ids = attributed_ids_by_condition.get(condition_id)
        if not source_ids:
            raise ValueError("trace condition source attempt attribution is empty")
        attempts = tuple(
            attempt
            for attempt in ordered_source_attempts
            if attempt.attempt_id in source_ids
        )
        if len(attempts) != len(source_ids):
            raise ValueError("trace condition source attempt attribution drifted")
        condition_attributions.append(
            _build_condition_source_resource_attribution(
                condition_id=condition_id,
                experiment_id=experiment_by_condition[condition_id],
                attempts=attempts,
            )
        )
    router_authority_body = {
        "schema_version": "tokenshare.results_first_trace_router_authority.v1",
        "source_inventory_digest": source_exp1_inventory_plan.inventory_digest,
        "replay_inventory_digest": (
            replay_semantic_authority.semantic_inventory_plan.inventory_digest
        ),
        "source_manifest_digest": resolver.index.manifest.manifest_digest,
        "source_attempt_history_digest": attempt_history.authority_digest,
        "exp2_derivation_digest": exp2_context.derivation_digest,
        "fixed_attempt_sequence_digests": [
            {
                "condition_id": item.condition_id,
                "case_id": item.case_id,
                "planned_ai_unit_id": item.planned_ai_unit_id,
                "sample_slot_index": item.sample_slot_index,
                "sequence_digest": item.sequence_digest,
            }
            for item in fixed_context.attempt_sequences
        ],
        "experiment_by_condition_id": [
            {
                "condition_id": condition_id,
                "experiment_id": experiment_by_condition[condition_id],
            }
            for condition_id in sorted(experiment_by_condition)
        ],
        "global_paid_summary": dict(attempt_history.global_paid_summary),
        "condition_attributions": condition_attributions,
        "current_provider_calls": 0,
    }
    router_authority_digest = _digest(router_authority_body)
    router = ResultsFirstTraceContextRouter(
        inventory_plan=replay_semantic_authority.semantic_inventory_plan,
        exp1_context=exp1_context,
        exp2_context=exp2_context,
        fixed_context=fixed_context,
        experiment_by_condition_id=dict(experiment_by_condition),
        available_inventory_entry_ids=tuple(
            row.inventory_entry_id
            for row in replay_semantic_authority.semantic_inventory_plan.rows
        ),
        source_manifest_digest=resolver.index.manifest.manifest_digest,
        source_attempt_history_digest=attempt_history.authority_digest,
        router_authority_digest=router_authority_digest,
    )
    for condition_id, case_ids in case_ids_by_condition.items():
        for case_id in case_ids:
            router.runtime_for(condition_id=condition_id, case_id=case_id)
    _persist_exact_json_once(
        Path(exp2_projection_root).resolve().parent
        / "results_first_trace_router_authority.v1.json",
        {
            **router_authority_body,
            "authority_digest": router_authority_digest,
        },
    )
    return router


def _json_bytes(value: object) -> bytes:
    return _canonical_json(value).encode("utf-8")


def _persist_exact_json_once(path: Path, body: Mapping[str, object]) -> None:
    encoded = (_canonical_json(body) + "\n").encode("utf-8")
    if path.exists():
        if path.read_bytes() != encoded:
            raise ValueError(f"existing {path.name} identity drifted")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(encoded)
        stream.flush()


def _factor_request_metadata(body_bytes: bytes) -> dict[str, Any]:
    try:
        body = json.loads(body_bytes.decode("utf-8"))
        messages = body["messages"]
        user_text = "\n".join(
            str(message["content"])
            for message in messages
            if message.get("role") == "user"
        )
    except (KeyError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("factor request body is not parseable") from exc
    patterns = {
        "range_start": r'"range_start"\s*:\s*"(\d+)"',
        "range_end": r'"range_end"\s*:\s*"(\d+)"',
        "target_n": r'"target_n"\s*:\s*"(\d+)"',
        "coverage_id": r'"coverage_id"\s*:\s*"([^"]+)"',
        "partition_params_digest": r'"partition_params_digest"\s*:\s*"([^"]+)"',
        "child_index": r'"child_index"\s*:\s*(\d+)',
    }
    values: dict[str, Any] = {}
    for name, pattern in patterns.items():
        matches = tuple(dict.fromkeys(re.findall(pattern, user_text)))
        if len(matches) != 1:
            raise ValueError(f"factor request {name} identity drifted")
        values[name] = matches[0]
    values["range_start"] = int(values["range_start"])
    values["range_end"] = int(values["range_end"])
    values["child_index"] = int(values["child_index"])
    if values["range_start"] > values["range_end"]:
        raise ValueError("factor request interval is invalid")
    return values


def _entry_objects(resolver: Any, entry: Any) -> dict[str, bytes]:
    return {
        locator.object_role: resolver.read_verified(locator)
        for locator in entry.object_locators
    }


def _json_object_bytes(data: bytes, role: str) -> Mapping[str, Any]:
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"source {role} is not JSON") from exc
    if not isinstance(value, Mapping):
        raise ValueError(f"source {role} is not an object")
    return value


def _source_cost_cny(
    *,
    input_tokens: int,
    output_tokens: int,
    pricing: Mapping[str, Any],
) -> Decimal:
    if str(pricing.get("currency")) != "CNY":
        raise ValueError("Exp1 source pricing currency must be CNY")
    try:
        input_rate = Decimal(str(pricing["input_per_million_tokens"]))
        output_rate = Decimal(str(pricing["output_per_million_tokens"]))
    except (KeyError, ValueError) as exc:
        raise ValueError("Exp1 source pricing is incomplete") from exc
    if input_rate < 0 or output_rate < 0:
        raise ValueError("Exp1 source pricing cannot be negative")
    return (
        (
            Decimal(input_tokens) * input_rate
            + Decimal(output_tokens) * output_rate
        )
        / Decimal("1000000")
    ).quantize(_COST_QUANTUM)


def _intervals_cover(start: int, end: int, intervals: Sequence[tuple[int, int]]) -> bool:
    cursor = start
    for left, right in sorted(intervals):
        if right < cursor:
            continue
        if left > cursor:
            return False
        cursor = max(cursor, right + 1)
        if cursor > end:
            return True
    return cursor > end


def _project_factor_content(
    *,
    target: Mapping[str, Any],
    sources: Sequence[tuple[Mapping[str, Any], str, str]],
) -> tuple[str, str]:
    """Project declarations only; never consult the oracle or repair a claim."""

    target_start = int(target["range_start"])
    target_end = int(target["range_end"])
    covered: list[tuple[int, int]] = []
    found: list[tuple[int, Mapping[str, Any]]] = []
    created_at = "1970-01-01T00:00:00Z"
    source_digests: list[str] = []
    for source_meta, content_text, response_digest in sources:
        source_digests.append(response_digest)
        try:
            value = json.loads(content_text)
        except json.JSONDecodeError:
            return content_text, "source_parse_failure_propagated"
        if not isinstance(value, Mapping):
            return content_text, "source_parse_failure_propagated"
        source_start = int(source_meta["range_start"])
        source_end = int(source_meta["range_end"])
        if (
            str(value.get("range_start")) != str(source_start)
            or str(value.get("range_end")) != str(source_end)
        ):
            return content_text, "source_identity_failure_propagated"
        created_at = str(value.get("created_at") or created_at)
        kind = value.get("result_kind")
        if kind == "no_factor_in_range":
            covered.append(
                (max(target_start, source_start), min(target_end, source_end))
            )
        elif kind == "found_factor":
            try:
                factor = int(str(value.get("found_factor")))
            except ValueError:
                return content_text, "source_parse_failure_propagated"
            covered_end = min(target_end, source_end, factor - 1)
            if max(target_start, source_start) <= covered_end:
                covered.append((max(target_start, source_start), covered_end))
            if target_start <= factor <= target_end:
                found.append((factor, value))
        else:
            return content_text, "source_parse_failure_propagated"
    child_index = int(target["child_index"])
    coverage_id = str(target["coverage_id"])
    common = {
        "schema_version": "factorization.range_result.v1",
        "range_result_id": f"range_result:{coverage_id}:{child_index}",
        "target_n": str(target["target_n"]),
        "range_start": str(target_start),
        "range_end": str(target_end),
        "coverage_id": coverage_id,
        "child_index": child_index,
        "partition_params_digest": str(target["partition_params_digest"]),
        "executor_summary": {"checked_range": f"{target_start}-{target_end}"},
        "created_at": created_at,
    }
    if found:
        factor, source_value = min(found, key=lambda item: item[0])
        projected = {
            **common,
            "result_kind": "found_factor",
            "found_factor": str(source_value.get("found_factor")),
            "cofactor": source_value.get("cofactor"),
            "checked_divisor_count": factor - target_start + 1,
        }
        return _canonical_json(projected), "declared_found_factor_projected"
    if _intervals_cover(target_start, target_end, covered):
        projected = {
            **common,
            "result_kind": "no_factor_in_range",
            "found_factor": None,
            "cofactor": None,
            "checked_divisor_count": target_end - target_start + 1,
        }
        return _canonical_json(projected), "declared_no_factor_coverage_projected"
    missing = {
        "schema_version": "tokenshare.exp2_projection_missing.v1",
        "range_start": str(target_start),
        "range_end": str(target_end),
        "source_response_artifact_digests": source_digests,
        "reason": "Exp1 answers do not establish the entire target interval",
    }
    return _canonical_json(missing), "source_missingness_propagated"


def build_exp2_projected_trace_context(
    *,
    resolver: Any,
    replay_semantic_authority: Any,
    output_root: str | Path,
    source_attempt_history_authority: Exp1AttemptHistoryAuthority | None = None,
    _condition_id: str | None = None,
) -> Exp2ProjectedTraceContext:
    """Materialize a provider-zero Exp2 bank derived from immutable Exp1 objects."""

    from tokenshare.executors.response_bank import (
        OBJECT_ROLES,
        ExternalBankObjectLocator,
        ResponseBankEntry,
        ResponseBankManifest,
        ResponseBankResolver,
        ResultsFirstResponseBankManifest,
        canonical_digest,
        initialize_response_bank,
    )
    from tokenshare.experiments.paper_response_bank import (
        ResultsFirstReplaySemanticAuthority,
        build_paper_formal_trace_context,
        build_semantic_inventory,
    )

    if type(resolver) is not ResponseBankResolver:
        raise TypeError("Exp2 source resolver must be exact ResponseBankResolver")
    if type(replay_semantic_authority) is not ResultsFirstReplaySemanticAuthority:
        raise TypeError("typed replay semantic authority is required")
    exp2_candidates = tuple(
        candidate
        for candidate in replay_semantic_authority.candidates
        if candidate.experiment_id == "exp2_real_ai_scalability"
        and (_condition_id is None or candidate.condition_id == _condition_id)
    )
    if not exp2_candidates:
        raise ValueError("Exp2 replay candidates are missing")
    condition_ids = tuple(
        sorted({candidate.condition_id for candidate in exp2_candidates})
    )
    if _condition_id is None and len(condition_ids) > 1:
        root = Path(output_root).resolve()
        contexts = tuple(
            build_exp2_projected_trace_context(
                resolver=resolver,
                replay_semantic_authority=replay_semantic_authority,
                output_root=root
                / hashlib.sha256(condition_id.encode("utf-8")).hexdigest(),
                source_attempt_history_authority=(
                    source_attempt_history_authority
                ),
                _condition_id=condition_id,
            )
            for condition_id in condition_ids
        )
        aggregate_body = {
            "schema_version": "tokenshare.paper.exp2-projection-derivation.v1",
            "source_manifest_digest": resolver.index.manifest.manifest_digest,
            "source_attempt_history_digest": (
                None
                if source_attempt_history_authority is None
                else source_attempt_history_authority.authority_digest
            ),
            "condition_derivations": [
                {
                    "condition_id": condition_id,
                    "derivation_digest": context.derivation_digest,
                }
                for condition_id, context in zip(
                    condition_ids, contexts, strict=True
                )
            ],
            "repeat_worker_schedules": [
                dict(schedule)
                for context in contexts
                for schedule in context.repeat_worker_schedules
            ],
        }
        aggregate_digest = _digest(aggregate_body)
        _persist_exact_json_once(
            root / "exp2_projection_derivation.v1.json",
            {**aggregate_body, "derivation_digest": aggregate_digest},
        )
        return Exp2ProjectedTraceContext(
            formal_trace_context=_Exp2FormalTraceContextRouter(
                contexts=tuple(
                    context.formal_trace_context for context in contexts
                )
            ),
            derived_bank_root=root,
            source_bank_root_id=resolver.index.manifest.bank_root_id,
            derivation_digest=aggregate_digest,
            repeat_worker_schedules=tuple(
                schedule
                for context in contexts
                for schedule in context.repeat_worker_schedules
            ),
        )
    exp2_plan = build_semantic_inventory(exp2_candidates)
    exp2_case_samples = {
        (candidate.case_record_digest, candidate.sample_slot_index)
        for candidate in exp2_candidates
    }
    candidate_by_slot: dict[str, Any] = {}
    for candidate in exp2_candidates:
        from tokenshare.experiments.paper_response_bank import semantic_slot_key

        prepared = candidate.prepared_request
        slot = semantic_slot_key(
            case_record_digest=candidate.case_record_digest,
            planned_ai_unit_id=candidate.planned_ai_unit_id,
            sample_slot_index=candidate.sample_slot_index,
            replacement_slot=candidate.replacement_slot,
            provider_config_digest=prepared.provider_config_digest,
            prompt_profile_digest=candidate.prompt_profile_digest,
            prompt_admission_profile_digest=(
                prepared.prompt_admission_profile_digest
            ),
            plugin_version=prepared.plugin_version,
        )
        previous = candidate_by_slot.setdefault(slot, candidate)
        if previous.prepared_request != candidate.prepared_request:
            raise ValueError("Exp2 target semantic slot request drifted")
    source_rows_by_case_sample: dict[tuple[str, int], list[Any]] = {}
    source_entry_by_inventory = {
        entry.inventory_entry_id: entry for entry in resolver.index.entries
    }
    source_evidence: dict[str, dict[str, Any]] = {}
    for row in resolver.index.inventory_rows:
        case_sample = (row.case_record_digest, row.sample_slot_index)
        if case_sample not in exp2_case_samples:
            continue
        entry = source_entry_by_inventory.get(row.inventory_entry_id)
        if entry is None:
            raise ValueError("Exp1 source entry is missing")
        objects = _entry_objects(resolver, entry)
        request_meta = _factor_request_metadata(objects["request_body"])
        parsed = {role: _json_object_bytes(data, role) for role, data in objects.items()}
        attempt_id = str(
            parsed["acquisition_attempt"].get("attempt_id")
            or entry.acquisition_state_ref
        )
        usage_body = parsed["usage"]
        usage = usage_body.get("usage")
        if usage is None:
            input_tokens = output_tokens = total_tokens = None
        elif isinstance(usage, Mapping):
            values = (
                usage.get("prompt_tokens"),
                usage.get("completion_tokens"),
                usage.get("total_tokens"),
            )
            if any(
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < 0
                for value in values
            ):
                raise ValueError("Exp1 source token accounting is invalid")
            input_tokens, output_tokens, total_tokens = values
            if total_tokens != input_tokens + output_tokens:
                raise ValueError("Exp1 source token accounting drifted")
        else:
            raise ValueError("Exp1 source token evidence must be an object or null")
        latency_ms = parsed["latency"].get("latency_ms")
        if latency_ms is not None and (
            isinstance(latency_ms, bool)
            or not isinstance(latency_ms, int)
            or latency_ms < 0
        ):
            raise ValueError("Exp1 source API latency is invalid")
        cost_cny = (
            None
            if input_tokens is None or output_tokens is None
            else _source_cost_cny(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                pricing=parsed["pricing"],
            )
        )
        attempt_index = int(
            parsed["acquisition_attempt"].get(
                "attempt_index", row.replacement_slot
            )
        )
        if attempt_index != row.replacement_slot:
            raise ValueError("Exp1 source attempt order drifted")
        source_evidence[row.inventory_entry_id] = {
            "row": row,
            "entry": entry,
            "objects": objects,
            "parsed": parsed,
            "request_meta": request_meta,
            "attempt_id": attempt_id,
            "attempt_index": attempt_index,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
            "latency_ms": latency_ms,
            "cost_cny": cost_cny,
        }
        source_rows_by_case_sample.setdefault(case_sample, []).append(row)

    source_attempt_ids = [
        evidence["attempt_id"] for evidence in source_evidence.values()
    ]
    if len(set(source_attempt_ids)) != len(source_attempt_ids):
        raise ValueError("Exp1 source attempt_id is not globally unique")
    if source_attempt_history_authority is not None:
        if type(source_attempt_history_authority) is not Exp1AttemptHistoryAuthority:
            raise TypeError("typed Exp1 attempt history authority is required")
        authority_by_entry = {
            attempt.source_entry_id: attempt
            for question in source_attempt_history_authority.trace_bank.questions
            if (question.case_record_digest, question.sample_slot_index)
            in exp2_case_samples
            for node in question.nodes
            for attempt in node.attempts
        }
        if set(authority_by_entry) != {
            evidence["entry"].entry_id for evidence in source_evidence.values()
        }:
            raise ValueError("Exp2 source attempt authority coverage drifted")
        for evidence in source_evidence.values():
            source = authority_by_entry[evidence["entry"].entry_id]
            row = evidence["row"]
            if (
                source.source_acquisition_attempt_id != evidence["attempt_id"]
                or source.source_sample_slot_index != row.sample_slot_index
                or source.source_replacement_slot != row.replacement_slot
                or source.input_tokens != evidence["input_tokens"]
                or source.output_tokens != evidence["output_tokens"]
                or source.total_tokens != evidence["total_tokens"]
                or source.api_latency_ms != evidence["latency_ms"]
                or source.cost_cny != evidence["cost_cny"]
            ):
                raise ValueError("Exp2 source attempt authority accounting drifted")
            if (
                source.request_artifact_digest
                != next(
                    locator.object_digest
                    for locator in evidence["entry"].object_locators
                    if locator.object_role == "request_body"
                )
                or source.response_artifact_digest
                != next(
                    locator.object_digest
                    for locator in evidence["entry"].object_locators
                    if locator.object_role in {"raw_output", "provider_failure"}
                )
            ):
                raise ValueError("Exp2 source attempt artifact identity drifted")
            evidence["authority_attempt"] = source
    for (case_digest, sample_slot_index), source_rows in (
        source_rows_by_case_sample.items()
    ):
        rows_by_unit: dict[str, list[Any]] = {}
        for row in source_rows:
            rows_by_unit.setdefault(row.planned_ai_unit_id, []).append(row)
        for unit_rows in rows_by_unit.values():
            ordered_slots = tuple(
                row.replacement_slot
                for row in sorted(unit_rows, key=lambda item: item.replacement_slot)
            )
            if ordered_slots != tuple(range(len(ordered_slots))):
                raise ValueError(
                "canonical Exp1 attempt history is missing or out of order: "
                f"{case_digest}@{sample_slot_index}"
                )

    target_info: dict[str, dict[str, Any]] = {}
    for row in exp2_plan.rows:
        candidate = candidate_by_slot.get(row.semantic_slot_key)
        if candidate is None:
            raise ValueError("Exp2 target candidate is missing")
        target_info[row.inventory_entry_id] = {
            "row": row,
            "candidate": candidate,
            "request_meta": _factor_request_metadata(
                candidate.prepared_request.body_bytes
            ),
            "allocations": [],
        }

    for (case_digest, sample_slot_index), source_rows in (
        source_rows_by_case_sample.items()
    ):
        case_targets = [
            info
            for info in target_info.values()
            if info["row"].case_record_digest == case_digest
            and info["row"].sample_slot_index == sample_slot_index
        ]
        for source_row in sorted(
            source_rows,
            key=lambda row: (
                source_evidence[row.inventory_entry_id]["request_meta"]["range_start"],
                source_evidence[row.inventory_entry_id]["request_meta"]["range_end"],
                row.planned_ai_unit_id,
                row.replacement_slot,
            ),
        ):
            evidence = source_evidence[source_row.inventory_entry_id]
            source_meta = evidence["request_meta"]
            overlaps = [
                (
                    info,
                    max(source_meta["range_start"], info["request_meta"]["range_start"]),
                    min(source_meta["range_end"], info["request_meta"]["range_end"]),
                )
                for info in case_targets
                if max(source_meta["range_start"], info["request_meta"]["range_start"])
                <= min(source_meta["range_end"], info["request_meta"]["range_end"])
            ]
            weights = tuple(end - start + 1 for _, start, end in overlaps)
            if not overlaps or sum(weights) != source_meta["range_end"] - source_meta["range_start"] + 1:
                raise ValueError("Exp2 target shards do not conserve an Exp1 interval")
            input_parts = _optional_largest_remainder(
                evidence["input_tokens"], weights
            )
            output_parts = _optional_largest_remainder(
                evidence["output_tokens"], weights
            )
            latency_parts = _optional_largest_remainder(
                evidence["latency_ms"], weights
            )
            cost_units = (
                None
                if evidence["cost_cny"] is None
                else int(
                    (evidence["cost_cny"] / _COST_QUANTUM).to_integral_exact(
                        rounding=ROUND_DOWN
                    )
                )
            )
            cost_parts = _optional_largest_remainder(cost_units, weights)
            coverage_denominator = sum(weights)
            for index, (info, start, end) in enumerate(overlaps):
                cost_part = cost_parts[index]
                info["allocations"].append(
                    {
                        "source": evidence,
                        "overlap_start": start,
                        "overlap_end": end,
                        "input_tokens": input_parts[index],
                        "output_tokens": output_parts[index],
                        "latency_ms": latency_parts[index],
                        "cost_cny": (
                            None
                            if cost_part is None
                            else Decimal(cost_part) * _COST_QUANTUM
                        ),
                        "coverage_numerator": weights[index],
                        "coverage_denominator": coverage_denominator,
                    }
                )

    source_manifest = resolver.index.manifest
    source_authorization = _source_manifest_authorization(source_manifest)
    manifest_fields = dict(
        bank_root_id=canonical_digest(
            {
                "kind": "exp2_projected_from_exp1",
                "source_manifest_digest": resolver.index.manifest.manifest_digest,
                "target_inventory_digest": exp2_plan.inventory_digest,
                "condition_id": condition_ids[0],
                "formula": _ALLOCATION_FORMULA,
            }
        ),
        profile_digest=canonical_digest(
            {
                "source_manifest_digest": resolver.index.manifest.manifest_digest,
                "target_coverage_digest": replay_semantic_authority.coverage_digest,
            }
        ),
        budget_digest=resolver.index.manifest.budget_digest,
        inventory_digest=exp2_plan.inventory_digest,
        provider_config_digest=resolver.index.manifest.provider_config_digest,
        entry_ids=tuple(row.entry_id for row in exp2_plan.rows),
        object_role_schema=OBJECT_ROLES,
        terminal_entry_count=len(exp2_plan.rows),
    )
    if type(source_manifest) is ResponseBankManifest:
        manifest = ResponseBankManifest.create(
            **manifest_fields,
            created_by_paid_receipt_digest=source_authorization["receipt_digest"],
        )
    elif type(source_manifest) is ResultsFirstResponseBankManifest:
        manifest = ResultsFirstResponseBankManifest.create(
            **manifest_fields,
            authorization_kind=source_authorization["authorization_kind"],
            authorization_digest=source_authorization["authorization_digest"],
        )
    else:
        raise TypeError("exact response bank manifest is required")
    logical_shards: dict[tuple[str, int, str, int, str], dict[str, Any]] = {}
    for row in exp2_plan.rows:
        info = target_info[row.inventory_entry_id]
        candidate = info["candidate"]
        allocations = tuple(info["allocations"])
        key = (
            candidate.condition_id,
            candidate.repeat_id,
            candidate.case_record_digest,
            candidate.sample_slot_index,
            candidate.planned_ai_unit_id,
        )
        allocation_summary = _optional_resource_summary(
            token_values=tuple(
                None
                if item["input_tokens"] is None
                or item["output_tokens"] is None
                else item["input_tokens"] + item["output_tokens"]
                for item in allocations
            ),
            latency_values=tuple(item["latency_ms"] for item in allocations),
            cost_values=tuple(item["cost_cny"] for item in allocations),
        )
        shard = {
            "condition_id": candidate.condition_id,
            "repeat_id": candidate.repeat_id,
            "case_id": candidate.case_id,
            "case_record_digest": candidate.case_record_digest,
            "sample_slot_index": candidate.sample_slot_index,
            "planned_ai_unit_id": candidate.planned_ai_unit_id,
            "worker_count": candidate.worker_count,
            "interval_start": info["request_meta"]["range_start"],
            "interval_end": info["request_meta"]["range_end"],
            **allocation_summary,
            "inherited_source_api_latency_ms": allocation_summary[
                "source_api_latency_total_ms"
            ],
            "source_attempt_refs": [
                {
                    "source_attempt_id": item["source"]["attempt_id"],
                    "source_entry_id": item["source"]["entry"].entry_id,
                    "source_response_artifact_digest": next(
                        locator.object_digest
                        for locator in item["source"]["entry"].object_locators
                        if locator.object_role in {"raw_output", "provider_failure"}
                    ),
                    "overlap_start": item["overlap_start"],
                    "overlap_end": item["overlap_end"],
                    "allocated_api_latency_ms": item["latency_ms"],
                    "coverage_numerator": item["coverage_numerator"],
                    "coverage_denominator": item["coverage_denominator"],
                    "unknown_resource_fields": [
                        name
                        for name, value in (
                            (
                                "tokens",
                                None
                                if item["input_tokens"] is None
                                or item["output_tokens"] is None
                                else item["input_tokens"]
                                + item["output_tokens"],
                            ),
                            ("api_latency", item["latency_ms"]),
                            ("cost", item["cost_cny"]),
                        )
                        if value is None
                    ],
                }
                for item in allocations
            ],
        }
        previous = logical_shards.setdefault(key, shard)
        if previous != shard:
            raise ValueError("Exp2 replacement rows disagree on logical shard resources")

    schedules_by_shard: dict[
        tuple[str, int, str, int, str], Mapping[str, object]
    ] = {}
    repeat_worker_schedules: list[Mapping[str, object]] = []
    schedule_groups: dict[tuple[str, int, str, int], list[dict[str, Any]]] = {}
    for key, shard in logical_shards.items():
        schedule_groups.setdefault(key[:4], []).append(shard)
    for group_key, shards in sorted(schedule_groups.items()):
        condition_id, repeat_id, case_record_digest, sample_slot_index = group_key
        worker_counts = {int(shard["worker_count"]) for shard in shards}
        if len(worker_counts) != 1:
            raise ValueError("Exp2 worker count drifted within one repeat/root")
        worker_count = next(iter(worker_counts))
        if worker_count <= 0:
            raise ValueError("Exp2 worker count must be positive")
        worker_completion: list[int | None] = [0 for _ in range(worker_count)]
        scheduled_shards: list[Mapping[str, object]] = []
        for shard in sorted(
            shards,
            key=lambda item: (
                item["interval_start"],
                item["interval_end"],
                item["planned_ai_unit_id"],
            ),
        ):
            worker_index = min(
                range(worker_count),
                key=lambda index: (
                    worker_completion[index] is None,
                    0
                    if worker_completion[index] is None
                    else worker_completion[index],
                    index,
                ),
            )
            queue_start = worker_completion[worker_index]
            source_latency = shard["inherited_source_api_latency_ms"]
            if source_latency is not None and (
                isinstance(source_latency, bool)
                or not isinstance(source_latency, int)
                or source_latency < 0
            ):
                raise ValueError("Exp2 inherited source latency is invalid")
            completion = (
                None
                if queue_start is None or source_latency is None
                else queue_start + source_latency
            )
            worker_completion[worker_index] = completion
            scheduled = {
                **shard,
                "worker_index": worker_index,
                "current_protocol_scheduling_latency_ms": queue_start,
                "injected_fault_or_recovery_delay_ms": 0,
                "derived_total_latency_ms": completion,
                "derived_total_latency_components": {
                    "inherited_source_api_latency_ms": source_latency,
                    "current_protocol_scheduling_latency_ms": queue_start,
                    "injected_fault_or_recovery_delay_ms": 0,
                    "source_api_latency_missing_attempt_count": shard[
                        "source_api_latency_missing_attempt_count"
                    ],
                },
                "formula": "interval_overlap_then_serial_worker_critical_path.v1",
            }
            scheduled_shards.append(scheduled)
            schedules_by_shard[
                (
                    condition_id,
                    int(repeat_id),
                    str(case_record_digest),
                    int(sample_slot_index),
                    str(shard["planned_ai_unit_id"]),
                )
            ] = scheduled
        repeat_worker_schedules.append(
            {
                "condition_id": condition_id,
                "repeat_id": repeat_id,
                "case_id": scheduled_shards[0]["case_id"],
                "case_record_digest": case_record_digest,
                "sample_slot_index": sample_slot_index,
                "worker_count": worker_count,
                "worker_completion_ms": worker_completion,
                "experiment_makespan_ms": (
                    None
                    if any(value is None for value in worker_completion)
                    else max(worker_completion)
                ),
                "target_shards": scheduled_shards,
                "formula": "interval_overlap_then_serial_worker_critical_path.v1",
                "current_provider_calls": 0,
            }
        )

    entries: list[Any] = []
    object_bytes: dict[str, bytes] = {}
    for row in exp2_plan.rows:
        info = target_info[row.inventory_entry_id]
        allocations = sorted(
            info["allocations"],
            key=lambda item: (
                item["source"]["request_meta"]["range_start"],
                item["source"]["request_meta"]["range_end"],
                item["source"]["row"].planned_ai_unit_id,
                item["source"]["row"].replacement_slot,
            ),
        )
        if not allocations:
            raise ValueError("Exp2 target shard has no Exp1 source evidence")
        source_refs = [
            {
                "source_attempt_id": item["source"]["attempt_id"],
                "source_acquisition_attempt_id": item["source"]["attempt_id"],
                "source_entry_id": item["source"]["entry"].entry_id,
                "source_sample_slot_index": (
                    item["source"]["row"].sample_slot_index
                ),
                "source_replacement_slot": (
                    item["source"]["row"].replacement_slot
                ),
                "replacement_slot": item["source"]["row"].replacement_slot,
                "source_inventory_entry_id": item["source"]["row"].inventory_entry_id,
                "source_attempt_index": item["source"]["attempt_index"],
                "source_request_artifact_digest": next(
                    locator.object_digest
                    for locator in item["source"]["entry"].object_locators
                    if locator.object_role == "request_body"
                ),
                "source_request_artifact_ref": (
                    item["source"]["authority_attempt"].request_artifact_ref
                    if "authority_attempt" in item["source"]
                    else None
                ),
                "source_response_artifact_digest": next(
                    locator.object_digest
                    for locator in item["source"]["entry"].object_locators
                    if locator.object_role
                    in {"raw_output", "provider_failure"}
                ),
                "source_response_artifact_ref": (
                    item["source"]["authority_attempt"].response_artifact_ref
                    if "authority_attempt" in item["source"]
                    else None
                ),
                "overlap_start": item["overlap_start"],
                "overlap_end": item["overlap_end"],
                "allocated_input_tokens": item["input_tokens"],
                "allocated_output_tokens": item["output_tokens"],
                "allocated_total_tokens": (
                    None
                    if item["input_tokens"] is None
                    or item["output_tokens"] is None
                    else item["input_tokens"] + item["output_tokens"]
                ),
                "allocated_api_latency_ms": item["latency_ms"],
                "allocated_cost_cny": (
                    None
                    if item["cost_cny"] is None
                    else format(item["cost_cny"], "f")
                ),
                "coverage_numerator": item["coverage_numerator"],
                "coverage_denominator": item["coverage_denominator"],
                "unknown_resource_fields": [
                    name
                    for name, value in (
                        (
                            "tokens",
                            None
                            if item["input_tokens"] is None
                            or item["output_tokens"] is None
                            else item["input_tokens"] + item["output_tokens"],
                        ),
                        ("api_latency", item["latency_ms"]),
                        ("cost", item["cost_cny"]),
                    )
                    if value is None
                ],
                "source_ledger_ref": (
                    item["source"]["authority_attempt"].ledger_ref
                    if "authority_attempt" in item["source"]
                    else None
                ),
                "source_ledger_record_digest": (
                    item["source"]["authority_attempt"].ledger_record_digest
                    if "authority_attempt" in item["source"]
                    else None
                ),
                "formula": _ALLOCATION_FORMULA,
            }
            for item in allocations
        ]
        terminal_slot_by_unit = {
            item["source"]["row"].planned_ai_unit_id: max(
                candidate["source"]["row"].replacement_slot
                for candidate in allocations
                if candidate["source"]["row"].planned_ai_unit_id
                == item["source"]["row"].planned_ai_unit_id
            )
            for item in allocations
        }
        terminal_allocations = [
            item
            for item in allocations
            if item["source"]["row"].replacement_slot
            == terminal_slot_by_unit[item["source"]["row"].planned_ai_unit_id]
        ]
        terminal_kind = (
            "provider_failure"
            if any(
                item["source"]["entry"].terminal_kind != "success"
                for item in terminal_allocations
            )
            else "success"
        )
        terminal_role = "raw_output" if terminal_kind == "success" else "provider_failure"
        if terminal_kind == "success":
            projection_sources = []
            for item in terminal_allocations:
                raw = item["source"]["parsed"]["raw_output"]
                content_text = raw.get("content_text")
                if not isinstance(content_text, str):
                    raise ValueError("Exp1 raw output content is missing")
                projection_sources.append(
                    (
                        item["source"]["request_meta"],
                        content_text,
                        next(
                            ref["source_response_artifact_digest"]
                            for ref in source_refs
                            if ref["source_inventory_entry_id"]
                            == item["source"]["row"].inventory_entry_id
                        ),
                    )
                )
            projected_content, projection_status = _project_factor_content(
                target=info["request_meta"],
                sources=projection_sources,
            )
            terminal_body = {
                "schema_version": "tokenshare.response_bank_raw_output.v1",
                "raw_response_json": None,
                "content_text": projected_content,
                "reasoning_content": None,
                "provider_response_id": None,
                "finish_reason": "exp2_deterministic_projection",
                "resource_origin": "simulated_from_exp1_trace",
                "projection_status": projection_status,
                "source_attempt_refs": source_refs,
            }
        else:
            first_failure = next(
                item
                for item in terminal_allocations
                if item["source"]["entry"].terminal_kind != "success"
            )
            terminal_body = dict(first_failure["source"]["parsed"]["provider_failure"])
            terminal_body.update(
                {
                    "resource_origin": "simulated_from_exp1_trace",
                    "source_attempt_refs": source_refs,
                    "current_provider_calls": 0,
                }
            )
        known_input_tokens = sum(
            item["input_tokens"]
            for item in allocations
            if item["input_tokens"] is not None
        )
        known_output_tokens = sum(
            item["output_tokens"]
            for item in allocations
            if item["output_tokens"] is not None
        )
        token_missing_count = sum(
            item["input_tokens"] is None or item["output_tokens"] is None
            for item in allocations
        )
        input_tokens = None if token_missing_count else known_input_tokens
        output_tokens = None if token_missing_count else known_output_tokens
        resource_summary = _optional_resource_summary(
            token_values=tuple(
                None
                if item["input_tokens"] is None
                or item["output_tokens"] is None
                else item["input_tokens"] + item["output_tokens"]
                for item in allocations
            ),
            latency_values=tuple(item["latency_ms"] for item in allocations),
            cost_values=tuple(item["cost_cny"] for item in allocations),
        )
        latency_ms = resource_summary["source_api_latency_total_ms"]
        first_source = allocations[0]["source"]
        candidate = info["candidate"]
        prepared = candidate.prepared_request
        source_provenance = first_source["parsed"]["provenance"]
        source_acquisition = first_source["parsed"]["acquisition_attempt"]
        model_record = _json_object_bytes(
            first_source["objects"]["model_record"], "model_record"
        )
        derived_attempt_id = canonical_digest(
            {
                "target_inventory_entry_id": row.inventory_entry_id,
                "source_attempt_refs": source_refs,
            }
        )
        schedule = schedules_by_shard[
            (
                candidate.condition_id,
                candidate.repeat_id,
                candidate.case_record_digest,
                candidate.sample_slot_index,
                candidate.planned_ai_unit_id,
            )
        ]
        derived_objects = {
            "request_body": info["candidate"].prepared_request.body_bytes,
            terminal_role: _json_bytes(terminal_body),
            "provenance": _json_bytes(
                {
                    "schema_version": "tokenshare.response_bank_provenance.v1",
                    "provider_family": source_provenance["provider_family"],
                    "entry_id": prepared.entry_id,
                    "provider_config_digest": prepared.provider_config_digest,
                    "inference_request_digest": row.inference_request_digest,
                    "normalized_absolute_endpoint": (
                        prepared.normalized_absolute_endpoint
                    ),
                    "transport_call_count": 0,
                    "secret_persisted": False,
                    **source_authorization,
                    "request_body_digest": row.body_digest,
                    "configured_model": model_record["configured_model"],
                    "requested_model": model_record["requested_model"],
                    "resolved_model": model_record["resolved_model"],
                    "response_model_status": model_record[
                        "response_model_status"
                    ],
                    "source_transport": "fixed_exp1_trace_projection",
                    "resource_origin": "simulated_from_exp1_trace",
                    "current_provider_calls": 0,
                    "source_bank_root_id": resolver.index.manifest.bank_root_id,
                    "source_manifest_digest": resolver.index.manifest.manifest_digest,
                    "source_attempt_refs": source_refs,
                }
            ),
            "usage": _json_bytes(
                {
                    "schema_version": "tokenshare.response_bank_usage.v1",
                    "usage_status": (
                        "usage_missing"
                        if input_tokens is None or output_tokens is None
                        else "reported"
                    ),
                    "usage": (
                        None
                        if input_tokens is None or output_tokens is None
                        else {
                            "prompt_tokens": input_tokens,
                            "completion_tokens": output_tokens,
                            "total_tokens": input_tokens + output_tokens,
                        }
                    ),
                    **{
                        key: value
                        for key, value in resource_summary.items()
                        if key.startswith("source_tokens_")
                    },
                    "current_provider_calls": 0,
                    "source_attempt_refs": source_refs,
                }
            ),
            "latency": _json_bytes(
                {
                    "schema_version": "tokenshare.response_bank_latency.v1",
                    "latency_ms": latency_ms,
                    "timing_source": "simulated_from_exp1_trace",
                    **{
                        key: value
                        for key, value in resource_summary.items()
                        if key.startswith("source_api_latency_")
                    },
                    "source_attempt_refs": source_refs,
                    "worker_count": schedule["worker_count"],
                    "worker_index": schedule["worker_index"],
                    "current_protocol_scheduling_latency_ms": schedule[
                        "current_protocol_scheduling_latency_ms"
                    ],
                    "injected_fault_or_recovery_delay_ms": 0,
                    "derived_total_latency_ms": schedule["derived_total_latency_ms"],
                    "derived_total_latency_components": schedule[
                        "derived_total_latency_components"
                    ],
                    "experiment_makespan_ms": next(
                        item["experiment_makespan_ms"]
                        for item in repeat_worker_schedules
                        if item["condition_id"] == candidate.condition_id
                        and item["repeat_id"] == candidate.repeat_id
                        and item["case_record_digest"] == candidate.case_record_digest
                        and item["sample_slot_index"] == candidate.sample_slot_index
                    ),
                    "formula": "interval_overlap_then_serial_worker_critical_path.v1",
                }
            ),
            "pricing": _json_bytes(
                {
                    **dict(first_source["parsed"]["pricing"]),
                    "resource_origin": "simulated_from_exp1_trace",
                    "current_provider_calls": 0,
                    **{
                        key: value
                        for key, value in resource_summary.items()
                        if key.startswith("source_cost_")
                    },
                    "source_attempt_refs": source_refs,
                }
            ),
            "acquisition_attempt": _json_bytes(
                {
                    "schema_version": (
                        "tokenshare.response_bank_acquisition_attempt.v1"
                    ),
                    "attempt_id": derived_attempt_id,
                    "attempt_index": row.replacement_slot,
                    "linked_ambiguous_attempt_id": None,
                    "requested_at": source_acquisition.get("requested_at"),
                    "terminal_kind": terminal_kind,
                    "failure_kind": (
                        None
                        if terminal_kind == "success"
                        else source_acquisition.get("failure_kind")
                    ),
                    "retry_reason": None,
                    "requeue_reason": None,
                    "current_attempt_id": derived_attempt_id,
                    "current_attempt_ordinal": row.replacement_slot,
                    "resource_origin": "simulated_from_exp1_trace",
                    "current_provider_calls": 0,
                    "source_attempt_refs": source_refs,
                }
            ),
            "model_record": first_source["objects"]["model_record"],
        }
        locators = tuple(
            ExternalBankObjectLocator(
                bank_root_id=manifest.bank_root_id,
                manifest_digest=manifest.manifest_digest,
                entry_id=row.entry_id,
                object_role=role,
                object_digest=_bytes_digest(data),
            )
            for role, data in derived_objects.items()
        )
        for locator in locators:
            object_bytes[locator.object_digest] = derived_objects[locator.object_role]
        entries.append(
            ResponseBankEntry(
                inventory_digest=exp2_plan.inventory_digest,
                inventory_entry_id=row.inventory_entry_id,
                semantic_slot_key=row.semantic_slot_key,
                inference_request_digest=row.inference_request_digest,
                entry_id=row.entry_id,
                sample_slot_index=row.sample_slot_index,
                replacement_slot=row.replacement_slot,
                terminal_kind=terminal_kind,
                object_locators=locators,
                acquisition_state_ref=canonical_digest(
                    {
                        "kind": "exp2_projected_delivery",
                        "target_inventory_entry_id": row.inventory_entry_id,
                        "source_attempt_refs": source_refs,
                    }
                ),
            )
        )
    root = Path(output_root).resolve()
    derivation_digest = canonical_digest(
        {
            "source_manifest_digest": resolver.index.manifest.manifest_digest,
            "source_attempt_history_digest": (
                None
                if source_attempt_history_authority is None
                else source_attempt_history_authority.authority_digest
            ),
            "target_inventory_digest": exp2_plan.inventory_digest,
            "entries": [entry.to_dict() for entry in entries],
            "repeat_worker_schedules": repeat_worker_schedules,
        }
    )
    derivation_body = {
        "schema_version": "tokenshare.paper.exp2-projection-derivation.v1",
        "source_manifest_digest": resolver.index.manifest.manifest_digest,
        "source_attempt_history_digest": (
            None
            if source_attempt_history_authority is None
            else source_attempt_history_authority.authority_digest
        ),
        "target_inventory_digest": exp2_plan.inventory_digest,
        "entry_digests": [canonical_digest(entry.to_dict()) for entry in entries],
        "repeat_worker_schedules": repeat_worker_schedules,
        "derivation_digest": derivation_digest,
        "current_provider_calls": 0,
    }
    if root.exists():
        projected_resolver = ResponseBankResolver.open(root)
        existing_entries = {
            entry.inventory_entry_id: entry.to_dict()
            for entry in projected_resolver.index.entries
        }
        expected_entries = {
            entry.inventory_entry_id: entry.to_dict() for entry in entries
        }
        if (
            projected_resolver.index.manifest.manifest_digest
            != manifest.manifest_digest
            or projected_resolver.index.manifest.inventory_digest
            != exp2_plan.inventory_digest
            or existing_entries != expected_entries
        ):
            raise ValueError("existing Exp2 projection bank identity drifted")
        for expected_entry in entries:
            existing_entry = next(
                entry
                for entry in projected_resolver.index.entries
                if entry.entry_id == expected_entry.entry_id
            )
            for locator in existing_entry.object_locators:
                if projected_resolver.read_verified(locator) != object_bytes[
                    locator.object_digest
                ]:
                    raise ValueError("existing Exp2 projection object drifted")
    else:
        initialize_response_bank(
            root,
            manifest=manifest,
            inventory_rows=exp2_plan.rows,
            entries=entries,
            objects=object_bytes,
        )
        projected_resolver = ResponseBankResolver.open(root)
    _persist_exact_json_once(
        root / "exp2_projection_derivation.v1.json",
        derivation_body,
    )
    formal_context = build_paper_formal_trace_context(
        inventory_plan=exp2_plan,
        resolver=projected_resolver,
    )
    return Exp2ProjectedTraceContext(
        formal_trace_context=formal_context,
        derived_bank_root=root,
        source_bank_root_id=resolver.index.manifest.bank_root_id,
        derivation_digest=derivation_digest,
        repeat_worker_schedules=tuple(repeat_worker_schedules),
    )
