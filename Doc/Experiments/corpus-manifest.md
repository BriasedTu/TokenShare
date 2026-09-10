# Authoritative corpus manifest

`benchmarks/experiments/manifest.v1.json` 是公开 corpus 的机器可读权威，绑定正式 catalog、provider config、Lean sidecar、fixture 和 miniF2F 证明证据。本文件是人工可读摘要。

## Verification entry

```powershell
$repo = (Get-Location).Path
$env:PYTHONPATH = "$repo\src;$repo"
python verification/verify_authoritative_corpus.py
```

成功输出应报告：

```text
authoritative corpus ok: assets=20 root_identities=6912 root_identity_sha=600d29b146d7324ae09dd2ce2c227d64ad90ff8a0ba5eb9db0eab98eae1b352e reference_identities=104 reference_identity_sha=0478c5eaa96a35571f1c942da84e090a58af3323dc7ed961bbd603e4a812750e
```

## Frozen source identity

- tag object SHA：`018ac5c4a960ea10838a79287221e62fad7b1ac7`
- peeled frozen commit：`bb5e637785afb6bd5743e4d89af4c02ab0736204`

`benchmarks/paper/**` 和 `fixtures/lean_proof_project/**` 仅作为 frozen Git object source paths 记录在 manifest 中；公开运行时使用 `benchmarks/experiments/**` 与 `configs/experiments/**`。

## Migrated assets

| Role | Public path | Records | Bytes | SHA-256 |
|---|---|---:|---:|---|
| factorization_catalog | `benchmarks/experiments/factorization_catalog.v2.jsonl` | 500 | 307,387 | `9ce2b31a199455a37c0ca5afdee68e03540dc4912c4c4fe28e87ed3503467774` |
| lean_direct_catalog | `benchmarks/experiments/lean_catalog.v1.jsonl` | 30 | 20,855 | `1b2b709ce459d1c72966c708bf6fabfd6a747842fdbd853f2b0d42b74a3052c3` |
| lean_checker_preflight | `benchmarks/experiments/lean_checker_preflight.v1.json` | 600 | 672,477 | `3b8f0597466e4ed1836719f133a1698c75c9b063c6f60658720d9e6358c323c7` |
| lean_environment_semantic_authority | `benchmarks/experiments/lean_environment_semantic_authority.v1.json` | — | 3,396 | `b5bfb38160086bbbd3266da7f82a9c1f7dea51f4ae34ab4cbc91b32c74b29f13` |
| lean_lemma_graph_catalog | `benchmarks/experiments/lean_lemma_graph_catalog.v1.jsonl` | 165 | 1,078,709 | `5a134f246d45ad302ead57ef6eb9559b0b040ed85fac50dc6af49974756f2cdc` |
| exp1_provider_config | `configs/experiments/exp1_baseline_provider_config.v3.json` | 1 | 1,897 | `12a958510d1204738c162ad0e6754e550e41dce507dc796977c86da351bab30c` |
| exp5_provider_config | `configs/experiments/exp5_siliconflow_provider_config.v3.json` | 3 | 3,240 | `4760850bc7929dd97d8c1fe6d811359aee4c6f3e663f49faf532b0e6a0481af3` |

## Lean fixture blobs

| Public path | Bytes | SHA-256 |
|---|---:|---|
| `benchmarks/experiments/fixtures/lean_proof_project/lake-manifest.json` | 120 | `1b07ce0a9898b379578390d24abf183a3c319bdb4b076c18ea31edf1daf2e869` |
| `benchmarks/experiments/fixtures/lean_proof_project/lakefile.lean` | 102 | `288f5b67bd53c82742d276e5ed67714279bdce920c54a6986c1b4a7ef5032d39` |
| `benchmarks/experiments/fixtures/lean_proof_project/lean-toolchain` | 24 | `61561b06f5587e027815fac8f59bf6ca160dfbf3f7372fc6297664acfd85b8c0` |
| `benchmarks/experiments/fixtures/lean_proof_project/TokenShare.lean` | 247 | `86a6496322cc4049f9f443b19399766e2dd450e0f0f3d9b21858487b4e40786d` |
| `benchmarks/experiments/fixtures/lean_proof_project/TokenShare/Helper.lean` | 158 | `9e98398cb779e5fc5b55cd0a8e521a56f58475285ef6fcd977d73fc62238b02c` |
| `benchmarks/experiments/fixtures/lean_proof_project/TokenShare/LemmaGraphCases.lean` | 3,293 | `f015a29cd0eb90854a5b247a52c420a4885287c20c397f11f2490f1a5c857784` |
| `benchmarks/experiments/fixtures/lean_proof_project/TokenShare/LemmaGraphOracle.lean` | 3,677 | `4af791228bd24f4ea6d92fed511be8eee4f29d1ba8db5c6102968e743a8047ca` |
| `benchmarks/experiments/fixtures/lean_proof_project/TokenShare/Merge.lean` | 107 | `f149dc0171a55156891ecd415fc3889e9030e2b81ce53697b1e1c54a51252fc7` |
| `benchmarks/experiments/fixtures/lean_proof_project/TokenShare/SplitRules.lean` | 4,891 | `6716f703905abe0b230215ac20886eb96b8e929aec27fb9c78265a7450aee5c1` |
| `benchmarks/experiments/fixtures/lean_proof_project/TokenShare/Fixtures/Decomposition.lean` | 180 | `c974b8649b3eadf4738d39e3d852e8a958a5e75e2d751da01759e82cb2f919e0` |
| `benchmarks/experiments/fixtures/lean_proof_project/TokenShare/Fixtures/Direct.lean` | 119 | `06378524b81f22aa3ec652e96cc970be076aa390ab2ed6ada7f66f91013d8653` |
| `benchmarks/experiments/fixtures/lean_proof_project/TokenShare/Fixtures/Invalid.lean` | 129 | `45393c97a1a61a4dd5607da8bbfb73690657b9f1ba61e07d39180a581cfcde85` |
| `benchmarks/experiments/fixtures/lean_proof_project/TokenShare/Fixtures/Unsupported.lean` | 221 | `a6e79e5563ef4dccf0d7985e941c247d70e6e418ba0ba0bbc65785b33ac0619b` |

## Static identity checks

The verifier recomputes full inventory from public corpus/config and compares exact canonical identity sets:

| Identity set | Count | Canonical byte length | SHA-256 |
|---|---:|---:|---|
| full root identities | 6,912 | 1,427,265 | `600d29b146d7324ae09dd2ce2c227d64ad90ff8a0ba5eb9db0eab98eae1b352e` |
| full reference identities | 104 | 21,647 | `0478c5eaa96a35571f1c942da84e090a58af3323dc7ed961bbd603e4a812750e` |

No provider call is needed for these checks.

## miniF2F supplement

The separate Experiment 1 supplement binds 81 ordered root identities, 223 ordered
node identities, and 672 file SHA-256 values. These include the catalog, pinned
fixture, original statements, proof packages, independent reviews, admission
dispositions, and recorded compiler inputs and outputs. They are reproducibility
assets, not disposable test output.

The current full-profile identity table above remains separate from the supplement.
See [the supplement guide](minif2f-supplement.md) for admission requirements.
