"""确定性生成 Factorization 500-root 正式 paper catalog。"""

from __future__ import annotations

import argparse
import json
import random
from collections.abc import Mapping, Sequence
from hashlib import sha256
from pathlib import Path
from typing import Any

from tokenshare.experiments.paper_models import JsonObject


CATALOG_GENERATOR_VERSION = "tokenshare.paper_factorization_catalog.v2"
DEFAULT_CATALOG_COUNT = 500
DEFAULT_CATALOG_SEED = 20260720
MIN_TARGET_N = 1_000_000
MAX_TARGET_N_EXCLUSIVE = 100_000_000_000

_DIFFICULTIES = ("easy", "medium", "hard")
_POSITION_NAMES = ("early", "middle", "late")
_MAGNITUDE_EXPONENTS = (6, 7, 8, 9, 10)
_DIFFICULTY_PROFILES: dict[str, JsonObject] = {
    "easy": {
        "minimum_candidate_count": 8,
        "maximum_candidate_count": 32,
        "requested_child_count": 2,
    },
    "medium": {
        "minimum_candidate_count": 33,
        "maximum_candidate_count": 128,
        "requested_child_count": 4,
    },
    "hard": {
        "minimum_candidate_count": 129,
        "maximum_candidate_count": 512,
        "requested_child_count": 8,
    },
}
_MILLER_RABIN_BASES_64 = (2, 325, 9375, 28178, 450775, 9780504, 1795265022)


def generate_factorization_paper_cases(
    *,
    count: int = DEFAULT_CATALOG_COUNT,
    seed: int = DEFAULT_CATALOG_SEED,
) -> tuple[JsonObject, ...]:
    """生成稳定、有 oracle、可做 range split 的正式 Factorization roots。"""

    _require_positive_int("count", count)
    _require_non_negative_int("seed", seed)
    difficulty_counts = _difficulty_counts(count)
    cases: list[JsonObject] = []
    used_targets: set[int] = set()
    ordinal = 0
    for difficulty in _DIFFICULTIES:
        difficulty_count = difficulty_counts[difficulty]
        no_factor_count = min(7, difficulty_count // 20) if difficulty == "hard" else 0
        factor_case_count = difficulty_count - no_factor_count
        for local_index in range(difficulty_count):
            magnitude_exponent = _MAGNITUDE_EXPONENTS[local_index % len(_MAGNITUDE_EXPONENTS)]
            position = (
                "no_factor"
                if local_index >= factor_case_count
                else _POSITION_NAMES[local_index % len(_POSITION_NAMES)]
            )
            case = _generate_case(
                difficulty=difficulty,
                local_index=local_index,
                ordinal=ordinal,
                magnitude_exponent=magnitude_exponent,
                position=position,
                seed=seed,
                used_targets=used_targets,
            )
            cases.append(case)
            used_targets.add(int(case["target_n"]))
            ordinal += 1
    return tuple(cases)


def catalog_jsonl_text(cases: Sequence[Mapping[str, Any]]) -> str:
    """把 catalog 写成 canonical compact JSONL。"""

    return "".join(
        json.dumps(dict(case), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
        for case in cases
    )


def write_factorization_paper_catalog(
    *,
    output_path: str | Path,
    count: int = DEFAULT_CATALOG_COUNT,
    seed: int = DEFAULT_CATALOG_SEED,
) -> str:
    """生成并持久化 JSONL，返回文件内容 digest。"""

    text = catalog_jsonl_text(generate_factorization_paper_cases(count=count, seed=seed))
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return f"sha256:{sha256(text.encode('utf-8')).hexdigest()}"


def is_prime_64(value: int) -> bool:
    """确定性判断 unsigned 64-bit 整数是否为质数。"""

    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("value must be an integer")
    if value < 2:
        return False
    small_primes = (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37)
    if value in small_primes:
        return True
    if any(value % prime == 0 for prime in small_primes):
        return False

    odd_part = value - 1
    power_of_two = 0
    while odd_part % 2 == 0:
        power_of_two += 1
        odd_part //= 2
    for base in _MILLER_RABIN_BASES_64:
        if base % value == 0:
            continue
        witness = pow(base, odd_part, value)
        if witness in (1, value - 1):
            continue
        for _ in range(power_of_two - 1):
            witness = pow(witness, 2, value)
            if witness == value - 1:
                break
        else:
            return False
    return True


def _difficulty_counts(count: int) -> dict[str, int]:
    base, remainder = divmod(count, len(_DIFFICULTIES))
    return {
        difficulty: base + int(index < remainder)
        for index, difficulty in enumerate(_DIFFICULTIES)
    }


def _generate_case(
    *,
    difficulty: str,
    local_index: int,
    ordinal: int,
    magnitude_exponent: int,
    position: str,
    seed: int,
    used_targets: set[int],
) -> JsonObject:
    profile = _DIFFICULTY_PROFILES[difficulty]
    candidate_count, factor = _candidate_profile(
        difficulty=difficulty,
        local_index=local_index,
        seed=seed,
        position=position,
    )
    candidate_start = 2
    candidate_end = candidate_start + candidate_count - 1
    rng = random.Random(seed * 1_000_003 + ordinal * 97_409 + 17)
    lower = 10**magnitude_exponent
    upper = 10 ** (magnitude_exponent + 1)
    desired = rng.randrange(lower + (upper - lower) // 10, upper - (upper - lower) // 10)
    if position == "no_factor":
        target_n = _prime_in_interval(
            desired=desired,
            lower=lower,
            upper=upper,
            forbidden=used_targets,
        )
        oracle = [{"prime": str(target_n), "exponent": 1}]
    else:
        if factor is None:
            raise AssertionError("factor case requires an in-range factor")
        large_factor = _large_prime_factor(
            desired=desired,
            small_factor=factor,
            lower=lower,
            upper=upper,
            forbidden_targets=used_targets,
        )
        target_n = factor * large_factor
        oracle = [
            {"prime": str(factor), "exponent": 1},
            {"prime": str(large_factor), "exponent": 1},
        ]
    if not MIN_TARGET_N <= target_n < MAX_TARGET_N_EXCLUSIVE:
        raise AssertionError("generated target is outside the frozen range")
    requested_child_count = int(profile["requested_child_count"])
    return {
        "candidate_divisor_count": candidate_count,
        "candidate_end": str(candidate_end),
        "candidate_start": str(candidate_start),
        "case_id": f"factor_v2_{difficulty}_{local_index + 1:03d}",
        "catalog_ordinal": ordinal,
        "difficulty": difficulty,
        "factor_position_quantile": position,
        "generator_version": CATALOG_GENERATOR_VERSION,
        "oracle_prime_factors": oracle,
        "paper_difficulty": difficulty,
        "schema_version": "tokenshare.paper_factorization_case.v1",
        "source_seed": seed + ordinal,
        "split_params": {
            "range_policy": "contiguous",
            "requested_child_count": requested_child_count,
            "strategy_id": "factorization.candidate_range_partition.v1",
        },
        "target_n": str(target_n),
    }


def _candidate_profile(
    *,
    difficulty: str,
    local_index: int,
    seed: int,
    position: str,
) -> tuple[int, int | None]:
    profile = _DIFFICULTY_PROFILES[difficulty]
    minimum = int(profile["minimum_candidate_count"])
    maximum = int(profile["maximum_candidate_count"])
    span = maximum - minimum + 1
    proposed = minimum + ((local_index * 37 + seed) % span)
    if position == "no_factor":
        return proposed, None
    for offset in range(span):
        candidate_count = minimum + ((proposed - minimum + offset) % span)
        candidate_end = candidate_count + 1
        primes = [
            prime
            for prime in _small_primes(candidate_end)
            if _factor_position(prime, start=2, end=candidate_end) == position
        ]
        if primes:
            return candidate_count, primes[local_index % len(primes)]
    raise AssertionError(f"no {position} prime is available for {difficulty}")


def _factor_position(factor: int, *, start: int, end: int) -> str:
    ratio = (factor - start) / (end - start)
    return "early" if ratio <= 1 / 3 else "middle" if ratio <= 2 / 3 else "late"


def _small_primes(maximum: int) -> tuple[int, ...]:
    return tuple(value for value in range(2, maximum + 1) if is_prime_64(value))


def _large_prime_factor(
    *,
    desired: int,
    small_factor: int,
    lower: int,
    upper: int,
    forbidden_targets: set[int],
) -> int:
    minimum_factor = max(small_factor + 1, (lower + small_factor - 1) // small_factor)
    maximum_factor = (upper - 1) // small_factor
    candidate = max(minimum_factor, desired // small_factor)
    prime = _next_prime(candidate)
    if prime > maximum_factor:
        prime = _previous_prime(maximum_factor)
    while prime >= minimum_factor:
        target_n = small_factor * prime
        if target_n not in forbidden_targets and prime != small_factor:
            return prime
        prime = _previous_prime(prime - 1)
    raise ValueError("unable to generate a unique semiprime in magnitude interval")


def _prime_in_interval(
    *,
    desired: int,
    lower: int,
    upper: int,
    forbidden: set[int],
) -> int:
    prime = _next_prime(max(lower, desired))
    if prime >= upper:
        prime = _previous_prime(upper - 1)
    while prime >= lower:
        if prime not in forbidden:
            return prime
        prime = _previous_prime(prime - 1)
    raise ValueError("unable to generate a unique prime in magnitude interval")


def _next_prime(value: int) -> int:
    candidate = max(2, value)
    if candidate == 2:
        return 2
    if candidate % 2 == 0:
        candidate += 1
    while not is_prime_64(candidate):
        candidate += 2
    return candidate


def _previous_prime(value: int) -> int:
    if value < 2:
        raise ValueError("no prime exists below requested value")
    candidate = value if value % 2 else value - 1
    if value == 2:
        return 2
    while candidate >= 3 and not is_prime_64(candidate):
        candidate -= 2
    if candidate >= 2:
        return candidate
    raise ValueError("no prime exists below requested value")


def _require_positive_int(field_name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{field_name} must be an integer >= 1")


def _require_non_negative_int(field_name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field_name} must be an integer >= 0")


def _main() -> int:
    parser = argparse.ArgumentParser(description="Generate the frozen paper Factorization catalog.")
    parser.add_argument("--output", required=True)
    parser.add_argument("--count", type=int, default=DEFAULT_CATALOG_COUNT)
    parser.add_argument("--seed", type=int, default=DEFAULT_CATALOG_SEED)
    args = parser.parse_args()
    digest = write_factorization_paper_catalog(
        output_path=args.output,
        count=args.count,
        seed=args.seed,
    )
    print(digest)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())


__all__ = [
    "CATALOG_GENERATOR_VERSION",
    "DEFAULT_CATALOG_COUNT",
    "DEFAULT_CATALOG_SEED",
    "catalog_jsonl_text",
    "generate_factorization_paper_cases",
    "is_prime_64",
    "write_factorization_paper_catalog",
]
