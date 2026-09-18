from datetime import date

from tests.test_research_equity_binding import fixture
from tradingagents.research.engine import _calculate


def test_segmented_common_income_cannot_bind_parent_equity_opening(tmp_path):
    request, snapshot, proposal = fixture(tmp_path)
    segmented_income = snapshot.facts[0].model_copy(
        update={"segment": "Broker-dealer subsidiary"}
    )
    snapshot = snapshot.model_copy(
        update={"facts": (segmented_income, snapshot.facts[1])}
    )

    result = _calculate(proposal, request, snapshot)

    assert result == {
        "status": "unavailable",
        "limitations": [
            "Opening input not bound to a matching fact: current_net_income"
        ],
    }


def test_stale_share_proxy_is_rejected_when_newer_eligible_quarter_exists(tmp_path):
    request, snapshot, proposal = fixture(tmp_path)
    request = request.model_copy(
        update={"share_count_basis": "latest_quarter_diluted_proxy"}
    )
    income = snapshot.facts[0].model_copy(
        update={"period_start": date(2023, 10, 1), "period_end": date(2024, 9, 30)}
    )
    selected_proxy = snapshot.facts[1].model_copy(
        update={
            "metric": "weighted_average_diluted_shares",
            "period_type": "duration",
            "period_start": date(2024, 7, 1),
            "period_end": date(2024, 9, 30),
        }
    )
    selected_snapshot = snapshot.model_copy(
        update={"facts": (income, selected_proxy)}
    )
    assert _calculate(proposal, request, selected_snapshot)["status"] == "illustrative"

    newer_proxy = selected_proxy.model_copy(
        update={
            "id": "newer-quarter-diluted-shares",
            "period_start": date(2024, 10, 1),
            "period_end": date(2024, 12, 31),
            "location": "newer synthetic quarter",
        }
    )
    snapshot_with_newer_proxy = snapshot.model_copy(
        update={"facts": (income, selected_proxy, newer_proxy)}
    )

    result = _calculate(proposal, request, snapshot_with_newer_proxy)

    assert result == {
        "status": "unavailable",
        "limitations": [
            "Share-count proxy is not the latest eligible quarter; opening anchors require reconciliation."
        ],
    }
