from __future__ import annotations

from pathlib import Path

from jaw_ingest.llm_provider import NullProvider
from jaw_ingest.mcp_tools import ToolDispatcher
from jaw_ingest.planner import QueryPlanner
from jaw_ingest.query_engine import QueryEngine

from _fixtures import ScenarioExtractionProvider, build_scenario_system

QUESTION = "What is the total contract value of all projects commissioned by Metro Authority?"

# Deliberately wrong on the first attempt: GET_ATTRIBUTE uses a predicate that won't
# fuzzy-match "contract_value", so the AGGREGATE step gets nothing and verification
# must fail - exercising a genuine replan, not a scripted one.
_BROKEN_PLAN = {
    "question": QUESTION,
    "understanding": "Sum contract_value over projects commissioned by Metro Authority.",
    "operations": [
        {"output_var": "client", "op_type": "RESOLVE_ENTITY", "params": {"query": "Metro Authority", "entity_type": "client"}, "depends_on": [], "description": "resolve the client"},
        {"output_var": "projects", "op_type": "ENUMERATE", "params": {"entity_type": "project", "predicate": "commissioned_by", "anchor_var": "$client", "direction": "in"}, "depends_on": ["client"], "description": "all projects for this client"},
        {"output_var": "values", "op_type": "GET_ATTRIBUTE", "params": {"input_var": "$projects", "predicate": "totally_wrong_predicate_xyz"}, "depends_on": ["projects"], "description": "get contract values"},
        {"output_var": "total", "op_type": "AGGREGATE", "params": {"input_var": "$values", "function": "sum"}, "depends_on": ["values"], "description": "sum the values"},
        {"output_var": "answer", "op_type": "RETURN", "params": {"input_var": "$total"}, "depends_on": ["total"], "description": "final answer"},
    ],
    "final_var": "answer",
}

_FIXED_PLAN = {
    "question": QUESTION,
    "understanding": "Sum contract_value over projects commissioned by Metro Authority.",
    "operations": [
        {"output_var": "client", "op_type": "RESOLVE_ENTITY", "params": {"query": "Metro Authority", "entity_type": "client"}, "depends_on": [], "description": "resolve the client"},
        {"output_var": "projects", "op_type": "ENUMERATE", "params": {"entity_type": "project", "predicate": "commissioned_by", "anchor_var": "$client", "direction": "in"}, "depends_on": ["client"], "description": "all projects for this client"},
        {"output_var": "values", "op_type": "GET_ATTRIBUTE", "params": {"input_var": "$projects", "predicate": "contract_value"}, "depends_on": ["projects"], "description": "get contract values"},
        {"output_var": "total", "op_type": "AGGREGATE", "params": {"input_var": "$values", "function": "sum"}, "depends_on": ["values"], "description": "sum the values"},
        {"output_var": "answer", "op_type": "RETURN", "params": {"input_var": "$total"}, "depends_on": ["total"], "description": "final answer"},
    ],
    "final_var": "answer",
}


class ScenarioEngineProvider:
    """Routes to extraction / planning / replanning / answer-phrasing behavior by
    inspecting which system prompt is in play - mirroring how one real LLM provider
    would serve all four roles for the CLI in production.
    """

    def __init__(self) -> None:
        self.extraction = ScenarioExtractionProvider()
        self.plan_calls = 0

    def complete(self, system: str, user: str, response_schema: dict) -> dict:
        if system.startswith("You extract structured facts"):
            return self.extraction.complete(system, user, response_schema)
        if system.startswith("You turn a natural-language question"):
            self.plan_calls += 1
            if "A previous plan was executed and failed verification." in user:
                return _FIXED_PLAN
            return _BROKEN_PLAN
        if system.startswith("You write one concise"):
            return {"sentence": "The total contract value for Metro Authority's projects is 300,000,000."}
        return {}


def _build_engine(tmp_path: Path, provider: ScenarioEngineProvider) -> QueryEngine:
    system = build_scenario_system(tmp_path, provider=provider.extraction)
    dispatcher = ToolDispatcher(system)
    planner = QueryPlanner(provider)
    return QueryEngine(dispatcher, planner, max_iterations=3, answer_provider=provider)


def test_full_pipeline_replans_and_answers_correctly(tmp_path: Path) -> None:
    provider = ScenarioEngineProvider()
    engine = _build_engine(tmp_path, provider)

    result = engine.run(QUESTION)

    assert provider.plan_calls == 2, "expected exactly one initial plan + one replan"
    assert result.iterations_used == 2
    assert result.verification.passed
    assert result.final_answer.status == "verified"
    # Bridge Alpha's contract_value now resolves via the deterministic Fact (150000000)
    # rather than the LLM-transcribed Attribute (100000000) - see test_mcp_tools.py's
    # facts-priority tests. 150000000 + 200000000 (Bridge Beta, LLM-only) = 350000000.
    assert result.final_answer.answer == 350000000.0
    assert result.final_answer.evidence, "verified answer must carry evidence citations"
    assert all(c.document_id for c in result.final_answer.evidence)


def test_proof_state_has_full_trace_and_bindings(tmp_path: Path) -> None:
    provider = ScenarioEngineProvider()
    engine = _build_engine(tmp_path, provider)

    result = engine.run(QUESTION)

    var_names = {step.output_var for step in result.proof_state.trace}
    assert {"client", "projects", "values", "total", "answer"} <= var_names
    assert result.proof_state.bindings["total"] == 350000000.0
    # Bridge Gamma's client-mismatched project must never enter the population.
    assert len(result.proof_state.bindings["projects"]) == 2


def test_engine_reports_insufficient_evidence_with_no_llm_provider(tmp_path: Path) -> None:
    extraction_provider = ScenarioExtractionProvider()
    system = build_scenario_system(tmp_path, provider=extraction_provider)
    dispatcher = ToolDispatcher(system)
    planner = QueryPlanner(NullProvider())
    engine = QueryEngine(dispatcher, planner, max_iterations=3, answer_provider=NullProvider())

    result = engine.run(QUESTION)

    assert result.final_answer.status == "insufficient_evidence"
    assert result.iterations_used == 0
    assert "no_provider_configured" in result.verification.issues[0]


def test_replanning_is_bounded_by_max_iterations(tmp_path: Path) -> None:
    class AlwaysBrokenProvider:
        def __init__(self) -> None:
            self.extraction = ScenarioExtractionProvider()
            self.plan_calls = 0

        def complete(self, system, user, response_schema):
            if system.startswith("You extract structured facts"):
                return self.extraction.complete(system, user, response_schema)
            if system.startswith("You turn a natural-language question"):
                self.plan_calls += 1
                return _BROKEN_PLAN  # never fixes itself
            return {}

    provider = AlwaysBrokenProvider()
    system = build_scenario_system(tmp_path, provider=provider.extraction)
    dispatcher = ToolDispatcher(system)
    planner = QueryPlanner(provider)
    engine = QueryEngine(dispatcher, planner, max_iterations=3)

    result = engine.run(QUESTION)

    assert result.iterations_used == 3
    assert not result.verification.passed
    assert result.final_answer.status in {"unverified", "insufficient_evidence"}
    # plan() + 2 replans (bounded: no replan call is made after the final iteration)
    assert provider.plan_calls == 3
