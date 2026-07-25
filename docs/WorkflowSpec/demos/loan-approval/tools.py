"""Deterministic mock tools for the FlowSpec loan-approval demo."""

from __future__ import annotations

from typing import Any


def _completed(artifacts: dict[str, Any], patch: dict[str, Any] | None = None, usage: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {"status": "completed", "artifacts": artifacts, "context_patch": patch or {}, "usage": usage or []}


def validate_application(ctx):
    valid = bool(ctx.variables["application_id"] and ctx.variables["applicant_name"] and ctx.variables["loan_amount"] > 0 and ctx.variables["stated_income"] > 0)
    return _completed(
        {"ValidatedApplication.json": {"application_id": ctx.variables["application_id"], "valid": valid, "validation_errors": []}},
        {"application_valid": valid},
    )


def extract_application_evidence(ctx):
    return _completed(
        {
            "ApplicationEvidence.json": {
                "application_id": ctx.variables["application_id"],
                "document_revision": ctx.variables["document_revision"],
                "facts": [
                    {"field": "stated_income", "value": ctx.variables["stated_income"], "evidence_span": "payslip:p1:L8-L12"},
                    {"field": "loan_amount", "value": ctx.variables["loan_amount"], "evidence_span": "application:p1:L21"}
                ],
                "mock_model_output": True
            }
        },
        usage=[
            {
                "reserve_amount": "0.012",
                "cost_amount": "0.009",
                "usage": {"measurement_status": "locally_measured", "input_tokens": 1260, "output_tokens": 188, "total_tokens": 1448}
            }
        ],
    )


def pull_credit_report(ctx):
    score = int(ctx.variables["credit_score_fixture"])
    return _completed(
        {"CreditReport.json": {"application_id": ctx.variables["application_id"], "score": score, "enquiry_key": f"credit:{ctx.variables['application_id']}:{ctx.run_id}", "provider_status": "mock-completed"}},
        {"credit_score": score},
    )


def screen_fraud(ctx):
    fraud_score = float(ctx.variables["fraud_score_fixture"])
    fraud_flag = fraud_score >= 0.75
    return _completed(
        {"FraudSignals.json": {"application_id": ctx.variables["application_id"], "score": fraud_score, "flag": fraud_flag, "rule_version": "fraud-policy-3.2"}},
        {"fraud_flag": fraud_flag},
    )


def verify_income(ctx):
    amount = float(ctx.variables["verified_income_fixture"])
    return _completed(
        {"IncomeVerification.json": {"application_id": ctx.variables["application_id"], "verified_income": amount, "source": "mock-payroll-api", "match": True}},
        {"verified_income": amount},
    )


def calculate_risk_decision(ctx):
    score = int(ctx.variables["credit_score"])
    fraud_flag = bool(ctx.variables["fraud_flag"])
    serviceability = float(ctx.variables["verified_income"]) / max(float(ctx.variables["loan_amount"]), 1.0)
    risk_score = round((850 - score) / 850 + (0.5 if fraud_flag else 0) + (0.2 if serviceability < 1.8 else 0), 4)
    if fraud_flag or score < 620 or serviceability < 1.4:
        decision = "rejected"
        review_required = False
    elif score < 720 or float(ctx.variables["loan_amount"]) >= 40000:
        decision = "approved"
        review_required = True
    else:
        decision = "approved"
        review_required = False
    assessment = {
        "risk_score": risk_score,
        "credit_score": score,
        "fraud_flag": fraud_flag,
        "serviceability_ratio": round(serviceability, 3),
        "policy_version": "consumer-credit-2026.07"
    }
    plan = {"proposed_decision": decision, "review_required": review_required, "approved_amount": ctx.variables["loan_amount"] if decision == "approved" else 0, "reasons": ["score_band", "serviceability", "fraud_policy"]}
    return _completed({"RiskAssessment.json": assessment, "ApprovalPlan.json": plan}, {"risk_score": risk_score, "proposed_decision": decision, "review_required": review_required})


def review_credit_decision(ctx):
    fixture = ctx.human_decision()
    decision = str(fixture["decision"])
    return _completed(
        {"HumanReviewDecision.json": {"application_id": ctx.variables["application_id"], "decision": decision, "actor": fixture["actor"], "reason": fixture["reason"], "expected_revision": fixture["expected_revision"]}},
        {"review_decision": decision},
    )


def finalize_credit_decision(ctx):
    decision = ctx.variables["review_decision"] if ctx.variables["review_required"] else ctx.variables["proposed_decision"]
    return _completed({"FinalDecision.json": {"decision": decision, "source": "human_review" if ctx.variables["review_required"] else "policy", "policy_decision": ctx.variables["proposed_decision"]}}, {"decision": decision})


def capture_offer_acceptance(ctx):
    callback = ctx.callback()
    return _completed(
        {"AcceptedOffer.json": {"application_id": ctx.variables["application_id"], "accepted": callback["accepted"], "accepted_at": callback["accepted_at"], "signer_id": callback["signer_id"], "amount": ctx.variables["loan_amount"]}},
        {"offer_accepted_at": callback["accepted_at"]},
    )


def disburse_funds(ctx):
    disbursed_at = "2026-07-24T03:01:00Z"
    return _completed(
        {"DisbursementReceipt.json": {"application_id": ctx.variables["application_id"], "amount": ctx.variables["loan_amount"], "currency": "AUD", "ledger_operation_key": f"disburse:{ctx.variables['application_id']}", "status": "mock-settled", "disbursed_at": disbursed_at}},
        {"disbursed_at": disbursed_at},
    )


def issue_rejection_notice(ctx):
    assessment = ctx.artifact("RiskAssessment.json")
    return _completed(
        {"RejectionNotice.json": {"application_id": ctx.variables["application_id"], "decision": ctx.variables["decision"], "adverse_action_reasons": ["credit_score_below_policy_floor"], "risk_score": assessment["risk_score"], "delivery_status": "mock-delivered"}}
    )


def request_application_revision(ctx):
    review = ctx.artifact("HumanReviewDecision.json")
    return _completed(
        {
            "ApplicationRevisionRequest.json": {
                "application_id": ctx.variables["application_id"],
                "decision": ctx.variables["decision"],
                "requested_changes": ["supply_current_income_evidence"],
                "review_reason": review["reason"],
                "status": "open",
            }
        }
    )
