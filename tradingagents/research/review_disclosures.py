"""Code-owned review scope from revalidated frozen cases, never acceptance."""

from hashlib import sha256

from .case_context import CaseContext, load_case_context
from .storage import canonical_json, digest, parse_json

DISCLOSURE_POLICY = "validated-case-review-scope-v1"


def review_disclosure(request, snapshot, context, language):
    """Recompute review state; never trust a serialized status or authored claim."""
    if not isinstance(context, CaseContext):
        raise ValueError("review disclosure requires a validated case context")
    envelope = {"case": context.case, "review": context.review,
                "source_passages": context.source_passages}
    if context.operating_scenarios is not None:
        envelope["operating_scenarios"] = parse_json(context.artifacts["operating_scenario_package.json"])
    checked = load_case_context(canonical_json(envelope), request, snapshot)
    if (checked.model_context() != context.model_context()
            or checked.artifacts != context.artifacts):
        raise ValueError("review disclosure case state differs from frozen validation")
    operating = checked.operating_scenarios
    operating_status = ("absent" if operating is None else
                        "reviewed_operating_only" if operating.reviewed else "draft_unreviewed")
    chinese = language == "Chinese"
    if checked.reviewed:
        financial_text = (
            "财务明细已完成独立复核；这不代表估值模型或投资结论获得批准。" if chinese else
            "The financial schedules have completed an independent review; this is not approval "
            "of a valuation model or investment conclusion."
        )
    else:
        financial_text = (
            "财务明细仍为未经复核的草稿；导入草稿不代表批准。" if chinese else
            "The financial schedules remain an unreviewed draft; their ingestion is not approval."
        )
    if operating_status == "reviewed_operating_only":
        operating_text = (
            "条件性经营情景已单独复核，但仅限经营输入与计算；该复核不批准财务明细。" if chinese else
            "The conditional operating package was separately reviewed for operating inputs and "
            "calculations only; that review does not approve the financial schedules."
        )
    elif operating_status == "absent":
        operating_text = "未提供经营情景包。" if chinese else "No operating-scenario package was supplied."
    else:
        operating_text = (
            "经营情景尚未完成有效的独立复核，因此不提供其数值结果。" if chinese else
            "The operating-scenario package has not completed a valid independent review; "
            "its numerical results remain withheld."
        )
    scope_text = (
        "如有财政期间经营情景，其范围仅为收入和营业利润，而非从财政指引到日历期间现金流的"
        "已复核模型，也非经济假设获得认可。该模型衔接仍未建立；估值、每股价值与资金充足性结论仍不提供。"
        if chinese else
        "Fiscal operating scenarios, if supplied, cover revenue and operating income only, not "
        "a reviewed bridge from fiscal guidance to calendar-period cash flows or economic "
        "underwriting. That model bridge remains unavailable; valuation, per-share value and "
        "funding conclusions remain withheld."
    )
    heading = "复核状态与模型范围" if chinese else "Review status and model scope"
    text = f"## {heading}\n\n{financial_text} {operating_text}\n\n{scope_text}"
    return {"schema_version": 1, "policy": DISCLOSURE_POLICY,
        "classification": "code_owned_case_state_not_issuer_evidence_or_acceptance",
        "financial_status": "reviewed" if checked.reviewed else "draft_unreviewed",
        "operating_status": operating_status,
        "case_context_sha256": digest(checked.model_context()),
        "snapshot_sha256": digest(snapshot), "case_sha256": digest(checked.case),
        "review_sha256": digest(checked.review) if checked.review is not None else None,
        "operating_package_sha256": (
            sha256(checked.artifacts["operating_scenario_package.json"]).hexdigest()
            if operating is not None else None),
        "text": text, "text_sha256": sha256(text.encode()).hexdigest(),
        "financial_sentence": financial_text, "scope_sentence": scope_text}
