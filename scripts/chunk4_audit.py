from __future__ import annotations
import sys
import os
from pathlib import Path
import json
import tempfile
import traceback

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / 'src'
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from jaw_ingest.chunk4 import (
    EmbeddingService,
    QdrantStore,
    DuckDBStore,
    EvidenceCorpus,
    GraphStore,
    LexicalRetriever,
    SemanticRetriever,
    EntityResolver,
    HybridRetriever,
    DoclingAdapter,
    Chunk4Pipeline,
)
from jaw_ingest.evidence import DocumentRecord, Evidence, Fact

out = {"errors": []}

# Helper: sample corpus
from datetime import datetime

def create_sample_evidence(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    document = DocumentRecord(
        document_id="sample",
        filename="sample.pdf",
        source_path=root / "sample.pdf",
        document_type="pdf",
        extension=".pdf",
        size_bytes=0,
        checksum="abc",
        page_count=1,
        sheet_count=None,
        extraction_status="success",
        extraction_version="1.0",
        evidence_schema_version="1.0",
        metadata={},
    )
    evidence = Evidence(
        evidence_id="e1",
        source_type="pdf",
        document_id="sample",
        source_path=root / "sample.pdf",
        filename="sample.pdf",
        extraction_method="native_text",
        content={
            "raw_value": "Contract Value: INR 33.38 Cr",
            "text": "Contract Value: INR 33.38 Cr",
            "metadata": {},
        },
        location={
            "source_type": "pdf",
            "page_number": 1,
            "block_id": "b1",
            "bbox": [0.0, 0.0, 1.0, 1.0],
        },
        metadata={},
    )
    fact = Fact(
        fact_id="f1",
        evidence_id="e1",
        document_id="sample",
        source_path=root / "sample.pdf",
        subject_mention=None,
        predicate="contract_value",
        raw_value="INR 33.38 Cr",
        normalized_value="333800000",
        normalized_type="currency_inr",
        normalized_unit="INR",
        original_unit="INR",
        extraction_method="native_text",
        normalization_method="infer_currency_inr",
        extraction_confidence=None,
        normalization_confidence=1.0,
        validation_status="valid",
        provenance={
            "evidence_id": "e1",
            "document_id": "sample",
            "source_path": str(root / "sample.pdf"),
            "location": {"source_type": "pdf", "page_number": 1, "block_id": "b1", "bbox": [0.0, 0.0, 1.0, 1.0]},
        },
        metadata={},
    )
    for name, items in [("documents.jsonl", [document]), ("evidence.jsonl", [evidence]), ("facts.jsonl", [fact])]:
        with (root / name).open('w', encoding='utf-8') as fh:
            for item in items:
                fh.write(json.dumps(item.model_dump(mode='json'), ensure_ascii=False) + "\n")
    return document, evidence, fact

# 1. Embedding models
try:
    es_default = EmbeddingService(device='cpu')
    emb_report = {
        'default_model_name': getattr(es_default, 'model_name', None),
        'loaded': es_default.model is not None,
        'dimension': es_default.dimension,
        'device': es_default.device,
    }
    # Try encoding a sample
    try:
        vec = es_default.embed_texts(['test embedding'])[0]
        emb_report['encode_ok'] = True
        emb_report['sample_vector_len'] = len(vec)
    except Exception as e:
        emb_report['encode_ok'] = False
        emb_report['encode_error'] = str(e)
except Exception as e:
    emb_report = {'error': str(e), 'trace': traceback.format_exc()}
out['embedding_default'] = emb_report

# Attempt to load likely BGE-M3 identifiers (do not change defaults)
bge_candidates = [
    'BAIR/bge-small-en',
    'bigcode/bge-m3-small',
    'bge-m3',
    'BIGBIRD/bge-m3',
]
bge_results = {}
for cand in bge_candidates:
    try:
        svc = EmbeddingService(model_name=cand, device='cpu')
        loaded = svc.model is not None
        encode_ok = None
        encode_err = None
        try:
            v = svc.embed_texts(['hello world'])[0]
            encode_ok = True
        except Exception as ee:
            encode_ok = False
            encode_err = str(ee)
        bge_results[cand] = {'loaded': loaded, 'dimension': svc.dimension, 'device': svc.device, 'encode_ok': encode_ok, 'encode_err': encode_err}
    except Exception as e:
        bge_results[cand] = {'loaded': False, 'error': str(e), 'trace': traceback.format_exc()}
out['bge_attempts'] = bge_results

# 2. Qdrant: use Chunk4Pipeline to populate and search
try:
    tmp = Path(tempfile.mkdtemp(prefix='chunk4_audit_'))
    doc, evidence, fact = create_sample_evidence(tmp / 'evidence')
    pipeline = Chunk4Pipeline(evidence_root=tmp / 'evidence', duckdb_path=tmp / 'test.db', qdrant_location=':memory:', device='cpu')
    pipeline.index()
    # Perform semantic search
    q = 'contract value amount'
    sem = pipeline.semantic_retriever.search_semantic(q, limit=5)
    qdrant = pipeline.qdrant_store.search(pipeline.embedding_service.embed_texts([q])[0], limit=5)
    out['qdrant_search'] = {'semantic_len': len(sem), 'qdrant_hits': qdrant}
    # Map back ids
    mapped = []
    for hit in qdrant:
        pid = hit.get('payload', {}).get('original_id') or hit.get('payload', {}).get('document_id') or hit.get('id')
        mapped.append({'hit_id': hit.get('id'), 'mapped_original_id': pid})
    out['qdrant_mapped'] = mapped
except Exception as e:
    out['qdrant_error'] = {'err': str(e), 'trace': traceback.format_exc()}

# 3. DuckDB: run COUNT, SUM, AVG, filtering
try:
    store = pipeline.duckdb_store
    counts = {t: store.count(t) for t in ['documents', 'evidence', 'facts']}
    # Example SUM/AVG: none numeric except maybe normalized_value; stored as text; create a safe SUM on numeric cast
    try:
        sum_sql = "SELECT SUM(CAST(normalized_value AS DOUBLE)) as s FROM facts"
        sres = store.query(sum_sql)
        s = sres[0]['s'] if sres else None
    except Exception as e:
        s = str(e)
    try:
        avg_sql = "SELECT AVG(CAST(normalized_value AS DOUBLE)) as a FROM facts"
        ares = store.query(avg_sql)
        a = ares[0]['a'] if ares else None
    except Exception as e:
        a = str(e)
    filter_res = store.query("SELECT * FROM facts WHERE predicate = ?", ('contract_value',))
    out['duckdb'] = {'counts': counts, 'sum_normalized_value': s, 'avg_normalized_value': a, 'filter_hits': filter_res}
except Exception as e:
    out['duckdb_error'] = {'err': str(e), 'trace': traceback.format_exc()}

# 4. NetworkX graph
try:
    graph = pipeline.graph_store
    nodes = graph.entity_count()
    edges = graph.relationship_count()
    # collect entity and relation types
    entity_types = {}
    for nid, data in graph.graph.nodes(data=True):
        t = data.get('type')
        entity_types[t] = entity_types.get(t, 0) + 1
    rel_types = {}
    for u, v, k, data in graph.graph.edges(keys=True, data=True):
        rt = data.get('relation_type')
        rel_types[rt] = rel_types.get(rt, 0) + 1
        # verify provenance/evidence
        if 'evidence_id' not in data:
            out.setdefault('graph_prov_issues', []).append({'edge': k, 'missing': True})
    out['graph'] = {'nodes': nodes, 'edges': edges, 'entity_types': entity_types, 'relation_types': rel_types}
except Exception as e:
    out['graph_error'] = {'err': str(e), 'trace': traceback.format_exc()}

# 5. RapidFuzz tests
try:
    lr = LexicalRetriever()
    corpus = EvidenceCorpus([doc], [evidence], [fact])
    lr.index_corpus(corpus)
    exact = lr.search_exact('Contract Value: INR 33.38 Cr')
    fuzzy = lr.search_fuzzy('contract value inr', limit=5)
    variant = lr.search_fuzzy('Contract Value INR 33.38 Crore', limit=5)
    out['rapidfuzz'] = {'exact': exact, 'fuzzy': fuzzy, 'variant': variant}
except Exception as e:
    out['rapidfuzz_error'] = {'err': str(e), 'trace': traceback.format_exc()}

# 6. Entity resolution statuses
try:
    semsvc = pipeline.embedding_service
    sem = SemanticRetriever(semsvc)
    sem.index_corpus(corpus)
    resolver = EntityResolver(lr, sem, graph)
    r_resolved = resolver.resolve('contract value')
    r_unmatched = resolver.resolve('nonexistent entity 12345')
    out['resolver'] = {'resolved': r_resolved.status, 'unmatched': r_unmatched.status}
except Exception as e:
    out['resolver_error'] = {'err': str(e), 'trace': traceback.format_exc()}

# 7. Hybrid retrieval
try:
    hr = HybridRetriever(lr, sem, graph)
    q = 'contract value'
    lex = lr.search_fuzzy(q, limit=5)
    semr = sem.search_semantic(q, limit=5)
    hyb = hr.hybrid_search(q, limit=5)
    out['hybrid'] = {'lexical_top': lex[:3], 'semantic_top': semr[:3], 'hybrid_top': hyb[:3]}
except Exception as e:
    out['hybrid_error'] = {'err': str(e), 'trace': traceback.format_exc()}

# 8. MCP: enumerate tools and invoke a few via direct calls (attempt server start is environment-dependent)
try:
    from jaw_ingest.chunk4 import MCPToolRegistry
    registry = MCPToolRegistry(pipeline)
    tools = registry.tools()
    tool_names = [t.name for t in tools]
    # invoke search_evidence by calling pipeline.search_evidence
    se = pipeline.search_evidence('contract value', limit=5)
    se_entities = pipeline.graph_store.search_nodes('contract value', limit=5)
    td = pipeline.get_provenance(evidence_id='e1')
    qres = pipeline.query_duckdb('SELECT COUNT(*) AS count FROM evidence')
    out['mcp'] = {'tools': tool_names, 'search_evidence_sample': se, 'search_entities_sample': se_entities, 'get_provenance_sample': td, 'query_duckdb_sample': qres}
except Exception as e:
    out['mcp_error'] = {'err': str(e), 'trace': traceback.format_exc()}

# 9. Docling run on sample PDF
try:
    doc_adapter = DoclingAdapter()
    try:
        doc_res = doc_adapter.extract_pdf(evidence.source_path)
        out['docling'] = {'ok': True, 'pages': len(doc_res.get('pages', []))}
    except Exception as e:
        out['docling'] = {'ok': False, 'error': str(e), 'trace': traceback.format_exc()}
except Exception as e:
    out['docling_error'] = {'err': str(e), 'trace': traceback.format_exc()}

# 10. Idempotency: run pipeline.index() twice and compare counts
try:
    pipeline.index()
    counts_after_second = pipeline.get_coverage()
    out['idempotency'] = {'after_first_index': None, 'after_second_index': counts_after_second}
except Exception as e:
    out['idempotency_error'] = {'err': str(e), 'trace': traceback.format_exc()}

# 11. Dependency versions
import importlib
deps = ['rapidfuzz','duckdb','networkx','qdrant_client','mcp','sentence_transformers','transformers','torch','docling']
versions = {}
for d in deps:
    try:
        m = importlib.import_module(d)
        versions[d] = getattr(m, '__version__', str(m.__dict__.get('VERSION', 'unknown')))
    except Exception as e:
        versions[d] = f'not installed: {e}'
out['versions'] = versions

# 12. Full test run will be run separately; just record command
out['test_command'] = 'pytest -q'

print(json.dumps(out, indent=2))
