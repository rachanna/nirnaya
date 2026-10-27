"""Opt-in, read-only checks against the real Airwallex sandbox. Not part of the normal run.

    NIRNAYA_LIVE_AIRWALLEX=1 uv run pytest -m live_airwallex -s

Nothing here writes: it logs in, lists disputes and retrieves each one through the client.
"""

import os

import pytest

from nirnaya.config.env import load_dotenv
from nirnaya.integrations.airwallex.client import AirwallexClient
from nirnaya.integrations.airwallex.http import AirwallexHttp, Credentials

pytestmark = pytest.mark.live_airwallex

MERCHANT = "merchant-demo"


@pytest.fixture(scope="module")
def client() -> AirwallexClient:
    load_dotenv()
    if os.environ.get("NIRNAYA_LIVE_AIRWALLEX") != "1":
        pytest.skip("set NIRNAYA_LIVE_AIRWALLEX=1 to run live sandbox checks")
    return AirwallexClient(AirwallexHttp(Credentials.from_env(os.environ)), merchant_id=MERCHANT)


def test_list_and_retrieve_every_dispute(client: AirwallexClient) -> None:
    disputes = client.list_disputes(MERCHANT)
    print(f"\n{len(disputes)} dispute(s); unmappable: {client.unmappable or 'none'}")
    for d in disputes:
        again = client.retrieve_dispute(MERCHANT, d.id)
        assert again.id == d.id and again.merchant_id == MERCHANT
        print(
            f"  {d.id} {d.stage} {d.status} {d.amount} reason={d.reason.type} "
            f"code={d.reason.original_code} order={d.merchant_order_id}"
        )
