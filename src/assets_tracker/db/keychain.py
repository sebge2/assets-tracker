from __future__ import annotations

import secrets
import sys
from threading import Event
from typing import Any

_SERVICE = "com.assets-tracker.database-key"
_ACCOUNT = "portfolio"


class KeychainError(RuntimeError):
    """The database key could not be read from the protected macOS Keychain."""


def database_key() -> bytes:
    """Return the 256-bit SQLCipher key after Touch ID authorization.

    LocalAuthentication is used instead of a biometric Keychain access-control
    flag, which needs a provisioning entitlement on recent macOS versions.
    """
    if sys.platform != "darwin":
        raise KeychainError("Le déverrouillage sécurisé de la base requiert macOS.")

    try:
        import Security
    except ImportError as error:
        raise KeychainError("Les API de sécurité macOS sont indisponibles.") from error

    _require_touch_id()

    query: dict[Any, Any] = {
        Security.kSecClass: Security.kSecClassGenericPassword,
        Security.kSecAttrService: _SERVICE,
        Security.kSecAttrAccount: _ACCOUNT,
        Security.kSecReturnData: True,
        Security.kSecMatchLimit: Security.kSecMatchLimitOne,
    }
    status, result = Security.SecItemCopyMatching(query, None)
    if status == Security.errSecSuccess:
        key = bytes(result)
        if len(key) != 32:
            raise KeychainError("La clé stockée dans le Trousseau est invalide.")
        return key
    if status != Security.errSecItemNotFound:
        raise KeychainError(f"Le Trousseau macOS a refusé l'accès à la clé ({status}).")

    return _create_database_key(Security)


def _create_database_key(security: Any) -> bytes:
    key = secrets.token_bytes(32)
    attributes: dict[Any, Any] = {
        security.kSecClass: security.kSecClassGenericPassword,
        security.kSecAttrService: _SERVICE,
        security.kSecAttrAccount: _ACCOUNT,
        security.kSecValueData: key,
        security.kSecAttrAccessible: security.kSecAttrAccessibleWhenUnlockedThisDeviceOnly,
    }
    status, _ = security.SecItemAdd(attributes, None)
    if status != security.errSecSuccess:
        raise KeychainError(f"Impossible d'enregistrer la clé dans le Trousseau ({status}).")
    return key


def _require_touch_id() -> None:
    try:
        import LocalAuthentication
    except ImportError as error:
        raise KeychainError("Les API Touch ID de macOS sont indisponibles.") from error

    context = LocalAuthentication.LAContext.alloc().init()
    policy = LocalAuthentication.LAPolicyDeviceOwnerAuthenticationWithBiometrics
    available, availability_error = context.canEvaluatePolicy_error_(policy, None)
    if not available:
        raise KeychainError(f"Touch ID n'est pas disponible ({availability_error}).")

    completed = Event()
    result: dict[str, Any] = {}

    def reply(success: bool, authentication_error: Any) -> None:
        result["success"] = success
        result["error"] = authentication_error
        completed.set()

    context.evaluatePolicy_localizedReason_reply_(
        policy, "Déverrouiller votre portefeuille Assets Tracker", reply
    )
    if not completed.wait(timeout=60):
        raise KeychainError("Le délai d'authentification Touch ID est dépassé.")
    if not result["success"]:
        raise KeychainError(f"Authentification Touch ID refusée ({result['error']}).")
