"""End-to-end demo of the query engine: QUESTION -> PLAN -> PROOF STATE -> MCP tools ->
MULTI-HOP -> DETERMINISTIC COMPUTATION -> VERIFY -> REPLAN -> ANSWER.

No live LLM key exists in this environment, so this demo uses the same kind of
provider double the test suite uses (tests/_fixtures.py + a scenario-specific planning
provider) instead of a real LLM. Everything else - Chunk4Pipeline, WorldModelBuilder,
DuckDB, NetworkX, RapidFuzz, entity resolution, the ToolDispatcher/MCP boundary, the
executor, and the verifier - is the real production code, unmodified for this demo.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from jaw_ingest.mcp_tools import ToolDispatcher
from jaw_ingest.planner import QueryPlanner
from jaw_ingest.query_engine import QueryEngine

from _fixtures import ScenarioExtractionProvider, build_scenario_system

QUESTION = "What is the total contract value of all projects commissioned by Metro Authority?"

_BROKEN_PLAN = {
    "question": QUESTION,
    "understanding": "Sum contract_value over projects commissioned by Metro Authority.",
    "operations": [
        {"output_var": "client", "op_type": "RESOLVE_ENTITY", "params": {"query": "Metro Authority", "entity_type": "client"}, "depends_on": [], "description": "resolve the client"},
        {"output_var": "projects", "op_type": "ENUMERATE", "params": {"entity_type": "project", "predicate": "commissioned_by", "anchor_var": "$client", "direction": "in"}, "depends_on": ["client"], "description": "all projects for this client"},
        {"output_var": "values", "op_type": "GET_ATTRIBUTE", "params": {"input_var": "$projects", "predicate": "totally_wrong_predicate_xyz"}, "depends_on": ["projects"], "description": "get contract values (deliberately wrong predicate, to force a replan)"},
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


class DemoProvider:
    def __init__(self) -> None:
        self.extraction = ScenarioExtractionProvider()
        self.plan_calls = 0

    def complete(self, system: str, user: str, response_schema: dict) -> dict:
        if system.startswith("You extract structured facts"):
            return self.extraction.complete(system, user, response_schema)
        if system.startswith("You turn a natural-language question"):
            self.plan_calls += 1
            if "A previous plan was executed and failed verification." in user:
                print(f"  [replan #{self.plan_calls - 1}] planner given failure info, proposing a corrected plan\n")
                return _FIXED_PLAN
            print("  [initial plan] planner proposes a plan (deliberately using the wrong attribute predicate)\n")
            return _BROKEN_PLAN
        if system.startswith("You write one concise"):
            return {"sentence": "The total contract value for Metro Authority's projects is 300,000,000."}
        return {}


def main() -> None:
    tmp_path = Path(tempfile.mkdtemp())
    provider = DemoProvider()

    print(f"Building world model from a synthetic scenario (person->led->project->commissioned_by->client, plus contract_value attributes)...\n")
    system = build_scenario_system(tmp_path, provider=provider.extraction)
    print("World model coverage:", system.world_model.coverage(), "\n")

    dispatcher = ToolDispatcher(system)
    planner = QueryPlanner(provider)
    engine = QueryEngine(dispatcher, planner, max_iterations=3, answer_provider=provider)

    print(f"QUESTION: {QUESTION}\n")
    result = engine.run(QUESTION)

    print(f"Iterations used: {result.iterations_used}")
    print(f"Verification passed: {result.verification.passed}  (issues: {result.verification.issues})\n")
    print("Proof trail:")
    print(result.proof_state.explain())
    print()
    print(f"ANSWER: {result.final_answer.answer}")
    print(f"STATUS: {result.final_answer.status}  (confidence: {result.final_answer.confidence:.2f})\n")
    print("Evidence:")
    for citation in result.final_answer.evidence:
        print(f"  - [{citation.evidence_id}] {citation.document_id}: {citation.text!r}")


if __name__ == "__main__":
    main()
