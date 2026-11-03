import os

from cryptography.fernet import Fernet, InvalidToken

from app.ai_provider import AIProviderError


def _cipher() -> Fernet:
    key = os.getenv("AI_ENCRYPTION_KEY", "").strip()
    if not key:
        raise AIProviderError(
            "ai_encryption_not_configured",
            "Workspace AI credentials cannot be saved until AI_ENCRYPTION_KEY is configured.",
        )
    try:
        return Fernet(key.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as error:
        raise AIProviderError(
            "ai_encryption_misconfigured",
            "Workspace AI credential encryption is misconfigured.",
        ) from error


def encrypt_api_key(api_key: str) -> str:
    return _cipher().encrypt(api_key.encode("utf-8")).decode("ascii")


def decrypt_api_key(ciphertext: str) -> str:
    try:
        return _cipher().decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except (InvalidToken, UnicodeEncodeError, UnicodeDecodeError) as error:
        raise AIProviderError(
            "ai_credential_decryption_failed",
            "The saved workspace AI credential could not be decrypted. Reconfigure its provider key.",
        ) from error
