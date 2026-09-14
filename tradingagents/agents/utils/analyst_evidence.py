"""Attach provider evidence to specialist calls without sharing conversations."""

from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256

from langchain_core.messages import AIMessage, ToolMessage

from .evidence import build_packet


def collect_tool_evidence(state, role, prepared=None):
    """Return copied messages with source IDs; preserve the original tool history."""
    prepared = deepcopy(prepared or state.get("prepared_data", {}).get(role) or {
        "sources": [], "facts": [], "caveats": [],
    })
    prepared.setdefault("analysis_date", state["trade_date"])
    by_id = {source["id"]: source for source in prepared["sources"]}
    messages = []
    for message in state.get("messages", []):
        if isinstance(message, ToolMessage):
            source_id = role + "-tool-" + sha256(message.tool_call_id.encode()).hexdigest()[:12]
            if source_id not in by_id:
                source = {
                    "id": source_id,
                    "label": message.name or "Tool response",
                    "content": str(message.content),
                    "vendor": "See source content; not independently recorded",
                    "retrieved_at": datetime.now(timezone.utc).isoformat(),
                    "published_at": None,
                }
                prepared["sources"].append(source)
                by_id[source_id] = source
            message = message.model_copy(update={
                "content": f"Evidence source ID: {source_id}\n{message.content}",
            })
        messages.append(message)
    return prepared, messages


def finish_specialist(state, role, report_key, result, prepared):
    """Save raw evidence and a validated handoff without another model request."""
    update = {
        "messages": [result],
        "prepared_data": {**state.get("prepared_data", {}), role: prepared},
        report_key: "",
    }
    if not result.tool_calls:
        packet = build_packet(role, result.content, prepared)
        update[report_key] = packet.report
        # Keep the user's report readable; source data and the handoff are saved separately.
        update["messages"] = [result.model_copy(update={"content": packet.report})]
        update["evidence_packets"] = {
            **state.get("evidence_packets", {}), role: packet.to_dict(),
        }
    return update


def finish_sentiment(state, report, prepared):
    return finish_specialist(
        state, "sentiment", "sentiment_report", AIMessage(content=report), prepared,
    )
