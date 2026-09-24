"""Deterministic reader disclosure of reviewed conditional inputs, not forecasts."""

import re
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from html import escape


def cashflow_assumptions(bridge, language):
    """Only consume the revalidated, independently reviewed bridge context."""
    if bridge is None or not bridge.reviewed:
        return ""
    chinese = language == "Chinese"
    heading = "条件性现金流假设" if chinese else "Conditional cash-flow assumptions"
    intro = (
        "以下为分析师条件假设，并非公司指引或经济预测获批。金额为美元，显示值经四舍五入；"
        "正营运资金变动及增量承诺均扣减现金流。精确输入及依据见 "
        if chinese else
        "These are conditional analyst assumptions, not issuer guidance or approved economic "
        "forecasts. Amounts are USD, rounded for display; positive working-capital investment "
        "and incremental commitments reduce cash flow. Exact inputs and rationale: "
    )
    lines = [f"### {heading}", "", intro +
             "[cash-flow package](cashflow_bridge_package.json); "
             "[reviewed calculation context](cashflow_bridge_context.json).", "",
             "| Scenario / 情景 | Fiscal period / 财政期间 | Tax / 税率 | D&A / 折旧摊销 (USD) | "
             "D&A / revenue | Capex (USD) | Working-capital investment / 营运资金投入 (USD) | "
             "Incremental commitments / 增量承诺 (USD) |" if chinese else
             "| Scenario | Fiscal period (inclusive) | Tax rate | D&A (USD) | D&A / revenue | "
             "Capex (USD) | Working-capital investment (USD) | Incremental commitments (USD) |",
             "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
    def cell(value):
        plain = escape(str(value)).replace("|", "&#124;").replace("\n", " ").replace("\r", " ")
        return re.sub(r"([\\`*_{}\[\]()!])", r"\\\1", plain)

    with localcontext(Context(prec=80, rounding=ROUND_HALF_EVEN)):
        for scenario in bridge.model_context["scenarios"]:
            for period in scenario["periods"]:
                da = Decimal(period["depreciation_amortization"]["value"])
                revenue = Decimal(period["revenue"])
                da_share = f"{da / revenue * 100:.2f}%" if revenue else "N/A"
                rows = [cell(scenario["label"]),
                        f"{cell(period['fiscal_label'])}: {cell(period['period_start'])} — {cell(period['period_end'])}",
                        f"{Decimal(period['tax_rate']['value']) * 100:.2f}%", f"{da:,.2f}", da_share,
                        f"{Decimal(period['capex']['value']):,.2f}",
                        f"{Decimal(period['change_in_operating_working_capital']['value']):,.2f}",
                        f"{Decimal(period['incremental_commitment_deduction']):,.2f}"]
                lines.append("| " + " | ".join(rows) + " |")
    return "\n".join(lines)
