from juggler import firewall

PY = r"C:\Users\x\python.exe"
XR = r"C:\Users\x\Juggler\bin\xray.exe"


def rule(prog, profile, action="Allow", direction="Inbound", enabled="True"):
    return {"program": prog.lower(), "profile": profile, "action": action,
            "direction": direction, "enabled": enabled}


def test_public_only_rules_block_on_private_network():
    # The real-world case: Windows asked once, on a Public network.
    rules = [rule(PY, "Public"), rule(XR, "Public")]
    assert firewall.evaluate("Private", rules, [PY, XR]) == {PY: "no rule", XR: "no rule"}
    assert firewall.evaluate("Public", rules, [PY, XR]) == {PY: "allowed", XR: "allowed"}


def test_block_rule_wins_and_any_profile_counts():
    rules = [rule(PY, "Any"), rule(XR, "Private, Public"), rule(XR, "Private", action="Block")]
    assert firewall.evaluate("Private", rules, [PY, XR]) == {PY: "allowed", XR: "blocked"}


def test_disabled_and_outbound_rules_ignored():
    rules = [rule(PY, "Private", enabled="False"), rule(PY, "Private", direction="Outbound")]
    assert firewall.evaluate("Private", rules, [PY]) == {PY: "no rule"}
