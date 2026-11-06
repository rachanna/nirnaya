"""Versioned assets: immutable files, a SHA-256 lock, pinned versions, strict templates."""

import pytest

from nirnaya import assets
from nirnaya.assets import AssetError, AssetIntegrityError


@pytest.fixture
def root(tmp_path):
    """A small asset tree with two drafter versions, a config file and a lock."""
    (tmp_path / "prompts" / "drafter_system").mkdir(parents=True)
    (tmp_path / "prompts" / "drafter_system" / "v1.md").write_text("Draft for $merchant.\n")
    (tmp_path / "prompts" / "drafter_system" / "v2.md").write_text("Draft carefully.\n")
    (tmp_path / "prompts" / "tools").mkdir()
    (tmp_path / "prompts" / "tools" / "v1.toml").write_text('finish = "Stop."\n')
    (tmp_path / "fx").mkdir()
    (tmp_path / "fx" / "snap.toml").write_text('label = "x"\nrate = "1.10"\n')
    (tmp_path / "active.toml").write_text(
        '[prompts]\ndrafter_system = "v1"\ntools = "v1"\n\n[config]\nfx = "fx/snap.toml"\n'
    )
    assert assets.lock(tmp_path) == [
        "fx/snap.toml",
        "prompts/drafter_system/v1.md",
        "prompts/drafter_system/v2.md",
        "prompts/tools/v1.toml",
    ]
    return tmp_path


def test_a_prompt_loads_with_its_version_and_hash(root):
    a = assets.prompt("drafter_system", {}, root=root)
    assert (a.name, a.version, a.text) == ("drafter_system", "v1", "Draft for $merchant.\n")
    assert a.sha256 == assets.digest("Draft for $merchant.\n")
    assert a.ref == f"drafter_system@v1#{a.sha256[:12]}"


def test_the_hash_ignores_the_checkout_line_ending(root):
    path = root / "prompts" / "drafter_system" / "v1.md"
    path.write_bytes(b"Draft for $merchant.\r\n")  # a Windows checkout of the same text
    assert assets.prompt("drafter_system", {}, root=root).text == "Draft for $merchant.\n"


def test_an_edited_published_file_fails_closed(root):
    (root / "prompts" / "drafter_system" / "v1.md").write_text("Draft anything.\n")
    with pytest.raises(AssetIntegrityError, match="changed since it was locked"):
        assets.prompt("drafter_system", {}, root=root)


def test_a_file_missing_from_the_lock_fails_closed(root):
    (root / "prompts" / "drafter_system" / "v3.md").write_text("new\n")
    with pytest.raises(AssetIntegrityError, match="not in the lock"):
        assets.prompt("drafter_system", {"NIRNAYA_PROMPTS": "drafter_system:v3"}, root=root)


def test_locking_adds_new_files_and_never_rewrites_an_entry(root):
    (root / "prompts" / "drafter_system" / "v3.md").write_text("new\n")
    assert assets.lock(root) == ["prompts/drafter_system/v3.md"]
    assert assets.lock(root) == []
    (root / "prompts" / "drafter_system" / "v1.md").write_text("edited\n")
    with pytest.raises(AssetIntegrityError, match="publish a new version"):
        assets.lock(root)


def test_the_env_override_beats_the_pin(root):
    env = {"NIRNAYA_PROMPTS": " drafter_system:v2 "}
    assert assets.prompt("drafter_system", env, root=root).version == "v2"
    assert assets.prompt("tools", env, root=root).version == "v1"  # others keep their pin


@pytest.mark.parametrize(
    "override, why",
    [
        ("drafter_system", "name:version"),
        ("drafter_system:", "name:version"),
        ("drafter_sytem:v2", "unknown prompt"),
        ("drafter_system:v9", "no file"),
    ],
)
def test_a_bad_override_fails(root, override, why):
    with pytest.raises(AssetError, match=why):
        assets.prompt("drafter_system", {"NIRNAYA_PROMPTS": override}, root=root)


def test_render_needs_exactly_the_template_placeholders(root):
    a = assets.prompt("drafter_system", {}, root=root)
    assert assets.render(a, merchant="m-1") == "Draft for m-1.\n"
    with pytest.raises(AssetError, match="missing"):
        assets.render(a)
    with pytest.raises(AssetError, match="unexpected"):
        assets.render(a, merchant="m-1", extra="x")


def test_render_from_a_pool_takes_what_each_version_needs(root):
    """One pool of values serves every version of a prompt; a missing value still fails."""
    v1 = assets.prompt("drafter_system", {}, root=root)
    v2 = assets.prompt("drafter_system", {"NIRNAYA_PROMPTS": "drafter_system:v2"}, root=root)
    pool = {"merchant": "m-1", "other": "unused"}
    assert assets.render_from(v1, pool) == "Draft for m-1.\n"
    assert assets.render_from(v2, pool) == "Draft carefully.\n"
    with pytest.raises(AssetError, match="missing"):
        assets.render_from(v1, {"other": "x"})


def test_a_substituted_value_is_never_parsed_as_a_template(root):
    a = assets.prompt("drafter_system", {}, root=root)
    assert assets.render(a, merchant="$merchant ${x}") == "Draft for $merchant ${x}.\n"


def test_toml_assets_parse_and_reject_floats(root):
    a, data = assets.config("fx", root=root)
    assert (a.name, a.version, data) == ("fx", "snap", {"label": "x", "rate": "1.10"})
    _, tools = assets.prompt_table("tools", {}, root=root)
    assert tools == {"finish": "Stop."}
    (root / "fx" / "float.toml").write_text("rate = 1.10\n")
    assets.lock(root)
    with pytest.raises(AssetError, match="float"):
        assets.load_toml("fx/float.toml", root=root)


def test_the_shipped_assets_all_match_the_lock():
    """Every file under nirnaya/assets is locked and unchanged, and every pin resolves."""
    assert assets.lock(assets.ROOT, write=False) == []
    for name in assets.active(assets.ROOT)["prompts"]:
        assets.prompt(name, {})
    for key in assets.active(assets.ROOT)["config"]:
        assets.config(key)


# Hashes of the constants these files replaced (taken before the move), so the move provably
# changed nothing: the same policy, FX snapshot and prompt text as before.
BEFORE_MOVE = {
    "policy_json": "988c70d2b602be37220735a4371d16f0ac086d8aec1989a5c73502e6eb769dbd",
    "fx_json": "85f35e1c42b8c9d0e868a19e7178188f67de621abb0e9e53d563a97e0cc9666a",
    "investigator_system": "1a2f30db35063afc2bcf7ba9032fb6dad63b51153d96ca44172abded14ab7ded",
    "drafter_system": "6c9a943c49efcb3903249f5f022a615304f6850762a85a259bf16725a26b836a",
    "baseline_naive_system": "1e33f22c05c7bfa09ecc74aeaf2e4082cf74fcdd2c334f177f0573e88a13e3ef",
}


def test_moving_policy_and_fx_to_files_changed_no_value():
    import tomllib

    from nirnaya.config.demo import DEMO_FX
    from nirnaya.models import MerchantPolicy

    moved = assets.load("policies/merchant-demo/demo-2026-10.toml")
    policy = MerchantPolicy.model_validate(tomllib.loads(moved.text))
    assert assets.digest(policy.model_dump_json()) == BEFORE_MOVE["policy_json"]
    assert assets.digest(DEMO_FX.model_dump_json()) == BEFORE_MOVE["fx_json"]


@pytest.mark.parametrize("name", ["investigator_system", "drafter_system", "baseline_naive_system"])
def test_v1_prompts_are_the_text_that_was_in_code(name):
    a = assets.load(f"prompts/{name}/v1.md")
    assert a.sha256 == BEFORE_MOVE[name]
