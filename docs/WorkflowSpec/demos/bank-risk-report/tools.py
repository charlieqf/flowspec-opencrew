"""Deterministic mock tools for the FlowSpec bank-risk-report demo."""

from __future__ import annotations

from typing import Any


def done(artifacts: dict[str, Any], patch: dict[str, Any] | None = None, usage: list[dict[str, Any]] | None = None):
    return {"status": "completed", "artifacts": artifacts, "context_patch": patch or {}, "usage": usage or []}


def _snapshot(ctx, count_key: str, kind: str):
    count = int(ctx.variables[count_key])
    payload = {"entity_id": ctx.variables["entity_id"], "period": ctx.variables["period"], "data_cut_id": ctx.variables["data_cut_id"], "row_count": count, "snapshot_hash": f"mock:{kind}:{ctx.variables['data_cut_id']}:{count}"}
    return payload, count


def freeze_transactions(ctx):
    payload, count = _snapshot(ctx, "transaction_count_fixture", "transactions")
    return done({"TransactionsSnapshot.json": payload}, {"transaction_count": count})


def freeze_payments(ctx):
    payload, count = _snapshot(ctx, "payment_count_fixture", "payments")
    return done({"PaymentsSnapshot.json": payload}, {"payment_count": count})


def freeze_gl_controls(ctx):
    total = float(ctx.variables["gl_total_fixture"])
    return done({"GLControlTotals.json": {"entity_id": ctx.variables["entity_id"], "period": ctx.variables["period"], "data_cut_id": ctx.variables["data_cut_id"], "control_total": total}}, {"gl_total": total})


def reconcile_quality(ctx):
    rate = float(ctx.variables["discrepancy_rate_fixture"])
    passed = rate <= 0.01
    waiver_required = not passed
    issues = [] if passed else [{"code": "PAYMENT_POPULATION_BREAK", "severity": "high", "rate": rate}]
    return done(
        {
            "DQReport.json": {"passed": passed, "discrepancy_rate": rate, "issues": issues, "rule_version": "dq-rules-2026.06"},
            "Reconciliation.json": {"transaction_count": ctx.variables["transaction_count"], "payment_count": ctx.variables["payment_count"], "gl_total": ctx.variables["gl_total"], "status": "matched" if passed else "break"}
        },
        {"dq_passed": passed, "waiver_required": waiver_required},
    )


def review_dq_waiver(ctx):
    fixture = ctx.human_decision()
    return done({"DQWaiverDecision.json": {"decision": fixture["decision"], "reason": fixture["reason"], "actor": fixture["actor"]}}, {"waiver_decision": fixture["decision"]})


def finalize_quality_decision(ctx):
    accepted = bool(ctx.variables["dq_passed"]) or (bool(ctx.variables["waiver_required"]) and ctx.variables["waiver_decision"] == "approved")
    return done({"QualityDecision.json": {"accepted": accepted, "basis": "dq_pass" if ctx.variables["dq_passed"] else "waiver" if accepted else "rejected_waiver", "waiver_applied": accepted and ctx.variables["waiver_required"]}}, {"dq_accepted": accepted})


def prepare_clean_dataset(ctx):
    return done({"CleanDataset.json": {"entity_id": ctx.variables["entity_id"], "period": ctx.variables["period"], "data_cut_id": ctx.variables["data_cut_id"], "quality_basis": ctx.artifact("QualityDecision.json")}})


def calculate_risk_metrics(ctx):
    transaction_count = int(ctx.variables["transaction_count"])
    payment_count = int(ctx.variables["payment_count"])
    metrics = {"payment_coverage": round(payment_count / max(transaction_count, 1), 6), "mock_var_99": 18400000, "stage_3_ratio": 0.038}
    return done({"RiskMetrics.json": {"metric_version": "risk-metrics-4.7", "metrics": metrics, "definition_refs": {key: f"metric://{key}/v1" for key in metrics}}}, {"metric_version": "risk-metrics-4.7"})


def draft_grounded_narrative(ctx):
    metrics = ctx.artifact("RiskMetrics.json")
    return done(
        {"RiskNarrative.json": {"sections": [{"heading": "Portfolio movement", "text": "Payment coverage remained within the controlled reporting threshold.", "metric_refs": ["payment_coverage"]}, {"heading": "Credit risk", "text": "Stage 3 exposure is reported directly from the frozen metric set.", "metric_refs": ["stage_3_ratio", "mock_var_99"]}], "citations": metrics["definition_refs"], "mock_model_output": True}},
        usage=[{"reserve_amount": "0.030", "cost_amount": "0.021", "usage": {"measurement_status": "locally_measured", "input_tokens": 6430, "output_tokens": 812, "cached_input_tokens": 1200, "total_tokens": 7242}}],
    )


def build_compliance_appendix(ctx):
    return done({"ComplianceAppendix.pdf": {"document_type": "compliance_appendix", "version": "1", "included_refs": [ctx.variables["data_cut_id"], "dq-rules-2026.06", "risk-metrics-4.7"]}})


def assemble_risk_report(ctx):
    return done({"RiskReport.pdf": {"document_type": "monthly_risk_report", "version": f"{ctx.variables['period']}-{ctx.variables['data_cut_id']}", "included_refs": ["RiskMetrics.json", "RiskNarrative.json", "ComplianceAppendix.pdf"]}})


def review_report_signoff(ctx):
    fixture = ctx.human_decision()
    return done({"ReportApproval.json": {"decision": fixture["decision"], "actor": fixture["actor"], "reason": fixture["reason"], "report_hash": "mock:RiskReport.pdf"}}, {"publish_decision": fixture["decision"]})


def distribute_report(ctx):
    distributed_at = "2026-07-24T04:20:00Z"
    return done({"DistributionReceipt.json": {"recipient_set": ["board-risk-committee", "regulatory-reporting"], "operation_key": f"distribute:{ctx.variables['entity_id']}:{ctx.variables['period']}:{ctx.run_id}", "status": "mock-delivered", "distributed_at": distributed_at}}, {"distributed_at": distributed_at})


def request_data_remediation(ctx):
    return done({"DataRemediationRequest.json": {"entity_id": ctx.variables["entity_id"], "period": ctx.variables["period"], "issues": ctx.artifact("DQReport.json")["issues"], "status": "open"}})


def request_report_revision(ctx):
    approval = ctx.artifact("ReportApproval.json")
    return done(
        {
            "ReportRevisionRequest.json": {
                "entity_id": ctx.variables["entity_id"],
                "period": ctx.variables["period"],
                "data_cut_id": ctx.variables["data_cut_id"],
                "decision": ctx.variables["publish_decision"],
                "requested_changes": [approval["reason"]],
                "status": "open",
            }
        }
    )
