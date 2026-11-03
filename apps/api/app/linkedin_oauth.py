import hashlib
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode, urlparse

import httpx
from cryptography.fernet import Fernet, InvalidToken

AUTHORIZATION_ENDPOINT = "https://www.linkedin.com/oauth/v2/authorization"
TOKEN_ENDPOINT = "https://www.linkedin.com/oauth/v2/accessToken"
USERINFO_ENDPOINT = "https://api.linkedin.com/v2/userinfo"
LINKEDIN_SCOPES = ("openid", "profile", "w_member_social")
STATE_TTL = timedelta(minutes=10)


class LinkedInConfigurationError(Exception):
    pass


class LinkedInRequestError(Exception):
    pass


@dataclass(frozen=True)
class LinkedInSettings:
    client_id: str
    client_secret: str
    redirect_uri: str
    encryption_key: str
    api_version: str


@dataclass(frozen=True)
class LinkedInToken:
    access_token: str
    refresh_token: str
    scopes: list[str]
    expires_at: datetime
    refresh_expires_at: datetime | None


def linkedin_settings() -> LinkedInSettings:
    client_id = os.getenv("LINKEDIN_CLIENT_ID", "").strip()
    client_secret = os.getenv("LINKEDIN_CLIENT_SECRET", "").strip()
    redirect_uri = os.getenv("LINKEDIN_REDIRECT_URI", "").strip()
    if not redirect_uri:
        api_base_url = os.getenv("API_BASE_URL", "http://localhost:8000").strip().rstrip("/")
        redirect_uri = f"{api_base_url}/api/v1/integrations/linkedin/callback"
    encryption_key = os.getenv("SOCIAL_TOKEN_ENCRYPTION_KEY", "").strip()
    api_version = os.getenv("LINKEDIN_API_VERSION", "").strip()
    if not client_id or not client_secret:
        raise LinkedInConfigurationError("LinkedIn client credentials are not configured.")
    if not encryption_key:
        raise LinkedInConfigurationError("Social token encryption is not configured.")
    if not api_version or len(api_version) != 6 or not api_version.isdigit():
        raise LinkedInConfigurationError("A valid LinkedIn API version (YYYYMM) is not configured.")
    parsed_uri = urlparse(redirect_uri)
    if (
        parsed_uri.scheme not in ("https", "http")
        or not parsed_uri.netloc
        or parsed_uri.username
        or parsed_uri.password
    ):
        raise LinkedInConfigurationError("The LinkedIn redirect URI is invalid.")
    if os.getenv("APP_ENV", "local").lower() in {"production", "prod"}:
        if parsed_uri.scheme != "https":
            raise LinkedInConfigurationError("The LinkedIn redirect URI must use HTTPS in production.")
    elif parsed_uri.scheme != "https" and parsed_uri.hostname not in ("localhost", "127.0.0.1", "::1"):
        raise LinkedInConfigurationError("Non-local LinkedIn redirect URIs must use HTTPS.")
    _fernet(encryption_key)
    return LinkedInSettings(client_id, client_secret, redirect_uri, encryption_key, api_version)


def configuration_status() -> dict[str, Any]:
    try:
        settings = linkedin_settings()
    except LinkedInConfigurationError as error:
        return {"configured": False, "reason": str(error)}
    return {
        "configured": True,
        "redirect_uri": settings.redirect_uri,
        "required_scopes": list(LINKEDIN_SCOPES),
        "api_version": settings.api_version,
        "publishing_enabled": linkedin_publishing_enabled(),
    }


def linkedin_publishing_enabled() -> bool:
    return os.getenv("PUBLISHING_ENABLED", "false").strip().lower() == "true"


def create_state() -> tuple[str, str, datetime]:
    raw_state = secrets.token_urlsafe(32)
    return raw_state, hashlib.sha256(raw_state.encode()).hexdigest(), datetime.now(timezone.utc) + STATE_TTL


def authorization_url(settings: LinkedInSettings, state: str) -> str:
    query = urlencode({
        "response_type": "code",
        "client_id": settings.client_id,
        "redirect_uri": settings.redirect_uri,
        "state": state,
        "scope": " ".join(LINKEDIN_SCOPES),
    })
    return f"{AUTHORIZATION_ENDPOINT}?{query}"


def exchange_code(settings: LinkedInSettings, code: str) -> LinkedInToken:
    try:
        response = httpx.post(
            TOKEN_ENDPOINT,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": settings.redirect_uri,
                "client_id": settings.client_id,
                "client_secret": settings.client_secret,
            },
            headers={"Accept": "application/json"},
            timeout=httpx.Timeout(20.0, connect=5.0),
        )
    except httpx.RequestError as error:
        raise LinkedInRequestError("LinkedIn authorization could not be completed. Start a new connection attempt.") from error
    return _parse_token_response(response, list(LINKEDIN_SCOPES))


def fetch_profile(access_token: str) -> dict[str, str]:
    try:
        response = httpx.get(
            USERINFO_ENDPOINT,
            headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
            timeout=httpx.Timeout(15.0, connect=5.0),
        )
    except httpx.RequestError as error:
        raise LinkedInRequestError("LinkedIn profile could not be retrieved. Start a new connection attempt.") from error
    if response.is_error:
        raise LinkedInRequestError("LinkedIn profile could not be retrieved. Confirm the app has the profile permission.")
    try:
        payload = response.json()
    except ValueError as error:
        raise LinkedInRequestError("LinkedIn returned an invalid profile response.") from error
    if not isinstance(payload, dict):
        raise LinkedInRequestError("LinkedIn returned an invalid profile response.")
    subject = payload.get("sub")
    name = payload.get("name")
    if not isinstance(subject, str) or not subject.strip() or not isinstance(name, str) or not name.strip():
        raise LinkedInRequestError("LinkedIn profile response is missing a usable member identity.")
    return {"subject": subject.strip(), "name": name.strip()}


def refresh_access_token(settings: LinkedInSettings, refresh_token: str, previous_scopes: list[str]) -> LinkedInToken:
    try:
        response = httpx.post(
            TOKEN_ENDPOINT,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": settings.client_id,
                "client_secret": settings.client_secret,
            },
            headers={"Accept": "application/json"},
            timeout=httpx.Timeout(20.0, connect=5.0),
        )
    except httpx.RequestError as error:
        raise LinkedInRequestError("LinkedIn token refresh failed; reconnect the account.") from error
    return _parse_token_response(response, previous_scopes)


def encrypt_token(token: str, encryption_key: str | None = None) -> str:
    key = encryption_key or linkedin_settings().encryption_key
    return _fernet(key).encrypt(token.encode()).decode()


def decrypt_token(ciphertext: str, encryption_key: str | None = None) -> str:
    key = encryption_key or linkedin_settings().encryption_key
    try:
        return _fernet(key).decrypt(ciphertext.encode()).decode()
    except (InvalidToken, UnicodeDecodeError, ValueError) as error:
        raise LinkedInConfigurationError("Stored LinkedIn credentials cannot be decrypted; reconnect after restoring the token encryption key.") from error


def _fernet(key: str) -> Fernet:
    try:
        return Fernet(key.encode())
    except (ValueError, TypeError) as error:
        raise LinkedInConfigurationError("Social token encryption key is invalid; provide a Fernet-compatible key.") from error


def _parse_token_response(response: httpx.Response, requested_scopes: list[str]) -> LinkedInToken:
    if response.is_error:
        raise LinkedInRequestError("LinkedIn rejected the authorization request. Check the redirect URI and approved app permissions.")
    try:
        payload = response.json()
    except ValueError as error:
        raise LinkedInRequestError("LinkedIn returned an invalid token response.") from error
    if not isinstance(payload, dict):
        raise LinkedInRequestError("LinkedIn returned an invalid token response.")
    access_token = payload.get("access_token")
    expires_in = payload.get("expires_in")
    if not isinstance(access_token, str) or not access_token or not isinstance(expires_in, int) or expires_in <= 0:
        raise LinkedInRequestError("LinkedIn returned incomplete token information.")
    scopes_value = payload.get("scope")
    if scopes_value is None:
        scopes = requested_scopes
    elif isinstance(scopes_value, str):
        scopes = sorted(set(scopes_value.split()))
    else:
        raise LinkedInRequestError("LinkedIn returned invalid permission information.")
    refresh_token = payload.get("refresh_token") or ""
    if not isinstance(refresh_token, str):
        raise LinkedInRequestError("LinkedIn returned invalid refresh-token information.")
    refresh_expires_in = payload.get("refresh_token_expires_in")
    if refresh_expires_in is not None and (
        not isinstance(refresh_expires_in, int) or refresh_expires_in <= 0
    ):
        raise LinkedInRequestError("LinkedIn returned invalid refresh-token expiration.")
    now = datetime.now(timezone.utc)
    return LinkedInToken(
        access_token=access_token,
        refresh_token=refresh_token,
        scopes=scopes,
        expires_at=now + timedelta(seconds=expires_in),
        refresh_expires_at=(
            now + timedelta(seconds=refresh_expires_in)
            if refresh_expires_in is not None else None
        ),
    )
