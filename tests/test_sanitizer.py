"""脱敏器单元测试"""

from src.utils.sanitizer import Sanitizer


def test_password_masked():
    s = Sanitizer()
    out = s.sanitize("config: password=abc123 token=xyz")
    assert "abc123" not in out
    assert "xyz" not in out
    assert "[已脱敏]" in out


def test_ip_masked():
    s = Sanitizer()
    assert s.sanitize("server 192.168.1.100 down") == "server X.X.X.X down"


def test_phone_masked():
    s = Sanitizer()
    out = s.sanitize("contact 13812345678")
    assert "13812345678" not in out


def test_email_masked():
    s = Sanitizer()
    out = s.sanitize("user foo@bar.com failed")
    assert "foo@bar.com" not in out


def test_disable():
    s = Sanitizer(enable=False)
    assert s.sanitize("password=abc") == "password=abc"
