import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pwdlib import PasswordHash
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Membership, SessionToken, User

password_hash = PasswordHash.recommended()
bearer = HTTPBearer(auto_error=False)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(db: Session, user: User) -> str:
    token = secrets.token_urlsafe(32)
    db.add(SessionToken(
        token_hash=hash_token(token),
        user_id=user.id,
        expires_at=datetime.now(timezone.utc) + timedelta(days=7),
    ))
    return token


def current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> User:
    if credentials is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    token = db.scalar(select(SessionToken).where(SessionToken.token_hash == hash_token(credentials.credentials)))
    if token is None:
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    expiry = token.expires_at
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    if expiry <= datetime.now(timezone.utc):
        db.delete(token)
        db.commit()
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    user = db.get(User, token.user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid session")
    return user


def workspace_member(db: Session, user: User, organization_id: int) -> Membership:
    membership = db.scalar(select(Membership).where(
        Membership.organization_id == organization_id,
        Membership.user_id == user.id,
        Membership.active.is_(True),
    ))
    if membership is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    return membership


def require_role(membership: Membership, *roles: str) -> None:
    if membership.role not in roles:
        raise HTTPException(status_code=403, detail="Insufficient workspace permissions")
