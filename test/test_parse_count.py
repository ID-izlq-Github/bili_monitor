from bili_monitor.api.client import _parse_count


def test_none():
    assert _parse_count(None) == 0


def test_int():
    assert _parse_count(4000) == 4000
    assert _parse_count(0) == 0


def test_float():
    assert _parse_count(1.5) == 1


def test_plain_int_str():
    assert _parse_count("4000") == 4000
    assert _parse_count("0") == 0
    assert _parse_count("  123  ") == 123


def test_plus_suffix():
    assert _parse_count("4000+") == 4000
    assert _parse_count("100+") == 100


def test_w_suffix():
    assert _parse_count("1.2w") == 12000
    assert _parse_count("3.5w") == 35000
    assert _parse_count("10w") == 100000
    assert _parse_count("0.5w") == 5000


def test_wan_suffix():
    assert _parse_count("1.2万") == 12000
    assert _parse_count("10万") == 100000


def test_k_suffix():
    assert _parse_count("1.2K") == 1200
    assert _parse_count("3.5k") == 3500
    assert _parse_count("10K") == 10000


def test_invalid_str():
    assert _parse_count("abc") == 0
    assert _parse_count("") == 0


def test_mixed_whitespace():
    assert _parse_count("  4000+  ") == 4000
    assert _parse_count("  1.2w  ") == 12000
