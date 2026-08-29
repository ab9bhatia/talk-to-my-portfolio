from shared.web.app_urls import _ipv4_from_ifconfig, discover_lan_ipv4, portfolio_display_url


def test_wildcard_bind_is_replaced_with_lan_ip_in_portfolio_url():
    url = portfolio_display_url(
        "http://0.0.0.0:9000/talktomyportfolio",
        lan_ip_resolver=lambda: "192.168.1.42",
    )

    assert url == "http://192.168.1.42:9000/talktomyportfolio/portfolio"


def test_explicit_display_host_wins_for_multi_network_machines():
    url = portfolio_display_url(
        "http://0.0.0.0:9000/talktomyportfolio",
        display_host="10.0.0.25",
        lan_ip_resolver=lambda: "192.168.1.42",
    )

    assert url == "http://10.0.0.25:9000/talktomyportfolio/portfolio"


def test_lan_bind_replaces_localhost_base_url():
    url = portfolio_display_url(
        "http://127.0.0.1:9000/talktomyportfolio",
        bind_host="0.0.0.0",
        lan_ip_resolver=lambda: "192.168.1.42",
    )

    assert url == "http://192.168.1.42:9000/talktomyportfolio/portfolio"


def test_configured_hostname_is_preserved():
    url = portfolio_display_url(
        "https://portfolio.home/talktomyportfolio",
        lan_ip_resolver=lambda: "192.168.1.42",
    )

    assert url == "https://portfolio.home/talktomyportfolio/portfolio"


def test_lan_discovery_skips_failed_and_unreachable_candidates():
    def failed_probe():
        raise OSError("no route")

    assert discover_lan_ipv4(
        (failed_probe, lambda: "0.0.0.0", lambda: "127.0.0.1", lambda: "192.168.50.7")
    ) == "192.168.50.7"


def test_lan_discovery_falls_back_to_localhost():
    assert discover_lan_ipv4((lambda: "0.0.0.0", lambda: "127.0.0.1")) == "127.0.0.1"


def test_ifconfig_parser_prefers_active_lan_interface_over_vpn():
    output = """utun0: flags=8051<UP,POINTOPOINT,RUNNING>
    inet 10.8.0.2 netmask 0xffffff00
en0: flags=8863<UP,BROADCAST,RUNNING>
    inet 192.168.8.192 netmask 0xffffff00
    status: active
"""

    assert _ipv4_from_ifconfig(output) == "192.168.8.192"
