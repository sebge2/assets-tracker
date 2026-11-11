from __future__ import annotations

import sys
from typing import Any

import pytest

from assets_tracker.db import keychain


class FakeSecurity:
    kSecClass = "class"
    kSecClassGenericPassword = "generic-password"
    kSecAttrService = "service"
    kSecAttrAccount = "account"
    kSecReturnData = "return-data"
    kSecMatchLimit = "match-limit"
    kSecMatchLimitOne = "one"
    kSecAttrAccessibleWhenUnlockedThisDeviceOnly = "this-device-only"
    kSecAttrAccessible = "accessible"
    kSecValueData = "value-data"
    errSecSuccess = 0
    errSecItemNotFound = -25300

    def __init__(self) -> None:
        self.added_attributes: dict[str, Any] | None = None

    def SecItemCopyMatching(self, query: dict[str, Any], _: None) -> tuple[int, None]:
        assert query[self.kSecAttrService] == "com.assets-tracker.database-key"
        return self.errSecItemNotFound, None

    def SecItemAdd(self, attributes: dict[str, Any], _: None) -> tuple[int, None]:
        self.added_attributes = attributes
        return self.errSecSuccess, None


def test_first_database_key_is_stored_on_this_unlocked_device(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    security = FakeSecurity()
    monkeypatch.setattr(keychain.sys, "platform", "darwin")
    monkeypatch.setitem(sys.modules, "Security", security)
    monkeypatch.setattr(keychain, "_require_touch_id", lambda: None)

    key = keychain.database_key()

    assert len(key) == 32
    assert security.added_attributes is not None
    assert security.added_attributes[security.kSecValueData] == key
    assert (
        security.added_attributes[security.kSecAttrAccessible]
        == security.kSecAttrAccessibleWhenUnlockedThisDeviceOnly
    )
