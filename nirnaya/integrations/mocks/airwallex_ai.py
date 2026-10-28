"""SYNTHETIC examples of Airwallex AI Dispute Automation recommendations for the offline demo.

They are not Airwallex output: they only show how Nirnaya displays a recommendation beside its
own decision, and every one is marked simulated. Chosen to be plausible and neutral, not to
flatter Nirnaya: the AI agrees on K1b and K2 and differs on K3. The documented rule that the
recommendation applies at the CHARGEBACK stage is kept: at RFI it is not supported. On the live
sandbox the real recommendation (if any) is read from the dispute instead.
"""

from nirnaya.models.dispute import AiRecommendation
from nirnaya.models.enums import Action, Stage

SIMULATED: dict[tuple[str, Stage], AiRecommendation] = {
    ("K1", Stage.RFI): AiRecommendation(
        status="UNAVAILABLE", unavailable_reason="NOT_SUPPORTED", simulated=True
    ),
    ("K1", Stage.CHARGEBACK): AiRecommendation(
        status="AVAILABLE",
        action=Action.CHALLENGE,
        action_raw="Challenge",
        evidence_to_submit=(
            "order_fulfillment_file_ids",
            "previous_purchase_similar_product_file_ids",
        ),
        simulated=True,
    ),
    ("K2", Stage.CHARGEBACK): AiRecommendation(
        status="AVAILABLE", action=Action.ACCEPT, action_raw="Accept", simulated=True
    ),
    ("K3", Stage.CHARGEBACK): AiRecommendation(
        status="AVAILABLE",
        action=Action.CHALLENGE,
        action_raw="Challenge",
        evidence_to_submit=("refund_policy_file_ids", "customer_communication_file_ids"),
        simulated=True,
    ),
}


def simulated(label: str, stage: Stage) -> AiRecommendation | None:
    return SIMULATED.get((label, stage))
