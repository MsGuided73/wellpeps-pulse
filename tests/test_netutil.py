"""Real client IP behind a trusted reverse proxy (harvey/netutil.py)."""

import pytest

from harvey.netutil import client_ip, parse_networks

DOCKER = parse_networks("10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,127.0.0.1/32")


def test_parse_networks_accepts_ips_cidrs_and_lists():
    nets = parse_networks(["10.0.0.0/8", " 192.0.2.7 ", "2001:db8::/32"])

    assert [str(n) for n in nets] == ["10.0.0.0/8", "192.0.2.7/32", "2001:db8::/32"]
    assert parse_networks("") == ()
    assert parse_networks("  ,  ") == ()
    assert parse_networks(None) == ()


@pytest.mark.parametrize("bad", ["not-an-ip", "10.0.0.0/33", "10.0.0.1/8", "1.2.3.4,,oops"])
def test_parse_networks_rejects_garbage(bad):
    with pytest.raises(ValueError):
        parse_networks(bad)


def test_untrusted_peer_is_returned_and_xff_ignored():
    # A direct attacker (not a proxy) tries to spoof its address.
    assert client_ip("203.0.113.9", "1.1.1.1", DOCKER) == "203.0.113.9"
    assert client_ip("203.0.113.9", "10.0.0.5, 1.1.1.1", DOCKER) == "203.0.113.9"


def test_no_trusted_proxies_means_xff_is_never_used():
    assert client_ip("10.0.0.2", "198.51.100.4", ()) == "10.0.0.2"


def test_trusted_peer_uses_the_rightmost_untrusted_hop():
    assert client_ip("10.0.1.2", "198.51.100.4", DOCKER) == "198.51.100.4"


def test_spoofed_left_entries_are_ignored_behind_the_proxy():
    # The client sent "X-Forwarded-For: 6.6.6.6"; Traefik appended the real address.
    assert client_ip("172.18.0.3", "6.6.6.6, 198.51.100.4", DOCKER) == "198.51.100.4"


def test_multi_hop_chain_skips_every_trusted_proxy():
    xff = "6.6.6.6, 198.51.100.4, 192.168.1.10, 10.0.0.7"
    assert client_ip("172.18.0.3", xff, DOCKER) == "198.51.100.4"


def test_all_hops_trusted_falls_back_to_the_peer():
    assert client_ip("10.0.0.2", "10.0.0.3, 192.168.0.4", DOCKER) == "10.0.0.2"


@pytest.mark.parametrize("xff", ["", None, "   ", "garbage", "198.51.100.4, not-an-ip", "1.2.3.4:99999x"])
def test_garbage_xff_from_a_trusted_peer_falls_back_to_the_peer(xff):
    assert client_ip("10.0.0.2", xff, DOCKER) == "10.0.0.2"


def test_ipv6_client_and_bracketed_forms():
    trusted = parse_networks("fd00::/8")

    assert client_ip("fd00::1", "2001:db8::42", trusted) == "2001:db8::42"
    assert client_ip("fd00::1", "[2001:db8::42]", trusted) == "2001:db8::42"
    assert client_ip("2001:db8::99", "2001:db8::42", trusted) == "2001:db8::99"


def test_ipv4_mapped_peer_matches_ipv4_networks():
    assert client_ip("::ffff:10.0.0.2", "198.51.100.4", DOCKER) == "198.51.100.4"


def test_unparseable_peer_is_returned_verbatim():
    assert client_ip("testclient", "198.51.100.4", DOCKER) == "testclient"
    assert client_ip("", "198.51.100.4", DOCKER) == "unknown"
