"""Demo merchant policy and FX snapshot, loaded from versioned files.

The values live in nirnaya/assets/ (policies/merchant-demo/<version>.toml and fx/...), pinned in
nirnaya/assets/active.toml and hash-locked. ASSUMPTION: every number is a demo value chosen to
make the demo scenarios legible; none of it is real Airwallex pricing, network fee or win rate.
TODO(verify-airwallex-docs): replace F_pre, F_cb, F_pa with current Airwallex pricing.
"""

from nirnaya import assets
from nirnaya.models import FxTable, MerchantPolicy, PriorEntry


def _policy() -> tuple[assets.Asset, MerchantPolicy]:
    asset, data = assets.config("policy")
    policy = MerchantPolicy.model_validate(data)
    if (asset.name, asset.version) != (policy.merchant_id, policy.version):
        raise assets.AssetError(
            f"{asset.path} holds {policy.merchant_id} version {policy.version}: "
            "the file must be policies/<merchant_id>/<version>.toml"
        )
    return asset, policy


POLICY_ASSET, DEMO_POLICY = _policy()
DEMO_PRIORS: tuple[PriorEntry, ...] = DEMO_POLICY.priors

FX_ASSET, _fx = assets.config("fx")
DEMO_FX = FxTable.model_validate(_fx)
