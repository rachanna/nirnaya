"""Kit case 1 end to end: the RFI challenge carries no customer_info; after the issuer rejects
the evidence, the CHARGEBACK challenge carries the order's device ID and IP."""

from nirnaya.console.session import DemoSession, M


def test_k1_sends_device_and_ip_only_on_its_chargeback_challenge():
    s = DemoSession.offline()
    s.run_kit()
    s.reject_k1()
    first, second = [w for w in s.store.writes(M, "K1") if str(w.action) == "CHALLENGE"]
    assert (first.cycle, first.request["customer_info"]) == (1, None)  # RFI: not applicable
    info = second.request["customer_info"]
    assert second.cycle == 2
    assert (info["device_id"], info["ip"], info["source_record_id"]) == (
        "dev-k1-7f3a91",
        "203.0.113.24",
        "K1-order",
    )


def test_the_offline_sandbox_runner_sends_it_to_airwallex():
    from datetime import UTC, datetime

    from nirnaya.sandbox_demo import main

    lines: list[str] = []
    code = main(
        ["--offline", "--submit", "--yes"],
        env={"DRY_RUN": "false", "NIRNAYA_AGENT": "scripted"},
        out=lines.append,
        sleep=lambda _: None,
        now=datetime(2026, 10, 27, 12, tzinfo=UTC),
    )
    text = "\n".join(lines)
    assert code == 0
    assert "customer_info: device dev-k1-7f3a91, ip 203.0.113.24 (from" in text
