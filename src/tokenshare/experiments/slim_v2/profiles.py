"""Slim V2 full/representative 冻结 profile、inventory 与资源计划。"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from math import ceil
from pathlib import Path
from typing import Any, Mapping

from .case_source import load_cases, select_cases_by_ids
from .schema import RootInventoryV1

EXP1_FACTORIZATION_CASE_IDS = (
    "factor_v2_easy_036",
    "factor_v2_easy_110",
    "factor_v2_easy_018",
    "factor_v2_easy_063",
    "factor_v2_easy_106",
    "factor_v2_easy_045",
    "factor_v2_easy_009",
    "factor_v2_easy_095",
    "factor_v2_easy_160",
    "factor_v2_easy_093",
    "factor_v2_easy_143",
    "factor_v2_easy_103",
    "factor_v2_easy_065",
    "factor_v2_easy_078",
    "factor_v2_easy_126",
    "factor_v2_easy_107",
    "factor_v2_easy_099",
    "factor_v2_easy_039",
    "factor_v2_easy_071",
    "factor_v2_easy_152",
    "factor_v2_easy_023",
    "factor_v2_easy_101",
    "factor_v2_easy_028",
    "factor_v2_easy_115",
    "factor_v2_easy_031",
    "factor_v2_easy_128",
    "factor_v2_easy_003",
    "factor_v2_easy_068",
    "factor_v2_easy_042",
    "factor_v2_easy_112",
    "factor_v2_easy_096",
    "factor_v2_easy_014",
    "factor_v2_easy_007",
    "factor_v2_easy_092",
    "factor_v2_easy_088",
    "factor_v2_easy_089",
    "factor_v2_easy_149",
    "factor_v2_easy_150",
    "factor_v2_easy_034",
    "factor_v2_easy_141",
    "factor_v2_easy_137",
    "factor_v2_easy_033",
    "factor_v2_easy_064",
    "factor_v2_easy_020",
    "factor_v2_easy_133",
    "factor_v2_easy_085",
    "factor_v2_easy_002",
    "factor_v2_easy_139",
    "factor_v2_easy_066",
    "factor_v2_easy_109",
    "factor_v2_easy_017",
    "factor_v2_easy_051",
    "factor_v2_easy_019",
    "factor_v2_easy_062",
    "factor_v2_easy_148",
    "factor_v2_easy_119",
    "factor_v2_easy_056",
    "factor_v2_easy_104",
    "factor_v2_easy_097",
    "factor_v2_easy_146",
    "factor_v2_easy_127",
    "factor_v2_easy_013",
    "factor_v2_easy_136",
    "factor_v2_easy_102",
    "factor_v2_easy_167",
    "factor_v2_easy_144",
    "factor_v2_easy_131",
    "factor_v2_easy_044",
    "factor_v2_easy_147",
    "factor_v2_easy_125",
    "factor_v2_easy_059",
    "factor_v2_easy_134",
    "factor_v2_easy_158",
    "factor_v2_easy_021",
    "factor_v2_easy_072",
    "factor_v2_easy_132",
    "factor_v2_easy_073",
    "factor_v2_easy_165",
    "factor_v2_easy_084",
    "factor_v2_easy_043",
    "factor_v2_easy_154",
    "factor_v2_easy_123",
    "factor_v2_easy_083",
    "factor_v2_easy_049",
    "factor_v2_easy_029",
    "factor_v2_easy_074",
    "factor_v2_easy_055",
    "factor_v2_easy_157",
    "factor_v2_easy_030",
    "factor_v2_easy_114",
    "factor_v2_easy_135",
    "factor_v2_easy_016",
    "factor_v2_easy_090",
    "factor_v2_easy_153",
    "factor_v2_easy_054",
    "factor_v2_easy_069",
    "factor_v2_easy_140",
    "factor_v2_easy_086",
    "factor_v2_easy_035",
    "factor_v2_easy_040",
    "factor_v2_medium_063",
    "factor_v2_medium_018",
    "factor_v2_medium_071",
    "factor_v2_medium_080",
    "factor_v2_medium_116",
    "factor_v2_medium_087",
    "factor_v2_medium_100",
    "factor_v2_medium_084",
    "factor_v2_medium_166",
    "factor_v2_medium_081",
    "factor_v2_medium_152",
    "factor_v2_medium_156",
    "factor_v2_medium_053",
    "factor_v2_medium_097",
    "factor_v2_medium_135",
    "factor_v2_medium_150",
    "factor_v2_medium_054",
    "factor_v2_medium_130",
    "factor_v2_medium_138",
    "factor_v2_medium_127",
    "factor_v2_medium_030",
    "factor_v2_medium_099",
    "factor_v2_medium_070",
    "factor_v2_medium_160",
    "factor_v2_medium_014",
    "factor_v2_medium_122",
    "factor_v2_medium_139",
    "factor_v2_medium_022",
    "factor_v2_medium_028",
    "factor_v2_medium_125",
    "factor_v2_medium_027",
    "factor_v2_medium_158",
    "factor_v2_medium_148",
    "factor_v2_medium_031",
    "factor_v2_medium_046",
    "factor_v2_medium_119",
    "factor_v2_medium_036",
    "factor_v2_medium_024",
    "factor_v2_medium_047",
    "factor_v2_medium_095",
    "factor_v2_medium_102",
    "factor_v2_medium_124",
    "factor_v2_medium_061",
    "factor_v2_medium_117",
    "factor_v2_medium_065",
    "factor_v2_medium_044",
    "factor_v2_medium_039",
    "factor_v2_medium_165",
    "factor_v2_medium_123",
    "factor_v2_medium_147",
    "factor_v2_medium_111",
    "factor_v2_medium_157",
    "factor_v2_medium_051",
    "factor_v2_medium_108",
    "factor_v2_medium_154",
    "factor_v2_medium_136",
    "factor_v2_medium_017",
    "factor_v2_medium_094",
    "factor_v2_medium_118",
    "factor_v2_medium_056",
    "factor_v2_medium_106",
    "factor_v2_medium_141",
    "factor_v2_medium_167",
    "factor_v2_medium_066",
    "factor_v2_medium_109",
    "factor_v2_medium_164",
    "factor_v2_medium_067",
    "factor_v2_medium_129",
    "factor_v2_medium_077",
    "factor_v2_medium_048",
    "factor_v2_medium_134",
    "factor_v2_medium_114",
    "factor_v2_medium_083",
    "factor_v2_medium_140",
    "factor_v2_medium_002",
    "factor_v2_medium_034",
    "factor_v2_medium_149",
    "factor_v2_medium_088",
    "factor_v2_medium_161",
    "factor_v2_medium_096",
    "factor_v2_medium_074",
    "factor_v2_medium_085",
    "factor_v2_medium_013",
    "factor_v2_medium_072",
    "factor_v2_medium_132",
    "factor_v2_medium_055",
    "factor_v2_medium_103",
    "factor_v2_medium_012",
    "factor_v2_medium_064",
    "factor_v2_medium_126",
    "factor_v2_medium_057",
    "factor_v2_medium_091",
    "factor_v2_medium_120",
    "factor_v2_medium_112",
    "factor_v2_medium_076",
    "factor_v2_medium_045",
    "factor_v2_medium_052",
    "factor_v2_medium_006",
    "factor_v2_medium_146",
    "factor_v2_medium_068",
    "factor_v2_hard_138",
    "factor_v2_hard_145",
    "factor_v2_hard_012",
    "factor_v2_hard_035",
    "factor_v2_hard_089",
    "factor_v2_hard_062",
    "factor_v2_hard_165",
    "factor_v2_hard_040",
    "factor_v2_hard_057",
    "factor_v2_hard_020",
    "factor_v2_hard_104",
    "factor_v2_hard_069",
    "factor_v2_hard_017",
    "factor_v2_hard_055",
    "factor_v2_hard_043",
    "factor_v2_hard_075",
    "factor_v2_hard_034",
    "factor_v2_hard_005",
    "factor_v2_hard_125",
    "factor_v2_hard_152",
    "factor_v2_hard_106",
    "factor_v2_hard_038",
    "factor_v2_hard_079",
    "factor_v2_hard_133",
    "factor_v2_hard_026",
    "factor_v2_hard_032",
    "factor_v2_hard_081",
    "factor_v2_hard_088",
    "factor_v2_hard_013",
    "factor_v2_hard_080",
    "factor_v2_hard_091",
    "factor_v2_hard_071",
    "factor_v2_hard_141",
    "factor_v2_hard_004",
    "factor_v2_hard_084",
    "factor_v2_hard_163",
    "factor_v2_hard_129",
    "factor_v2_hard_041",
    "factor_v2_hard_153",
    "factor_v2_hard_086",
    "factor_v2_hard_130",
    "factor_v2_hard_021",
    "factor_v2_hard_077",
    "factor_v2_hard_162",
    "factor_v2_hard_134",
    "factor_v2_hard_142",
    "factor_v2_hard_015",
    "factor_v2_hard_087",
    "factor_v2_hard_097",
    "factor_v2_hard_121",
    "factor_v2_hard_166",
    "factor_v2_hard_037",
    "factor_v2_hard_131",
    "factor_v2_hard_083",
    "factor_v2_hard_010",
    "factor_v2_hard_061",
    "factor_v2_hard_116",
    "factor_v2_hard_028",
    "factor_v2_hard_136",
    "factor_v2_hard_076",
    "factor_v2_hard_113",
    "factor_v2_hard_158",
    "factor_v2_hard_049",
    "factor_v2_hard_160",
    "factor_v2_hard_140",
    "factor_v2_hard_011",
    "factor_v2_hard_078",
    "factor_v2_hard_023",
    "factor_v2_hard_019",
    "factor_v2_hard_047",
    "factor_v2_hard_036",
    "factor_v2_hard_051",
    "factor_v2_hard_018",
    "factor_v2_hard_066",
    "factor_v2_hard_031",
    "factor_v2_hard_107",
    "factor_v2_hard_156",
    "factor_v2_hard_045",
    "factor_v2_hard_090",
    "factor_v2_hard_025",
    "factor_v2_hard_114",
    "factor_v2_hard_067",
    "factor_v2_hard_157",
    "factor_v2_hard_060",
    "factor_v2_hard_085",
    "factor_v2_hard_098",
    "factor_v2_hard_128",
    "factor_v2_hard_046",
    "factor_v2_hard_094",
    "factor_v2_hard_161",
    "factor_v2_hard_073",
    "factor_v2_hard_122",
    "factor_v2_hard_101",
    "factor_v2_hard_132",
    "factor_v2_hard_072",
    "factor_v2_hard_118",
    "factor_v2_hard_103",
    "factor_v2_hard_059",
    "factor_v2_hard_126",
    "factor_v2_hard_139",
)

EXP1_LEAN_CASE_IDS = (
    "lean_v2_simple_pure_logic_direct_prop_01",
    "lean_v2_simple_pure_logic_direct_prop_15",
    "lean_v2_simple_pure_logic_direct_prop_07",
    "lean_v2_simple_pure_logic_direct_prop_06",
    "lean_v2_simple_pure_logic_direct_prop_04",
    "lean_v2_simple_pure_logic_direct_prop_09",
    "lean_v2_simple_pure_logic_direct_prop_08",
    "lean_v2_simple_pure_logic_direct_prop_05",
    "lean_v2_simple_pure_logic_direct_prop_11",
    "lean_v2_simple_pure_logic_direct_prop_12",
    "lean_v2_simple_pure_logic_direct_prop_03",
    "lean_v2_simple_pure_logic_direct_prop_02",
    "lean_v2_simple_pure_logic_direct_prop_13",
    "lean_v2_simple_pure_logic_direct_prop_14",
    "lean_v2_simple_pure_logic_direct_prop_10",
    "lean_v2_simple_function_set_direct_subset_05",
    "lean_v2_simple_function_set_direct_subset_09",
    "lean_v2_simple_function_set_direct_subset_12",
    "lean_v2_simple_function_set_direct_subset_04",
    "lean_v2_simple_function_set_direct_subset_07",
    "lean_v2_simple_function_set_direct_subset_10",
    "lean_v2_simple_function_set_direct_subset_01",
    "lean_v2_simple_function_set_direct_subset_08",
    "lean_v2_simple_function_set_direct_subset_11",
    "lean_v2_simple_function_set_direct_subset_06",
    "lean_v2_simple_function_set_direct_subset_15",
    "lean_v2_simple_function_set_direct_subset_02",
    "lean_v2_simple_function_set_direct_subset_13",
    "lean_v2_simple_function_set_direct_subset_03",
    "lean_v2_simple_function_set_direct_subset_14",
    "lean_v2_simple_induction_direct_nat_01",
    "lean_v2_simple_induction_direct_nat_12",
    "lean_v2_simple_induction_direct_nat_02",
    "lean_v2_simple_induction_direct_nat_13",
    "lean_v2_simple_induction_direct_nat_03",
    "lean_v2_simple_induction_direct_nat_07",
    "lean_v2_simple_induction_direct_nat_09",
    "lean_v2_simple_induction_direct_nat_04",
    "lean_v2_simple_induction_direct_nat_05",
    "lean_v2_simple_induction_direct_nat_15",
    "lean_v2_simple_induction_direct_nat_14",
    "lean_v2_simple_induction_direct_nat_11",
    "lean_v2_simple_induction_direct_nat_08",
    "lean_v2_simple_induction_direct_nat_06",
    "lean_v2_simple_induction_direct_nat_10",
    "lean_v2_medium_lemma_dag_11",
    "lean_v2_medium_lemma_dag_09",
    "lean_v2_medium_lemma_dag_13",
    "lean_v2_medium_lemma_dag_02",
    "lean_v2_medium_lemma_dag_08",
    "lean_v2_medium_lemma_dag_04",
    "lean_v2_medium_lemma_dag_12",
    "lean_v2_medium_lemma_dag_07",
    "lean_v2_medium_lemma_dag_05",
    "lean_v2_medium_lemma_dag_01",
    "lean_v2_medium_lemma_dag_03",
    "lean_v2_medium_lemma_dag_14",
    "lean_v2_medium_lemma_dag_10",
    "lean_v2_medium_lemma_dag_15",
    "lean_v2_medium_lemma_dag_06",
    "lean_v2_medium_function_set_dx_subset_chain_03",
    "lean_v2_medium_function_set_dx_subset_chain_05",
    "lean_v2_medium_function_set_dx_subset_chain_06",
    "lean_v2_medium_function_set_dx_subset_chain_02",
    "lean_v2_medium_function_set_dx_subset_chain_11",
    "lean_v2_medium_function_set_dx_subset_chain_10",
    "lean_v2_medium_function_set_dx_subset_chain_04",
    "lean_v2_medium_function_set_dx_subset_chain_01",
    "lean_v2_medium_function_set_dx_subset_chain_15",
    "lean_v2_medium_function_set_dx_subset_chain_12",
    "lean_v2_medium_function_set_dx_subset_chain_13",
    "lean_v2_medium_function_set_dx_subset_chain_07",
    "lean_v2_medium_function_set_dx_subset_chain_14",
    "lean_v2_medium_function_set_dx_subset_chain_08",
    "lean_v2_medium_function_set_dx_subset_chain_09",
    "lean_v2_medium_induction_nat_predicate_chain_13",
    "lean_v2_medium_induction_nat_predicate_chain_05",
    "lean_v2_medium_induction_nat_predicate_chain_03",
    "lean_v2_medium_induction_nat_predicate_chain_11",
    "lean_v2_medium_induction_nat_predicate_chain_10",
    "lean_v2_medium_induction_nat_predicate_chain_02",
    "lean_v2_medium_induction_nat_predicate_chain_09",
    "lean_v2_medium_induction_nat_predicate_chain_14",
    "lean_v2_medium_induction_nat_predicate_chain_07",
    "lean_v2_medium_induction_nat_predicate_chain_08",
    "lean_v2_medium_induction_nat_predicate_chain_01",
    "lean_v2_medium_induction_nat_predicate_chain_04",
    "lean_v2_medium_induction_nat_predicate_chain_06",
    "lean_v2_medium_induction_nat_predicate_chain_15",
    "lean_v2_medium_induction_nat_predicate_chain_12",
    "lean_v2_hard_frontier_pure_logic_checker_12",
    "lean_v2_hard_frontier_pure_logic_checker_02",
    "lean_v2_hard_frontier_pure_logic_checker_09",
    "lean_v2_hard_frontier_pure_logic_checker_10",
    "lean_v2_hard_frontier_pure_logic_checker_08",
    "lean_v2_hard_frontier_pure_logic_checker_01",
    "lean_v2_hard_frontier_pure_logic_checker_03",
    "lean_v2_hard_frontier_pure_logic_checker_15",
    "lean_v2_hard_frontier_pure_logic_checker_05",
    "lean_v2_hard_frontier_pure_logic_checker_11",
    "lean_v2_hard_frontier_pure_logic_checker_14",
    "lean_v2_hard_frontier_pure_logic_checker_07",
    "lean_v2_hard_frontier_pure_logic_checker_06",
    "lean_v2_hard_frontier_pure_logic_checker_04",
    "lean_v2_hard_frontier_pure_logic_checker_13",
    "lean_v2_hard_frontier_function_set_checker_14",
    "lean_v2_hard_frontier_function_set_checker_11",
    "lean_v2_hard_frontier_function_set_checker_10",
    "lean_v2_hard_frontier_function_set_checker_15",
    "lean_v2_hard_frontier_function_set_checker_08",
    "lean_v2_hard_frontier_function_set_checker_09",
    "lean_v2_hard_frontier_function_set_checker_05",
    "lean_v2_hard_frontier_function_set_checker_04",
    "lean_v2_hard_frontier_function_set_checker_01",
    "lean_v2_hard_frontier_function_set_checker_02",
    "lean_v2_hard_frontier_function_set_checker_06",
    "lean_v2_hard_frontier_function_set_checker_03",
    "lean_v2_hard_frontier_function_set_checker_07",
    "lean_v2_hard_frontier_function_set_checker_13",
    "lean_v2_hard_frontier_function_set_checker_12",
    "lean_v2_hard_frontier_induction_checker_06",
    "lean_v2_hard_frontier_induction_checker_05",
    "lean_v2_hard_frontier_induction_checker_10",
    "lean_v2_hard_frontier_induction_checker_01",
    "lean_v2_hard_frontier_induction_checker_12",
    "lean_v2_hard_frontier_induction_checker_13",
    "lean_v2_hard_frontier_induction_checker_07",
    "lean_v2_hard_frontier_induction_checker_02",
    "lean_v2_hard_frontier_induction_checker_08",
    "lean_v2_hard_frontier_induction_checker_14",
    "lean_v2_hard_frontier_induction_checker_11",
    "lean_v2_hard_frontier_induction_checker_04",
    "lean_v2_hard_frontier_induction_checker_03",
    "lean_v2_hard_frontier_induction_checker_15",
    "lean_v2_hard_frontier_induction_checker_09",
)

EXP2_FACTORIZATION_CASE_IDS = (
    "factor_v2_hard_138",
    "factor_v2_hard_145",
    "factor_v2_hard_012",
    "factor_v2_hard_035",
    "factor_v2_hard_089",
    "factor_v2_hard_062",
    "factor_v2_hard_165",
    "factor_v2_hard_040",
    "factor_v2_hard_057",
    "factor_v2_hard_020",
    "factor_v2_hard_104",
    "factor_v2_hard_069",
    "factor_v2_hard_017",
    "factor_v2_hard_055",
    "factor_v2_hard_043",
    "factor_v2_hard_075",
    "factor_v2_hard_034",
    "factor_v2_hard_005",
    "factor_v2_hard_125",
    "factor_v2_hard_152",
    "factor_v2_hard_106",
    "factor_v2_hard_038",
    "factor_v2_hard_079",
    "factor_v2_hard_133",
    "factor_v2_hard_026",
    "factor_v2_hard_032",
    "factor_v2_hard_081",
    "factor_v2_hard_088",
    "factor_v2_hard_013",
    "factor_v2_hard_080",
    "factor_v2_hard_091",
    "factor_v2_hard_071",
    "factor_v2_hard_141",
    "factor_v2_hard_004",
    "factor_v2_hard_084",
    "factor_v2_hard_163",
    "factor_v2_hard_129",
    "factor_v2_hard_041",
    "factor_v2_hard_153",
    "factor_v2_hard_086",
    "factor_v2_hard_130",
    "factor_v2_hard_021",
    "factor_v2_hard_077",
    "factor_v2_hard_162",
    "factor_v2_hard_134",
    "factor_v2_hard_142",
    "factor_v2_hard_015",
    "factor_v2_hard_087",
    "factor_v2_hard_097",
    "factor_v2_hard_121",
)

EXP3_FACTORIZATION_CASE_IDS = (
    "factor_v2_easy_036",
    "factor_v2_easy_110",
    "factor_v2_easy_018",
    "factor_v2_easy_063",
    "factor_v2_easy_106",
    "factor_v2_easy_045",
    "factor_v2_easy_009",
    "factor_v2_easy_095",
    "factor_v2_easy_160",
    "factor_v2_easy_093",
    "factor_v2_easy_143",
    "factor_v2_easy_103",
    "factor_v2_easy_065",
    "factor_v2_easy_078",
    "factor_v2_easy_126",
    "factor_v2_easy_107",
    "factor_v2_easy_099",
    "factor_v2_medium_063",
    "factor_v2_medium_018",
    "factor_v2_medium_071",
    "factor_v2_medium_080",
    "factor_v2_medium_116",
    "factor_v2_medium_087",
    "factor_v2_medium_100",
    "factor_v2_medium_084",
    "factor_v2_medium_166",
    "factor_v2_medium_081",
    "factor_v2_medium_152",
    "factor_v2_medium_156",
    "factor_v2_medium_053",
    "factor_v2_medium_097",
    "factor_v2_medium_135",
    "factor_v2_medium_150",
    "factor_v2_medium_054",
    "factor_v2_hard_138",
    "factor_v2_hard_145",
    "factor_v2_hard_012",
    "factor_v2_hard_035",
    "factor_v2_hard_089",
    "factor_v2_hard_062",
    "factor_v2_hard_165",
    "factor_v2_hard_040",
    "factor_v2_hard_057",
    "factor_v2_hard_020",
    "factor_v2_hard_104",
    "factor_v2_hard_069",
    "factor_v2_hard_017",
    "factor_v2_hard_055",
    "factor_v2_hard_043",
    "factor_v2_hard_075",
)

EXP3_LEAN_CASE_IDS = (
    "lean_v2_simple_pure_logic_direct_prop_01",
    "lean_v2_simple_function_set_direct_subset_05",
    "lean_v2_simple_induction_direct_nat_01",
)

EXP4_FACTORIZATION_CASE_IDS = (
    "factor_v2_easy_036",
    "factor_v2_easy_110",
    "factor_v2_easy_018",
    "factor_v2_easy_063",
    "factor_v2_easy_106",
    "factor_v2_easy_045",
    "factor_v2_easy_009",
    "factor_v2_easy_095",
    "factor_v2_easy_160",
    "factor_v2_easy_093",
    "factor_v2_easy_143",
    "factor_v2_easy_103",
    "factor_v2_easy_065",
    "factor_v2_easy_078",
    "factor_v2_easy_126",
    "factor_v2_easy_107",
    "factor_v2_easy_099",
    "factor_v2_medium_063",
    "factor_v2_medium_018",
    "factor_v2_medium_071",
    "factor_v2_medium_080",
    "factor_v2_medium_116",
    "factor_v2_medium_087",
    "factor_v2_medium_100",
    "factor_v2_medium_084",
    "factor_v2_medium_166",
    "factor_v2_medium_081",
    "factor_v2_medium_152",
    "factor_v2_medium_156",
    "factor_v2_medium_053",
    "factor_v2_medium_097",
    "factor_v2_medium_135",
    "factor_v2_medium_150",
    "factor_v2_medium_054",
    "factor_v2_hard_138",
    "factor_v2_hard_145",
    "factor_v2_hard_012",
    "factor_v2_hard_035",
    "factor_v2_hard_089",
    "factor_v2_hard_062",
    "factor_v2_hard_165",
    "factor_v2_hard_040",
    "factor_v2_hard_057",
    "factor_v2_hard_020",
    "factor_v2_hard_104",
    "factor_v2_hard_069",
    "factor_v2_hard_017",
    "factor_v2_hard_055",
    "factor_v2_hard_043",
    "factor_v2_hard_075",
)

EXP4_LEAN_CASE_IDS = (
    "lean_v2_simple_pure_logic_direct_prop_01",
    "lean_v2_simple_pure_logic_direct_prop_15",
    "lean_v2_simple_function_set_direct_subset_05",
    "lean_v2_simple_function_set_direct_subset_09",
    "lean_v2_simple_induction_direct_nat_01",
    "lean_v2_medium_lemma_dag_11",
    "lean_v2_medium_lemma_dag_09",
    "lean_v2_medium_function_set_dx_subset_chain_03",
    "lean_v2_medium_function_set_dx_subset_chain_05",
    "lean_v2_medium_induction_nat_predicate_chain_13",
    "lean_v2_hard_frontier_pure_logic_checker_12",
    "lean_v2_hard_frontier_pure_logic_checker_02",
    "lean_v2_hard_frontier_function_set_checker_14",
    "lean_v2_hard_frontier_function_set_checker_11",
    "lean_v2_hard_frontier_induction_checker_06",
)

EXP5_FACTORIZATION_CASE_IDS = (
    "factor_v2_hard_138",
    "factor_v2_hard_145",
    "factor_v2_hard_012",
    "factor_v2_hard_035",
    "factor_v2_hard_089",
    "factor_v2_hard_062",
    "factor_v2_hard_165",
    "factor_v2_hard_040",
    "factor_v2_hard_057",
    "factor_v2_hard_020",
    "factor_v2_hard_104",
    "factor_v2_hard_069",
    "factor_v2_hard_017",
    "factor_v2_hard_055",
    "factor_v2_hard_043",
    "factor_v2_hard_075",
    "factor_v2_hard_034",
    "factor_v2_hard_005",
    "factor_v2_hard_125",
    "factor_v2_hard_152",
    "factor_v2_hard_106",
    "factor_v2_hard_038",
    "factor_v2_hard_079",
    "factor_v2_hard_133",
    "factor_v2_hard_026",
    "factor_v2_hard_032",
    "factor_v2_hard_081",
    "factor_v2_hard_088",
    "factor_v2_hard_013",
    "factor_v2_hard_080",
    "factor_v2_hard_091",
    "factor_v2_hard_071",
    "factor_v2_hard_141",
    "factor_v2_hard_004",
    "factor_v2_hard_084",
    "factor_v2_hard_163",
    "factor_v2_hard_129",
    "factor_v2_hard_041",
    "factor_v2_hard_153",
    "factor_v2_hard_086",
    "factor_v2_hard_130",
    "factor_v2_hard_021",
)

EXP5_LEAN_CASE_IDS = (
    "lean_v2_hard_frontier_pure_logic_checker_12",
    "lean_v2_hard_frontier_pure_logic_checker_02",
    "lean_v2_hard_frontier_pure_logic_checker_09",
    "lean_v2_hard_frontier_pure_logic_checker_10",
    "lean_v2_hard_frontier_function_set_checker_14",
    "lean_v2_hard_frontier_function_set_checker_11",
    "lean_v2_hard_frontier_function_set_checker_10",
    "lean_v2_hard_frontier_function_set_checker_15",
    "lean_v2_hard_frontier_induction_checker_06",
    "lean_v2_hard_frontier_induction_checker_05",
    "lean_v2_hard_frontier_induction_checker_10",
    "lean_v2_hard_frontier_induction_checker_01",
)

REPRESENTATIVE_CASE_IDS = (
    "factor_v2_hard_138",
    "factor_v2_hard_145",
    "lean_v2_simple_induction_direct_nat_01",
    "lean_v2_simple_pure_logic_direct_prop_01",
)

REPRESENTATIVE_CHALLENGE_ROWS = (
    (
        'factor_v2_hard_138',
        0,
        'INVALID_PARSED_CANDIDATE',
        'stable_first_planned_unit',
        'ordinal_0',
    ),
    (
        'factor_v2_hard_145',
        0,
        'PARSER_REQUIRED_CANONICAL_JSON',
        'stable_first_planned_unit',
        'every_attempt',
    ),
    (
        'lean_v2_simple_induction_direct_nat_01',
        0,
        'REQUIRED_CHILD_DELAY',
        'last_required_terminal_slot',
        'ordinal_0',
    ),
    (
        'lean_v2_simple_pure_logic_direct_prop_01',
        0,
        'RECOVERABLE_NO_RETURN',
        'stable_first_planned_unit',
        'ordinal_0',
    ),
)


EXPERIMENT_ORDER = ("exp1", "exp2", "exp3", "exp4", "exp5")
EXP3_FAULT_TYPES = (
    "false_positive",
    "false_negative",
    "no_return",
    "late_submission",
    "executor_error",
)
EXP4_MODES = (
    "FULL",
    "NO_VERIFICATION",
    "NO_PARSER_POLICY",
    "NO_REQUEUE",
    "NO_MERGE_GATE",
    "NO_VERIFICATION__NO_PARSER_POLICY",
    "NO_VERIFICATION__NO_REQUEUE",
    "NO_VERIFICATION__NO_MERGE_GATE",
    "NO_PARSER_POLICY__NO_REQUEUE",
    "NO_PARSER_POLICY__NO_MERGE_GATE",
    "NO_REQUEUE__NO_MERGE_GATE",
)
EXP4_NO_REQUEUE_MODES = frozenset(
    mode for mode in EXP4_MODES if "NO_REQUEUE" in mode
)
_EXP4_DISABLED_NAMES = {
    "NO_VERIFICATION": "verification",
    "NO_PARSER_POLICY": "parser_policy",
    "NO_REQUEUE": "requeue",
    "NO_MERGE_GATE": "merge_gate",
}
_EXP5_PROVIDER_ENTRIES = {
    "zai-org/GLM-5.2": "glm_5_2_exp5_v3",
    "Qwen/Qwen3-14B": "qwen3_14b_exp5_v3",
    "MiniMaxAI/MiniMax-M2.5": "minimax_m2_5_exp5_v3",
    "Pro/deepseek-ai/DeepSeek-V3": "deepseek_v3_pro_exp5_v3",
}


@dataclass(frozen=True, slots=True)
class ExperimentProfileV1:
    """一个实验在冻结 profile 中的显式运行控制。"""

    experiment_id: str
    worker_counts: tuple[int, ...]
    repeat_ids: tuple[int, ...]
    max_retries: int
    continue_after_terminal_child_failure: bool
    source_repeat_id: int | None
    provider_entry_id: str | None = None
    model_id: str | None = None
    thinking: bool | None = None
    reasoning_effort: str | None = None
    timeout_seconds: int | None = None
    max_tokens: int | None = None


@dataclass(frozen=True, slots=True)
class ProfileV1:
    """Full 与 representative 共用的最小冻结 profile。"""

    profile_id: str
    experiments: Mapping[str, ExperimentProfileV1]


def build_profile(profile_id: str) -> ProfileV1:
    """返回显式冻结的 full/representative 运行控制。"""

    if profile_id not in {"full", "representative"}:
        raise ValueError(f"unknown Slim V2 profile: {profile_id}")
    exp2_workers = (1, 3, 7, 10, 30, 50) if profile_id == "full" else (1, 10, 50)
    exp3_repeats = (0, 1) if profile_id == "full" else (0,)
    exp4_repeats = (0, 1, 2) if profile_id == "full" else (0,)
    exp5_repeats = (0, 1, 2) if profile_id == "full" else (0,)
    experiments = {
        "exp1": ExperimentProfileV1(
            "exp1", (10,), (0,), 2, False, None,
            "deepseek_v4_pro_exp1_baseline", "deepseek-v4-pro", True,
            "high", 600, 300_000,
        ),
        "exp2": ExperimentProfileV1(
            "exp2", exp2_workers, (0, 1), 2, False, 0,
        ),
        "exp3": ExperimentProfileV1(
            "exp3", (10,), exp3_repeats, 2, False, 0,
        ),
        "exp4": ExperimentProfileV1(
            "exp4", (10,), exp4_repeats, 1, False, 0,
        ),
        "exp5": ExperimentProfileV1(
            "exp5", (10,), exp5_repeats, 0, False, None,
        ),
    }
    return ProfileV1(profile_id=profile_id, experiments=experiments)


@dataclass(frozen=True, slots=True)
class ConditionV1:
    condition_id: str
    experiment_id: str
    domain: str
    difficulty: str | None
    topic_family: str | None
    repeat_id: int
    worker_count: int
    max_retries: int
    continue_after_terminal_child_failure: bool
    source_repeat_id: int | None
    position_stratum: str | None = None
    fault_type: str | None = None
    fault_rate_percent: int | None = None
    dead_worker_count: int | None = None
    kill_progress_percent: int | None = None
    mode: str | None = None
    model_id: str | None = None


@dataclass(frozen=True, slots=True)
class RootRunV1:
    root_run_id: str
    experiment_id: str
    condition_id: str
    case_id: str
    repeat_id: int
    domain: str
    difficulty: str | None
    topic_family: str | None
    worker_count: int
    max_retries: int
    continue_after_terminal_child_failure: bool
    source_repeat_id: int | None
    planned_ai_unit_count: int
    is_paper_denominator: bool = True
    challenge_plan_id: str | None = None


@dataclass(frozen=True, slots=True)
class ChallengePlanV1:
    challenge_plan_id: str
    case_id: str
    repeat_id: int
    challenge_family: str
    target_rule: str
    attempt_rule: str


@dataclass(frozen=True, slots=True)
class InventoryV1:
    profile_id: str
    conditions: tuple[ConditionV1, ...]
    roots: tuple[RootRunV1, ...]
    references: tuple[RootRunV1, ...]
    challenges: tuple[ChallengePlanV1, ...]

    @property
    def condition_counts(self) -> dict[str, int]:
        return dict(Counter(item.experiment_id for item in self.conditions))

    @property
    def paper_root_counts(self) -> dict[str, int]:
        return dict(Counter(item.experiment_id for item in self.roots))

    @property
    def reference_root_counts(self) -> dict[str, int]:
        return dict(Counter(item.experiment_id for item in self.references))

    @property
    def paper_root_count(self) -> int:
        return len(self.roots)

    @property
    def execution_root_count(self) -> int:
        return len(self.roots) + len(self.references)


@dataclass(frozen=True, slots=True)
class RootInventoryRowsV1:
    """可直接写入 roots/reference JSONL 的 typed schema rows。"""

    roots: tuple[RootInventoryV1, ...]
    exp3_references: tuple[RootInventoryV1, ...]


_REPO_ROOT = Path(__file__).parents[4]
_FACTOR_CATALOG = _REPO_ROOT / "benchmarks/paper/factorization_catalog.v2.jsonl"
_LEAN_CATALOG = _REPO_ROOT / "benchmarks/paper/lean_lemma_graph_catalog.v1.jsonl"
_CHALLENGE_FAMILIES = (
    "INVALID_PARSED_CANDIDATE",
    "PARSER_REQUIRED_CANONICAL_JSON",
    "RECOVERABLE_NO_RETURN",
    "REQUIRED_CHILD_DELAY",
)


def _catalogs() -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    factor = {str(row["case_id"]): row for row in load_cases(_FACTOR_CATALOG)}
    lean = {str(row["case_id"]): row for row in load_cases(_LEAN_CATALOG)}
    return factor, lean


def _case_data(
    case_id: str,
    factor: Mapping[str, dict[str, Any]],
    lean: Mapping[str, dict[str, Any]],
) -> tuple[str, str, str | None, int, dict[str, Any]]:
    if case_id in factor:
        row = factor[case_id]
        return (
            "factorization",
            str(row["paper_difficulty"]),
            None,
            int(row["split_params"]["requested_child_count"]),
            row,
        )
    row = lean[case_id]
    return (
        "lean",
        str(row["paper_difficulty"]),
        str(row["topic_family"]),
        int(row["expected_ai_unit_count"]),
        row,
    )


def _condition_id(experiment_id: str, **dimensions: Any) -> str:
    encoded = "|".join(f"{key}={value}" for key, value in dimensions.items())
    return f"{experiment_id}|{encoded}"


def _canonical_condition_id(experiment_id: str, dimensions: Mapping[str, Any]) -> str:
    if experiment_id == "exp1":
        ordered = {
            "domain": dimensions["domain"],
            "difficulty": dimensions["difficulty"],
        }
        if "topic" in dimensions:
            ordered["topic"] = dimensions["topic"]
    elif experiment_id == "exp2":
        ordered = {
            "worker": dimensions["worker"],
            "repeat": dimensions["repeat"],
            "position": dimensions["position"],
        }
    elif experiment_id == "exp3":
        ordered = {"domain": dimensions["domain"]}
        stratum_key = "difficulty" if "difficulty" in dimensions else "topic"
        ordered[stratum_key] = dimensions[stratum_key]
        if "fault" in dimensions:
            ordered.update(
                fault=dimensions["fault"],
                rate=dimensions["rate"],
                repeat=dimensions["repeat"],
            )
        else:
            ordered.update(
                dead=dimensions["dead"],
                progress=dimensions["progress"],
                repeat=dimensions["repeat"],
            )
    elif experiment_id == "exp4":
        ordered = {
            "mode": dimensions["mode"],
            "repeat": dimensions["repeat"],
        }
    elif experiment_id == "exp5":
        topic = dimensions.get("topic")
        stratum = (
            "factorization_hard"
            if dimensions["domain"] == "factorization"
            else f"lean_hard_{topic}"
        )
        ordered = {
            "model": dimensions["model"],
            "repeat": dimensions["repeat"],
            "stratum": stratum,
        }
    else:
        raise ValueError(f"unknown experiment for condition ID: {experiment_id}")
    return _condition_id(experiment_id, **ordered)


def _root(
    *,
    condition: ConditionV1,
    case_id: str,
    ordinal: int,
    factor: Mapping[str, dict[str, Any]],
    lean: Mapping[str, dict[str, Any]],
    is_paper_denominator: bool = True,
    challenge_plan_id: str | None = None,
) -> RootRunV1:
    domain, difficulty, topic, unit_count, _row = _case_data(case_id, factor, lean)
    kind = "paper" if is_paper_denominator else "reference"
    return RootRunV1(
        root_run_id=f"{condition.experiment_id}|{kind}|{ordinal:05d}|{case_id}",
        experiment_id=condition.experiment_id,
        condition_id=condition.condition_id,
        case_id=case_id,
        repeat_id=condition.repeat_id,
        domain=domain,
        difficulty=difficulty,
        topic_family=topic,
        worker_count=condition.worker_count,
        max_retries=condition.max_retries,
        continue_after_terminal_child_failure=(
            condition.continue_after_terminal_child_failure
        ),
        source_repeat_id=condition.source_repeat_id,
        planned_ai_unit_count=unit_count,
        is_paper_denominator=is_paper_denominator,
        challenge_plan_id=challenge_plan_id,
    )


def _challenge_plans(
    profile_id: str,
    factor: Mapping[str, dict[str, Any]],
    lean: Mapping[str, dict[str, Any]],
) -> tuple[ChallengePlanV1, ...]:
    target_rules = {
        "INVALID_PARSED_CANDIDATE": ("stable_first_planned_unit", "ordinal_0"),
        "PARSER_REQUIRED_CANONICAL_JSON": (
            "stable_first_planned_unit",
            "every_attempt",
        ),
        "RECOVERABLE_NO_RETURN": ("stable_first_planned_unit", "ordinal_0"),
    }

    def target_rule(
        family: str,
        case_id: str,
        domain: str,
    ) -> tuple[str, str]:
        if family != "REQUIRED_CHILD_DELAY":
            return target_rules[family]
        if domain == "lean":
            return "last_required_terminal_slot", "ordinal_0"
        row = factor[case_id]
        prime_factors = row["oracle_prime_factors"]
        is_prime = (
            isinstance(prime_factors, list)
            and len(prime_factors) == 1
            and prime_factors[0]["prime"] == row["target_n"]
        )
        return (
            "last_required_range" if is_prime else "all_true_divisor_ranges",
            "ordinal_0",
        )

    if profile_id == "representative":
        rows = REPRESENTATIVE_CHALLENGE_ROWS
    else:
        factor_cells = sorted(
            (
                (
                    case_id,
                    repeat_id,
                    str(factor[case_id]["paper_difficulty"]),
                )
                for case_id in EXP4_FACTORIZATION_CASE_IDS
                for repeat_id in (0, 1, 2)
            ),
            key=lambda item: (item[2], item[0], item[1]),
        )
        lean_cells = sorted(
            (
                (
                    case_id,
                    repeat_id,
                    str(lean[case_id]["paper_difficulty"]),
                    str(lean[case_id]["topic_family"]),
                )
                for case_id in EXP4_LEAN_CASE_IDS
                for repeat_id in (0, 1, 2)
            ),
            key=lambda item: (item[2], item[3], item[0], item[1]),
        )
        factor_cycle = _CHALLENGE_FAMILIES
        lean_cycle = (
            "REQUIRED_CHILD_DELAY",
            "INVALID_PARSED_CANDIDATE",
            "PARSER_REQUIRED_CANONICAL_JSON",
            "RECOVERABLE_NO_RETURN",
        )
        factor_assignments = tuple(
            (
                case_id,
                repeat_id,
                factor_cycle[index % len(factor_cycle)],
            )
            for index, (case_id, repeat_id, _difficulty) in enumerate(factor_cells)
        )
        lean_assignments = tuple(
            (
                case_id,
                repeat_id,
                lean_cycle[index % len(lean_cycle)],
            )
            for index, (case_id, repeat_id, _difficulty, _topic) in enumerate(lean_cells)
        )
        rows = tuple(
            (
                case_id,
                repeat_id,
                family,
                *target_rule(family, case_id, "factorization"),
            )
            for case_id, repeat_id, family in factor_assignments
        ) + tuple(
            (
                case_id,
                repeat_id,
                family,
                *target_rule(family, case_id, "lean"),
            )
            for case_id, repeat_id, family in lean_assignments
        )
    return tuple(
        ChallengePlanV1(
            challenge_plan_id=f"exp4-challenge-{index:03d}",
            case_id=str(case_id),
            repeat_id=int(repeat_id),
            challenge_family=str(family),
            target_rule=str(target_rule),
            attempt_rule=str(attempt_rule),
        )
        for index, (case_id, repeat_id, family, target_rule, attempt_rule) in enumerate(rows)
    )


def build_inventory(profile_id: str | ProfileV1) -> InventoryV1:
    """从冻结 profile 与当前 catalog 展开 canonical conditions/roots。"""

    profile = build_profile(profile_id) if isinstance(profile_id, str) else profile_id
    factor, lean = _catalogs()
    all_factor_ids = (
        EXP1_FACTORIZATION_CASE_IDS
        + EXP2_FACTORIZATION_CASE_IDS
        + EXP3_FACTORIZATION_CASE_IDS
        + EXP4_FACTORIZATION_CASE_IDS
        + EXP5_FACTORIZATION_CASE_IDS
    )
    all_lean_ids = (
        EXP1_LEAN_CASE_IDS
        + EXP3_LEAN_CASE_IDS
        + EXP4_LEAN_CASE_IDS
        + EXP5_LEAN_CASE_IDS
    )
    select_cases_by_ids(factor.values(), tuple(dict.fromkeys(all_factor_ids)))
    select_cases_by_ids(lean.values(), tuple(dict.fromkeys(all_lean_ids)))

    conditions: list[ConditionV1] = []
    roots: list[RootRunV1] = []
    references: list[RootRunV1] = []

    def condition(experiment_id: str, **dimensions: Any) -> ConditionV1:
        controls = profile.experiments[experiment_id]
        item = ConditionV1(
            condition_id=_canonical_condition_id(experiment_id, dimensions),
            experiment_id=experiment_id,
            domain=str(dimensions.get("domain", "mixed")),
            difficulty=dimensions.get("difficulty"),
            topic_family=dimensions.get("topic"),
            repeat_id=int(dimensions.get("repeat", 0)),
            worker_count=int(dimensions.get("worker", controls.worker_counts[0])),
            max_retries=controls.max_retries,
            continue_after_terminal_child_failure=(
                controls.continue_after_terminal_child_failure
            ),
            source_repeat_id=controls.source_repeat_id,
            position_stratum=dimensions.get("position"),
            fault_type=dimensions.get("fault"),
            fault_rate_percent=dimensions.get("rate"),
            dead_worker_count=dimensions.get("dead"),
            kill_progress_percent=dimensions.get("progress"),
            mode=dimensions.get("mode"),
            model_id=dimensions.get("model"),
        )
        conditions.append(item)
        return item

    exp1_ids = (
        EXP1_FACTORIZATION_CASE_IDS + EXP1_LEAN_CASE_IDS
        if profile.profile_id == "full"
        else REPRESENTATIVE_CASE_IDS
    )
    exp1_groups: dict[tuple[str, str, str | None], list[str]] = {}
    for case_id in exp1_ids:
        domain, difficulty, topic, _units, _row = _case_data(case_id, factor, lean)
        exp1_groups.setdefault((domain, difficulty, topic), []).append(case_id)
    for (domain, difficulty, topic), case_ids in exp1_groups.items():
        dimensions: dict[str, Any] = {
            "domain": domain,
            "difficulty": difficulty,
            "repeat": 0,
        }
        if topic is not None:
            dimensions["topic"] = topic
        item = condition("exp1", **dimensions)
        for case_id in case_ids:
            roots.append(
                _root(
                    condition=item,
                    case_id=case_id,
                    ordinal=len(roots),
                    factor=factor,
                    lean=lean,
                )
            )

    exp2_ids = (
        EXP2_FACTORIZATION_CASE_IDS
        if profile.profile_id == "full"
        else REPRESENTATIVE_CASE_IDS[:2]
    )
    for worker in profile.experiments["exp2"].worker_counts:
        for repeat_id in (0, 1):
            by_position: dict[str, list[str]] = {}
            for case_id in exp2_ids:
                position = str(factor[case_id]["factor_position_quantile"])
                by_position.setdefault(position, []).append(case_id)
            for position, case_ids in by_position.items():
                item = condition(
                    "exp2",
                    worker=worker,
                    repeat=repeat_id,
                    position=position,
                    domain="factorization",
                )
                for case_id in case_ids:
                    roots.append(
                        _root(
                            condition=item,
                            case_id=case_id,
                            ordinal=len(roots),
                            factor=factor,
                            lean=lean,
                        )
                    )

    if profile.profile_id == "full":
        factor_by_difficulty: dict[str, list[str]] = {}
        for case_id in EXP3_FACTORIZATION_CASE_IDS:
            factor_by_difficulty.setdefault(
                str(factor[case_id]["paper_difficulty"]), []
            ).append(case_id)
        lean_by_topic = {
            str(lean[case_id]["topic_family"]): [case_id]
            for case_id in EXP3_LEAN_CASE_IDS
        }
        rate_groups = (
            [("factorization", key, None, value, (1, 5, 10, 25, 50, 100))
             for key, value in factor_by_difficulty.items()]
            + [("lean", None, key, value, (10, 50, 100))
               for key, value in lean_by_topic.items()]
        )
        for domain, difficulty, topic, case_ids, rates in rate_groups:
            for fault in EXP3_FAULT_TYPES:
                for rate in rates:
                    for repeat_id in (0, 1):
                        dimensions = {
                            "domain": domain,
                            "fault": fault,
                            "rate": rate,
                            "repeat": repeat_id,
                        }
                        if difficulty is not None:
                            dimensions["difficulty"] = difficulty
                        if topic is not None:
                            dimensions["topic"] = topic
                        item = condition("exp3", **dimensions)
                        for case_id in case_ids:
                            roots.append(
                                _root(
                                    condition=item,
                                    case_id=case_id,
                                    ordinal=len(roots),
                                    factor=factor,
                                    lean=lean,
                                )
                            )
        death_groups = [
            ("factorization", key, None, value)
            for key, value in factor_by_difficulty.items()
        ] + [
            ("lean", None, key, value) for key, value in lean_by_topic.items()
        ]
        for domain, difficulty, topic, case_ids in death_groups:
            for dead in (1, 3):
                for progress in (25, 50, 75):
                    for repeat_id in (0, 1):
                        dimensions = {
                            "domain": domain,
                            "dead": dead,
                            "progress": progress,
                            "repeat": repeat_id,
                        }
                        if difficulty is not None:
                            dimensions["difficulty"] = difficulty
                        if topic is not None:
                            dimensions["topic"] = topic
                        item = condition("exp3", **dimensions)
                        for case_id in case_ids:
                            roots.append(
                                _root(
                                    condition=item,
                                    case_id=case_id,
                                    ordinal=len(roots),
                                    factor=factor,
                                    lean=lean,
                                )
                            )
        reference_ids = EXP3_FACTORIZATION_CASE_IDS + EXP3_LEAN_CASE_IDS
        reference_repeats = (0, 1)
    else:
        factor_case = "factor_v2_hard_145"
        lean_case = "lean_v2_simple_pure_logic_direct_prop_01"
        for fault in EXP3_FAULT_TYPES:
            item = condition(
                "exp3", domain="factorization", difficulty="hard",
                fault=fault, rate=100, repeat=0,
            )
            roots.append(
                _root(
                    condition=item, case_id=factor_case, ordinal=len(roots),
                    factor=factor, lean=lean,
                )
            )
        for dead, progress in ((1, 25), (3, 75)):
            item = condition(
                "exp3", domain="factorization", difficulty="hard",
                dead=dead, progress=progress, repeat=0,
            )
            roots.append(
                _root(
                    condition=item, case_id=factor_case, ordinal=len(roots),
                    factor=factor, lean=lean,
                )
            )
        item = condition(
            "exp3", domain="lean", topic="pure_logic",
            fault="false_positive", rate=100, repeat=0,
        )
        roots.append(
            _root(
                condition=item, case_id=lean_case, ordinal=len(roots),
                factor=factor, lean=lean,
            )
        )
        reference_ids = (factor_case, lean_case)
        reference_repeats = (0,)

    for repeat_id in reference_repeats:
        for case_id in reference_ids:
            domain, difficulty, topic, _units, _row = _case_data(case_id, factor, lean)
            dimensions = {"domain": domain}
            if domain == "factorization":
                dimensions["difficulty"] = difficulty
            else:
                dimensions["topic"] = topic
            dimensions["repeat"] = repeat_id
            controls = profile.experiments["exp3"]
            ref_condition = ConditionV1(
                condition_id=_condition_id("exp3-reference", **dimensions),
                experiment_id="exp3",
                domain=domain,
                difficulty=difficulty,
                topic_family=topic,
                repeat_id=repeat_id,
                worker_count=10,
                max_retries=controls.max_retries,
                continue_after_terminal_child_failure=False,
                source_repeat_id=0,
            )
            references.append(
                _root(
                    condition=ref_condition,
                    case_id=case_id,
                    ordinal=len(references),
                    factor=factor,
                    lean=lean,
                    is_paper_denominator=False,
                )
            )

    challenges = _challenge_plans(profile.profile_id, factor, lean)
    plans_by_repeat: dict[int, list[ChallengePlanV1]] = {}
    for plan in challenges:
        plans_by_repeat.setdefault(plan.repeat_id, []).append(plan)
    for mode in EXP4_MODES:
        for repeat_id, plans in plans_by_repeat.items():
            item = condition("exp4", mode=mode, repeat=repeat_id, domain="mixed")
            for plan in plans:
                roots.append(
                    _root(
                        condition=item,
                        case_id=plan.case_id,
                        ordinal=len(roots),
                        factor=factor,
                        lean=lean,
                        challenge_plan_id=plan.challenge_plan_id,
                    )
                )

    model_ids = (
        "zai-org/GLM-5.2",
        "Qwen/Qwen3-14B",
        "MiniMaxAI/MiniMax-M2.5",
        "Pro/deepseek-ai/DeepSeek-V3",
    )
    exp5_ids = (
        EXP5_FACTORIZATION_CASE_IDS + EXP5_LEAN_CASE_IDS
        if profile.profile_id == "full"
        else ("factor_v2_hard_145",)
    )
    exp5_groups: dict[tuple[str, str | None], list[str]] = {}
    for case_id in exp5_ids:
        domain, _difficulty, topic, _units, _row = _case_data(case_id, factor, lean)
        exp5_groups.setdefault((domain, topic), []).append(case_id)
    model_order_by_repeat = {
        0: model_ids,
        1: (model_ids[1], model_ids[3], model_ids[0], model_ids[2]),
        2: (model_ids[2], model_ids[0], model_ids[3], model_ids[1]),
    }
    for repeat_id in profile.experiments["exp5"].repeat_ids:
        for model_id in model_order_by_repeat[repeat_id]:
            for (domain, topic), case_ids in exp5_groups.items():
                dimensions = {
                    "model": model_id,
                    "repeat": repeat_id,
                    "domain": domain,
                    "difficulty": "hard",
                }
                if topic is not None:
                    dimensions["topic"] = topic
                item = condition("exp5", **dimensions)
                for case_id in case_ids:
                    roots.append(
                        _root(
                            condition=item,
                            case_id=case_id,
                            ordinal=len(roots),
                            factor=factor,
                            lean=lean,
                        )
                    )

    return InventoryV1(
        profile_id=profile.profile_id,
        conditions=tuple(conditions),
        roots=tuple(roots),
        references=tuple(references),
        challenges=challenges,
    )


def _planned_ai_unit_ids(
    root: RootRunV1,
    factor: Mapping[str, dict[str, Any]],
    lean: Mapping[str, dict[str, Any]],
) -> list[str]:
    if root.domain == "factorization":
        catalog_count = int(
            factor[root.case_id]["split_params"]["requested_child_count"]
        )
        if catalog_count != root.planned_ai_unit_count:
            raise ValueError(
                f"factor catalog contradicts root count for {root.root_run_id}"
            )
        planned = [f"range_{index}" for index in range(root.planned_ai_unit_count)]
    elif root.domain == "lean":
        order = lean[root.case_id]["merge_plan_shape"]["dependency_order"]
        if not isinstance(order, list) or any(
            not isinstance(item, str) or not item for item in order
        ):
            raise ValueError(f"Lean case {root.case_id} has invalid dependency order")
        planned = list(order)
    else:
        raise ValueError(f"unknown inventory domain: {root.domain}")
    if (
        len(planned) != root.planned_ai_unit_count
        or len(planned) != len(set(planned))
    ):
        raise ValueError(
            f"planned AI unit IDs contradict root count for {root.root_run_id}"
        )
    return planned


def _disabled_mechanisms(mode: str | None) -> list[str]:
    if mode in (None, "FULL"):
        return []
    parts = mode.split("__")
    try:
        return [_EXP4_DISABLED_NAMES[part] for part in parts]
    except KeyError as exc:
        raise ValueError(f"unknown Exp4 mode: {mode}") from exc


def _provider_identity(condition: ConditionV1 | None) -> tuple[str, str]:
    if condition is not None and condition.experiment_id == "exp5":
        if condition.model_id not in _EXP5_PROVIDER_ENTRIES:
            raise ValueError(f"unknown Exp5 model: {condition.model_id}")
        assert condition.model_id is not None
        return _EXP5_PROVIDER_ENTRIES[condition.model_id], condition.model_id
    return "deepseek_v4_pro_exp1_baseline", "deepseek-v4-pro"


def _project_root_inventory(
    root: RootRunV1,
    condition: ConditionV1 | None,
    factor: Mapping[str, dict[str, Any]],
    lean: Mapping[str, dict[str, Any]],
) -> RootInventoryV1:
    provider_entry_id, configured_model = _provider_identity(condition)
    row = RootInventoryV1(
        experiment_id=root.experiment_id,
        condition_id=root.condition_id,
        case_id=root.case_id,
        repeat_id=root.repeat_id,
        domain=root.domain,
        difficulty=root.difficulty,
        topic_family=root.topic_family,
        position_stratum=(condition.position_stratum if condition else None),
        worker_count=root.worker_count,
        mode=(condition.mode if condition else None),
        disabled_mechanisms=_disabled_mechanisms(condition.mode if condition else None),
        fault_type=(condition.fault_type if condition else None),
        fault_rate=(
            condition.fault_rate_percent / 100
            if condition is not None and condition.fault_rate_percent is not None
            else None
        ),
        dead_worker_count=(condition.dead_worker_count if condition else None),
        kill_progress_target_ratio=(
            condition.kill_progress_percent / 100
            if condition is not None and condition.kill_progress_percent is not None
            else None
        ),
        provider_entry_id=provider_entry_id,
        configured_model=configured_model,
        planned_ai_unit_ids=_planned_ai_unit_ids(root, factor, lean),
        challenge_plan_id=root.challenge_plan_id,
    )
    row.validate()
    return row


def project_root_inventory_rows(inventory: InventoryV1) -> RootInventoryRowsV1:
    """把 plan DTO 显式投影为 projector/reducer 共用的 typed rows。"""

    factor, lean = _catalogs()
    conditions = {item.condition_id: item for item in inventory.conditions}
    if any(root.condition_id not in conditions for root in inventory.roots):
        raise ValueError("paper root inventory references an unknown condition")
    roots = tuple(
        _project_root_inventory(root, conditions[root.condition_id], factor, lean)
        for root in inventory.roots
    )
    references = tuple(
        _project_root_inventory(root, None, factor, lean)
        for root in inventory.references
    )
    return RootInventoryRowsV1(roots=roots, exp3_references=references)


@dataclass(frozen=True, slots=True)
class ExperimentPlanV1:
    experiment_id: str
    paper_root_count: int
    reference_root_count: int
    planned_first_attempt_ai_units: int
    protocol_execution_attempt_upper: int
    provider_call_upper: int


@dataclass(frozen=True, slots=True)
class PlanV1:
    profile_id: str
    experiments: Mapping[str, ExperimentPlanV1]
    paper_root_count: int
    execution_root_count: int
    online_provider_call_upper: int
    exp3_reference_planned_first_attempt_ai_units: int
    exp3_reference_protocol_execution_attempt_upper: int
    estimated_response_bytes: int
    hard_response_bytes: int
    estimate_bytes: int
    hard_upper_bytes: int
    online_response_artifact_hard_upper_bytes: int
    per_root_free_space_margin_bytes: int

    @property
    def estimate_gib(self) -> float:
        return self.estimate_bytes / 1024**3

    @property
    def hard_upper_gib(self) -> float:
        return self.hard_upper_bytes / 1024**3

    @property
    def online_response_artifact_hard_upper_gib(self) -> float:
        return self.online_response_artifact_hard_upper_bytes / 1024**3

    @property
    def per_root_free_space_margin_gib(self) -> float:
        return self.per_root_free_space_margin_bytes / 1024**3


def build_plan(
    profile_id: str | ProfileV1,
    *,
    representative_raw_response_p95_bytes: int | None = None,
) -> PlanV1:
    """只读 profile/catalog，计算调用量、attempt 与磁盘双估算。"""

    profile = build_profile(profile_id) if isinstance(profile_id, str) else profile_id
    inventory = build_inventory(profile)
    conditions_by_id = {
        condition.condition_id: condition for condition in inventory.conditions
    }
    roots_by_experiment = {
        experiment_id: tuple(
            root for root in inventory.roots if root.experiment_id == experiment_id
        )
        for experiment_id in EXPERIMENT_ORDER
    }
    references = tuple(inventory.references)

    planned = {
        experiment_id: sum(root.planned_ai_unit_count for root in roots)
        for experiment_id, roots in roots_by_experiment.items()
    }
    protocol_upper = {
        "exp1": planned["exp1"] * 3,
        "exp2": planned["exp2"] * 3,
        "exp3": sum(
            root.planned_ai_unit_count
            * (
                4
                if conditions_by_id[root.condition_id].dead_worker_count is not None
                else 3
            )
            for root in roots_by_experiment["exp3"]
        ),
        "exp4": sum(
            root.planned_ai_unit_count
            * (
                1
                if conditions_by_id[root.condition_id].mode in EXP4_NO_REQUEUE_MODES
                else 2
            )
            for root in roots_by_experiment["exp4"]
        ),
        "exp5": planned["exp5"],
    }

    provider_upper = {
        "exp1": planned["exp1"] * 3,
        "exp2": 0,
        "exp3": 0,
        "exp4": 0,
        "exp5": planned["exp5"],
    }
    reference_planned = sum(root.planned_ai_unit_count for root in references)
    reference_attempt_upper = reference_planned * 3
    experiments = {
        experiment_id: ExperimentPlanV1(
            experiment_id=experiment_id,
            paper_root_count=len(roots_by_experiment[experiment_id]),
            reference_root_count=(len(references) if experiment_id == "exp3" else 0),
            planned_first_attempt_ai_units=planned[experiment_id],
            protocol_execution_attempt_upper=protocol_upper[experiment_id],
            provider_call_upper=provider_upper[experiment_id],
        )
        for experiment_id in EXPERIMENT_ORDER
    }
    online_upper = sum(item.provider_call_upper for item in experiments.values())
    if representative_raw_response_p95_bytes is None:
        response_bytes = 1024**2
    else:
        if representative_raw_response_p95_bytes < 0:
            raise ValueError("representative raw-response p95 bytes must be nonnegative")
        response_bytes = ceil(1.5 * representative_raw_response_p95_bytes)
    response_bytes = max(64 * 1024, min(16 * 1024**2, response_bytes))
    hard_response_bytes = 16 * 1024**2
    trace_attempt_upper = sum(protocol_upper.values()) + reference_attempt_upper

    def disk_bytes(response_bound: int) -> int:
        subtotal = (
            online_upper * (2 * response_bound + 24 * 1024)
            + inventory.execution_root_count * 64 * 1024
            + trace_attempt_upper * 12 * 1024
        )
        return ceil(1.25 * subtotal)

    online_hard_upper = ceil(
        1.25 * online_upper * (2 * hard_response_bytes + 24 * 1024)
    )
    return PlanV1(
        profile_id=profile.profile_id,
        experiments=experiments,
        paper_root_count=inventory.paper_root_count,
        execution_root_count=inventory.execution_root_count,
        online_provider_call_upper=online_upper,
        exp3_reference_planned_first_attempt_ai_units=reference_planned,
        exp3_reference_protocol_execution_attempt_upper=reference_attempt_upper,
        estimated_response_bytes=response_bytes,
        hard_response_bytes=hard_response_bytes,
        estimate_bytes=disk_bytes(response_bytes),
        hard_upper_bytes=disk_bytes(hard_response_bytes),
        online_response_artifact_hard_upper_bytes=online_hard_upper,
        per_root_free_space_margin_bytes=max(512 * 1024**2, 4 * response_bytes),
    )
