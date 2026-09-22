"""Code-owned valuation references, kept distinct from reported financial facts."""

import re
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from typing import Literal
from urllib.parse import quote

from pydantic import Field

from .contracts import Contract, Identifier
from .storage import digest


class CalculatedValue(Contract):
    id: Identifier
    value: Decimal
    unit: str
    currency: str | None = None
    classification: str = "illustrative_calculation_not_reported_fact"
    valuation_method: Literal["fcff", "equity_fcfe", "operating_scenario"]
    share_count_basis: Literal["point_in_time_diluted", "latest_quarter_diluted_proxy", "not_applicable"]
    model_input_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    model_result_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    evidence_ids: tuple[Identifier, ...]
    display_decimal_places: int = Field(default=2, ge=0, le=6)


_REFERENCE = re.compile(r"\{\{calc:([^{}]+)\}\}")
_SCENARIO_TABLE = re.compile(r"\{\{scenario_table\}\}")
_SCENARIO_ASSUMPTIONS_TABLE = re.compile(r"\{\{scenario_assumptions_table\}\}")
_SCENARIO_DELIVERY_CONTEXT = Context(prec=80, rounding=ROUND_HALF_EVEN)
_TOTAL_AMOUNTS = {
    "explicit_period_present_value", "terminal_value_at_horizon",
    "terminal_value_present_value", "enterprise_value", "net_debt", "equity_value",
    "total_equity_present_value", "terminal_equity_cash_flow",
}
_PER_SHARE = {"value_per_current_diluted_share"}
_RATIOS = {"terminal_value_share_of_enterprise_value", "terminal_value_share_of_total_equity_present_value"}
_FORECAST_AMOUNTS = {
    "revenue", "operating_profit_before_tax", "economic_sbc", "nopat",
    "depreciation_amortization", "capex", "working_capital", "change_in_working_capital",
    "fcff", "equity_cash_flow", "present_value", "net_income_common", "required_capital_retention",
}


def _display_value_and_unit(value: Decimal, unit: str) -> tuple[Decimal, str]:
    """Apply the calculation catalog's declared ratio convention."""

    if unit.strip().lower() == "fraction":
        return value * Decimal(100), "%"
    if unit.strip().lower() in {"%", "percent", "percentage"}:
        return value, "%"
    return value, unit


def _render_calculated_value(item: CalculatedValue, language: str) -> str:
    with localcontext() as context:
        context.prec = 80
        value, unit = _display_value_and_unit(item.value, item.unit)
        suffix = ""
        if item.unit == item.currency:
            choices = ((Decimal("1e8"), "亿"), (Decimal("1e4"), "万")) if language == "Chinese" else (
                (Decimal("1e9"), " billion"), (Decimal("1e6"), " million"))
            for scale, label in choices:
                if abs(value) >= scale:
                    value, suffix = value / scale, label
                    break
        number = format(value, f".{item.display_decimal_places}f")
    return f"{number}{suffix} {unit}"


def calculation_anchor_id(identifier: str) -> str:
    """Return the raw HTML anchor ID for a validated calculation identifier.

    ``Identifier`` permits Unicode, ``/`` and ``:``, all of which are valid in
    an HTML ``id`` but must be percent-encoded in an href fragment. Appendix
    emitters should place this exact value in ``id=``; callers should use
    :func:`calculation_anchor_href` for links.
    """

    return f"calculation-{identifier}"


def calculation_anchor_href(identifier: str) -> str:
    """Return the canonical percent-encoded appendix href for ``identifier``."""

    return "model_appendix.md#" + quote(calculation_anchor_id(identifier), safe="._-")


def _calculation_link(item, language):
    return (f"[{_render_calculated_value(item, language)}]"
            f"({calculation_anchor_href(item.id)})")


def _scenario_table(values: tuple[CalculatedValue, ...], language: str, *, cite=False) -> str:
    """Expand an explicit, source-backed operating-scenario table marker.

    The marker is deliberately narrow: it cannot manufacture a table from a
    valuation catalog. Every row needs reviewed conditional fiscal totals, at
    least one evidence identifier, and the same hash-bound package/result and
    currency context. Each row retains its exact calculation evidence IDs;
    the reader resolves those IDs through frozen fact ancestry to source links.
    """

    rows: dict[str, dict[str, CalculatedValue]] = {}
    prefix = "operating_scenario."
    suffixes = {
        ".fiscal_total.revenue": "revenue",
        ".fiscal_total.operating_income": "operating_income",
    }
    for item in values:
        if item.valuation_method != "operating_scenario" or not item.id.startswith(prefix):
            continue
        for suffix, field in suffixes.items():
            if item.id.endswith(suffix):
                scenario_id = item.id[len(prefix):-len(suffix)]
                rows.setdefault(scenario_id, {})[field] = item
                break

    complete_rows = [
        (scenario_id, row["revenue"], row["operating_income"])
        for scenario_id, row in rows.items()
        if {"revenue", "operating_income"} <= row.keys()
    ]
    table_values = tuple(
        item for _, revenue, operating_income in complete_rows for item in (revenue, operating_income)
    )
    # For operating scenarios, ``model_input_sha256`` is the reviewed package
    # hash, which binds the fiscal/calendar date convention and period basis.
    contexts = {
        (
            item.model_input_sha256,
            item.model_result_sha256,
            item.currency,
            item.unit,
            item.share_count_basis,
        )
        for item in table_values
    }
    if (
        not complete_rows
        or any({"revenue", "operating_income"} - row.keys() for row in rows.values())
        or any(
            not item.evidence_ids
            or item.classification
            != "conditional_operating_scenario_calculation_not_reported_fact"
            or item.currency is None
            or item.unit != item.currency
            for item in table_values
        )
        or len(contexts) != 1
    ):
        raise ValueError("scenario table requires source-backed reviewed operating totals")

    if language == "Chinese":
        header = "| 情景 | 财年收入 | 营业利润 | 来源 |"
    else:
        header = "| Scenario | Fiscal revenue | Operating income | Sources |"

    lines = [header, "| --- | ---: | ---: | --- |"]
    for scenario_id, revenue, operating_income in sorted(complete_rows):
        render = _calculation_link if cite else _render_calculated_value
        revenue_text = render(revenue, language)
        income_text = render(operating_income, language)
        label = scenario_id.replace("_", " ").title()
        evidence_ids = dict.fromkeys((*revenue.evidence_ids, *operating_income.evidence_ids))
        references = " ".join(f"[{identifier}]" for identifier in evidence_ids)
        lines.append(f"| {label} | {revenue_text} | {income_text} | {references} |")
    return "\n".join(lines)


def _scenario_assumptions_table(delivery: dict, language: str) -> str:
    """Expand the V7-only compact input/provenance scenario marker.

    Unlike the legacy fiscal-total marker, this table is rendered from the
    engine's case-reader delivery contract. Source references identify inputs
    only; no source link is emitted beside code-derived arithmetic, preventing
    a calculation from being misattributed to an issuer or other source.
    """

    presentation = delivery.get("scenario_presentation") if isinstance(delivery, dict) else None
    if not isinstance(presentation, dict) or not isinstance(presentation.get("rows"), list):
        raise ValueError("scenario assumptions table requires reviewed delivery presentation")
    rows = presentation["rows"]
    if not rows:
        raise ValueError("scenario assumptions table requires reviewed delivery rows")
    if language == "Chinese":
        header = "| 情景 / 期间 | 输入 / 溯源 | 收入/周（分析师计算） | 较前期收入 | 较前期费用 | 较前期每周收入 |"
        none = "不适用"
        per_week_suffix = " USD/周"
    else:
        header = "| Scenario / period | Inputs / provenance | Revenue / week (analyst calculation) | Revenue vs prior period (analyst calculation) | OpEx vs prior period (analyst calculation) | Weekly revenue vs prior period (analyst calculation) |"
        none = "n/a"
        per_week_suffix = " USD/week"

    def cell(value: object) -> str:
        return " ".join(str(value).splitlines()).replace("\\", "\\\\").replace("|", "\\|")

    lines = [header, "| --- | --- | ---: | ---: | ---: | ---: |"]
    with localcontext(_SCENARIO_DELIVERY_CONTEXT):
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("scenario assumptions table requires object rows")
            inputs = row.get("inputs")
            calculations = row.get("analyst_calculations")
            if not isinstance(inputs, list) or not isinstance(calculations, dict):
                raise ValueError("scenario assumptions table requires declared inputs and calculations")
            input_text = []
            for item in inputs:
                if not isinstance(item, dict):
                    raise ValueError("scenario assumptions table requires object inputs")
                name, value, unit, classification = (item.get(key) for key in (
                    "name", "value", "unit", "classification"
                ))
                if not all(isinstance(value, str) for value in (name, value, unit, classification)):
                    raise ValueError("scenario assumptions table input is incomplete")
                raw_value = Decimal(value)
                if unit == "fraction":
                    shown_value, shown_unit = raw_value * Decimal(100), "%"
                elif unit == "USD" and abs(raw_value) >= Decimal("1e9"):
                    shown_value, shown_unit = raw_value / Decimal("1e9"), "bn USD"
                else:
                    shown_value, shown_unit = raw_value, unit
                classification_label = {
                    "management_guidance_anchor": "issuer guidance",
                    "analyst_assumption": "analyst assumption",
                }.get(classification, classification)
                source_ids = item.get("source_ids")
                if not isinstance(source_ids, list) or not all(isinstance(source_id, str) for source_id in source_ids):
                    raise ValueError("scenario assumptions table provenance is incomplete")
                references = " ".join(f"[{cell(source_id)}]" for source_id in source_ids) or none
                if item.get("issuer_guidance_range") is not None:
                    raise ValueError("unclassified guidance cannot become a typed table range")
                input_text.append(
                    f"{cell(name)}: {shown_value:.2f} {cell(shown_unit)} "
                    f"({cell(classification_label)}; {references})"
                )
            weeks = Decimal(calculations["weeks"])
            week_text = f"{weeks:.2f}".rstrip("0").rstrip(".")
            label = (f"{cell(row.get('scenario_label'))} / {cell(row.get('fiscal_label'))} "
                     f"({week_text} {'周' if language == 'Chinese' else 'weeks'})")
            weekly = calculations.get("revenue_per_week")
            change = calculations.get("revenue_per_week_change_vs_previous_period")
            revenue_change = calculations.get("revenue_change_vs_previous_period")
            opex_change = calculations.get("opex_change_vs_previous_period")
            if not isinstance(weekly, str) or any(
                item is not None and not isinstance(item, str)
                for item in (change, revenue_change, opex_change)
            ):
                raise ValueError("scenario assumptions table calculations are incomplete")
            weekly_value = Decimal(weekly)
            weekly_text = (
                f"{weekly_value / Decimal('1e9'):.2f} bn{per_week_suffix}"
                if abs(weekly_value) >= Decimal("1e9") else f"{weekly_value:.2f}{per_week_suffix}"
            )

            def rate_text(value):
                return f"{Decimal(value) * Decimal(100):.2f}%" if value is not None else none

            lines.append(
                f"| {label} | {'; '.join(input_text)} | {weekly_text} | {rate_text(revenue_change)} | "
                f"{rate_text(opex_change)} | {rate_text(change)} |"
            )
    return "\n".join(lines)


def calculation_catalog(proposal, valuation) -> tuple[CalculatedValue, ...]:
    """Allowlisted outputs only; never elevate model-authored numbers to facts."""
    if valuation.get("status") != "illustrative":
        return ()
    result = valuation["result"]
    model = proposal.model or {}
    method = valuation.get("valuation_method", "equity_fcfe" if "cost_of_equity" in model else "fcff")
    share_basis = valuation.get("share_count_basis", "point_in_time_diluted")
    units = result["units"]
    amount_scale = Decimal(units["amount_scale"])
    if not amount_scale.is_finite() or amount_scale <= 0:
        raise ValueError("invalid calculated-value scale")
    values = []
    input_hash = digest(proposal)
    result_hash = digest(valuation)

    def add(path, raw, unit, scale=Decimal(1), currency=None, classification="illustrative_calculation_not_reported_fact"):
        if raw is None:
            return
        with localcontext() as context:
            context.prec = 50
            value = Decimal(raw) * scale
        values.append(CalculatedValue(
            id=f"valuation.{path}", value=value, unit=unit, currency=currency,
            model_input_sha256=input_hash, model_result_sha256=result_hash,
            evidence_ids=proposal.evidence_ids,
            valuation_method=method, share_count_basis=share_basis, classification=classification,
        ))

    for name in sorted(_TOTAL_AMOUNTS & result.keys()):
        add(name, result[name], units["currency"], amount_scale, units["currency"])
    for name in sorted(_PER_SHARE & result.keys()):
        denominator = "proxy diluted share" if valuation.get("share_count_basis") == "latest_quarter_diluted_proxy" else "share"
        add(name, result[name], f"{units['currency']}/{denominator}", currency=units["currency"])
    for name in sorted(_RATIOS & result.keys()):
        add(name, result[name], "%", Decimal(100))
    for index, period in enumerate(result.get("forecasts", ())):
        for name in sorted(_FORECAST_AMOUNTS & period.keys()):
            add(f"forecasts.{index}.{name}", period[name], units["currency"],
                amount_scale, units["currency"])
        for name, unit in (("discount_years", "years"), ("discount_factor", "multiple")):
            if name in period:
                add(f"forecasts.{index}.{name}", period[name], unit)
    for name in ("current_revenue", "current_working_capital", "net_debt", "current_net_income"):
        if name in model:
            add(f"inputs.{name}", model[name], units["currency"], amount_scale,
                units["currency"], "model_input_not_a_new_reported_fact")
    if "current_diluted_shares" in model:
        add("inputs.current_diluted_shares", model["current_diluted_shares"],
            "proxy diluted shares" if share_basis == "latest_quarter_diluted_proxy" else "diluted shares",
            Decimal(units["share_scale"]), classification="model_input_not_a_new_reported_fact")
    for name in ("discount_rate", "cost_of_equity", "terminal_growth"):
        if name in model:
            add(f"inputs.{name}", model[name], "%", Decimal(100),
                classification="model_assumption_or_convention")
    return tuple(values)


def render_calculations(text: str, values: tuple[CalculatedValue, ...], language="English", *, cite=False,
                        scenario_delivery: dict | None = None) -> str:
    by_id = {value.id: value for value in values}
    if len(by_id) != len(values):
        raise ValueError("ambiguous calculation identifiers")

    def replace(match):
        if match[1] not in by_id:
            raise ValueError("reader references an unknown calculation")
        render = _calculation_link if cite else _render_calculated_value
        return render(by_id[match[1]], language)

    rendered = _SCENARIO_TABLE.sub(lambda _: _scenario_table(values, language, cite=cite), text)
    rendered = _SCENARIO_ASSUMPTIONS_TABLE.sub(
        lambda _: _scenario_assumptions_table(scenario_delivery, language), rendered
    )
    rendered = _REFERENCE.sub(replace, rendered)
    if "{{calc:" in rendered:
        raise ValueError("malformed calculation reference")
    if "{{scenario_table" in rendered:
        raise ValueError("malformed scenario table reference")
    if "{{scenario_assumptions_table" in rendered:
        raise ValueError("malformed scenario assumptions table reference")
    return rendered
