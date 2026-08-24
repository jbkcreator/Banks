"""MOD-02 Clay port tests."""
from banks.clay_port import FakeClayPort


def test_fake_clay_enrich_returns_expected_keys():
    port = FakeClayPort()
    result = port.enrich("Acme", "Jane Doe")
    assert set(result.keys()) == {"name", "email", "linkedin_url", "headcount"}


def test_fake_clay_enrich_never_raises():
    port = FakeClayPort()
    result = port.enrich("", None)
    assert isinstance(result, dict)


def test_fake_clay_enrich_name_fallback():
    port = FakeClayPort()
    result = port.enrich("Startup Inc")
    assert result["name"] == "Test Contact"


def test_fake_clay_enrich_uses_provided_name():
    port = FakeClayPort()
    result = port.enrich("Acme", "Bob Smith")
    assert result["name"] == "Bob Smith"
