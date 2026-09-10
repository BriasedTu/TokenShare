import json

import pytest

from tokenshare.experiments import profiles
from tokenshare.experiments.cli import _parser
from tokenshare.experiments.schema import ExperimentRunConfigV1, SchemaValidationError


def test_supplement_inventory_only_enumerates_exp1_and_derives_unit_counts(tmp_path, monkeypatch):
    source = profiles._LEAN_CATALOG
    row = json.loads(source.read_text(encoding='utf-8').splitlines()[0])
    row['case_id'] = 'minif2f_test_example'
    catalog = tmp_path / 'supplement.jsonl'
    catalog.write_text(json.dumps(row) + '\n', encoding='utf-8')
    monkeypatch.setattr(profiles, '_MINIF2F_CATALOG', catalog, raising=False)
    inventory = profiles.build_inventory('minif2f')
    assert [root.case_id for root in inventory.roots] == ['minif2f_test_example']
    assert {root.experiment_id for root in inventory.roots} == {'exp1'}
    assert inventory.references == inventory.challenges == ()
    assert profiles.build_plan('minif2f').experiments['exp1'].planned_first_attempt_ai_units == row['expected_ai_unit_count']
    assert profiles.downstream_trace_consumer_case_ids('minif2f') == frozenset()
    typed = profiles.project_root_inventory_rows(inventory)
    assert typed.roots[0].planned_ai_unit_ids == row['merge_plan_shape']['dependency_order']


def test_cli_accepts_exp1_supplement_but_config_rejects_other_experiments():
    args = _parser().parse_args(['plan', '--profile', 'minif2f', '--run-id', 'offline-plan'])
    assert args.profile == 'minif2f'
    ExperimentRunConfigV1(run_id='future', profile_id='minif2f', experiment_ids=['exp1']).validate()
    with pytest.raises(SchemaValidationError, match='only Experiment 1'):
        ExperimentRunConfigV1(run_id='future', profile_id='minif2f', experiment_ids=['exp2']).validate()
