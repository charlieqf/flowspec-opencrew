"""Deterministic mock tools for the FlowSpec buyer due-diligence demo."""

from __future__ import annotations

from typing import Any


def done(artifacts: dict[str, Any], patch: dict[str, Any] | None = None, usage: list[dict[str, Any]] | None = None):
    return {"status": "completed", "artifacts": artifacts, "context_patch": patch or {}, "usage": usage or []}


def inventory_data_room(ctx):
    documents = ctx.variables["document_manifest_fixture"]
    present = {item["category"] for item in documents}
    missing = sorted(set(ctx.variables["required_categories"]) - present)
    complete = not missing
    return done({"Documents.json": {"matter_id": ctx.variables["matter_id"], "scope_revision": ctx.variables["scope_revision"], "documents": documents, "missing_categories": missing}}, {"document_count": len(documents), "missing_materials": missing, "inventory_complete": complete})


def request_supplement(ctx):
    return done({"SupplementRequest.json": {"matter_id": ctx.variables["matter_id"], "scope_revision": ctx.variables["scope_revision"], "requested_categories": ctx.variables["missing_materials"], "status": "mock-sent"}})


def extract_document_evidence(ctx):
    doc = ctx.item
    facts = [{"fact_id": f"{doc['document_id']}-F1", "statement": f"Controlled mock fact extracted from {doc['name']}", "evidence_span": {"document_id": doc["document_id"], "page": min(3, doc["page_count"]), "bbox": [0.12, 0.18, 0.84, 0.29]}}]
    return done(
        {"Extract_{document_id}.json": {"document_id": doc["document_id"], "category": doc["category"], "facts": facts, "page_count": doc["page_count"], "mock_model_output": True}},
        usage=[{"reserve_amount": "0.018", "cost_amount": "0.014", "usage": {"measurement_status": "locally_measured", "input_tokens": 850 + doc["page_count"] * 4, "output_tokens": 160, "total_tokens": 1010 + doc["page_count"] * 4, "provider_units": {"page_count": doc["page_count"]}}}],
    )


def index_evidence(ctx):
    extracts = ctx.artifact("Extract_*.json")
    if isinstance(extracts, dict):
        extracts = [extracts]
    documents = [{"document_id": item["document_id"], "category": item["category"], "fact_ids": [fact["fact_id"] for fact in item["facts"]]} for item in extracts]
    evidence_count = sum(len(item["facts"]) for item in extracts)
    return done({"EvidenceIndex.json": {"matter_id": ctx.variables["matter_id"], "scope_revision": ctx.variables["scope_revision"], "documents": sorted(documents, key=lambda item: item["document_id"]), "evidence_count": evidence_count}}, {"extracted_count": len(extracts)})


def review_legal_evidence(ctx):
    index = ctx.artifact("EvidenceIndex.json")
    candidates = [{"finding_id": "LEGAL-01", "title": "Change-of-control consent", "severity": "medium", "evidence_refs": ["DOC-002-F1"], "status": "candidate"}]
    return done({"LegalFindings.json": {"discipline": "legal", "candidates": candidates, "evidence_refs": [ref for item in candidates for ref in item["evidence_refs"]], "mock_model_output": True}}, usage=[{"agent_execution_id": f"agent_{ctx.run_id}_legal", "reserve_amount": "0.040", "cost_amount": "0.031", "usage": {"measurement_status": "locally_measured", "input_tokens": 4100, "output_tokens": 620, "reasoning_tokens": 480, "total_tokens": 4720, "provider_units": {"tool_calls": min(8, len(index["documents"]) + 2)}}}])


def review_financial_evidence(ctx):
    candidates = [{"finding_id": "FIN-01", "title": "Working-capital normalization", "severity": "medium", "evidence_refs": ["DOC-003-F1"], "status": "candidate"}]
    return done({"FinancialFindings.json": {"discipline": "financial", "candidates": candidates, "evidence_refs": ["DOC-003-F1"], "mock_model_output": True}}, usage=[{"reserve_amount": "0.032", "cost_amount": "0.024", "usage": {"measurement_status": "locally_measured", "input_tokens": 3600, "output_tokens": 530, "total_tokens": 4130}}])


def synthesize_findings(ctx):
    legal = ctx.artifact("LegalFindings.json")["candidates"]
    financial = ctx.artifact("FinancialFindings.json")["candidates"]
    findings = legal + financial
    return done({"RiskFindings.json": {"findings": findings, "evidence_coverage": 1.0, "candidate_only": True}}, {"finding_count": len(findings)})


def review_findings(ctx):
    fixture = ctx.human_decision()
    findings = ctx.artifact("RiskFindings.json")["findings"]
    return done({"ProfessionalReview.json": {"decision": fixture["decision"], "actor": fixture["actor"], "reason": fixture["reason"], "dispositions": [{"finding_id": item["finding_id"], "disposition": "retain"} for item in findings]}}, {"review_decision": fixture["decision"]})


def issue_dd_report(ctx):
    return done({"DDReport.pdf": {"document_type": "buyer_due_diligence_report", "matter_id": ctx.variables["matter_id"], "target_name": ctx.variables["target_name"], "scope_revision": ctx.variables["scope_revision"], "included_refs": ["EvidenceIndex.json", "RiskFindings.json", "ProfessionalReview.json"]}}, {"report_ref": "DDReport.pdf"})


def request_findings_revision(ctx):
    return done({"FindingsRevisionRequest.json": {"matter_id": ctx.variables["matter_id"], "scope_revision": ctx.variables["scope_revision"], "reason": ctx.artifact("ProfessionalReview.json")["reason"], "status": "open"}})
