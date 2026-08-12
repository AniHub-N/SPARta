"""JAW ingestion package."""
from .config import Settings, configure_logging
from .extraction import extract_document, extract_pdf_document, extract_xlsx_workbook
from .models import BaseDocument as Document, PDFDocument, Workbook, Page, EvidenceBlock, Sheet, Cell
from .utils import normalize_currency, normalize_number, parse_date
from .cache import CacheManager
from .chunk4 import Chunk4Pipeline
from .evidence_builder import EvidenceBuilder
from .evidence_cli import main as evidence_main
from .entity_resolution import EntityResolver, EntityResolutionResult, ResolverConfig
from .llm_provider import CachingLLMProvider, LLMProvider, NullProvider, OpenAICompatibleProvider, build_provider_from_settings
from .semantic_extraction import SemanticExtractor
from .semantic_schemas import Attribute, CanonicalEntity, EntityMention, ExtractionFailure, Relationship, SemanticExtractionResult
from .world_model import WorldModelBuilder
from .system import JAWSystem, build_system
from .mcp_tools import ToolDispatcher
from .planner import QueryPlanner
from .executor import MultiHopExecutor
from .verifier import Verifier
from .answer import synthesize_answer
from .query_engine import QueryEngine
from .query_schemas import (
    FinalAnswer,
    Operation,
    PlanningFailure,
    ProofState,
    ProofStep,
    QueryPlan,
    QueryResult,
    VerificationResult,
)
from .document_index import load_document_index
from .answer_coercion import coerce_numeric_answer, format_submission_value

__all__ = [
    "Settings",
    "configure_logging",
    "extract_document",
    "extract_pdf_document",
    "extract_xlsx_workbook",
    "Document",
    "PDFDocument",
    "Workbook",
    "Page",
    "EvidenceBlock",
    "Sheet",
    "Cell",
    "normalize_currency",
    "normalize_number",
    "parse_date",
    "CacheManager",
    "EvidenceBuilder",
    "Chunk4Pipeline",
    "evidence_main",
    "EntityResolver",
    "EntityResolutionResult",
    "ResolverConfig",
    "LLMProvider",
    "NullProvider",
    "OpenAICompatibleProvider",
    "CachingLLMProvider",
    "build_provider_from_settings",
    "SemanticExtractor",
    "Attribute",
    "CanonicalEntity",
    "EntityMention",
    "ExtractionFailure",
    "Relationship",
    "SemanticExtractionResult",
    "WorldModelBuilder",
    "JAWSystem",
    "build_system",
    "ToolDispatcher",
    "QueryPlanner",
    "MultiHopExecutor",
    "Verifier",
    "synthesize_answer",
    "QueryEngine",
    "FinalAnswer",
    "Operation",
    "PlanningFailure",
    "ProofState",
    "ProofStep",
    "QueryPlan",
    "QueryResult",
    "VerificationResult",
    "load_document_index",
    "coerce_numeric_answer",
    "format_submission_value",
]
