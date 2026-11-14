"""Authentication: password hashing, JWT creation/verification, and the
``get_current_user`` dependency that protects routes.

How the flow works
------------------
1. ``POST /auth/register`` stores the user with a **bcrypt hash** of the
   password (never the password itself).
2. ``POST /auth/login`` checks the password and returns a signed **JWT**
   (JSON Web Token). The token contains the user id (``sub``) and an expiry
   time (``exp``), signed with ``JWT_SECRET_KEY`` using HMAC-SHA256.
3. The client sends ``Authorization: Bearer <token>`` on protected requests.
4. ``get_current_user`` verifies the signature and expiry, loads the user,
   and returns it. Any failure produces ``401 Unauthorized``.

Because the token is signed, the server does not need to store sessions:
it can trust the user id inside a token whose signature is valid.
"""

from datetime import UTC, datetime, timedelta
from typing import Annotated

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from db import get_db, settings
from models import User

# Tells FastAPI (and Swagger's "Authorize" button) that tokens are obtained
# from /auth/login and sent as a Bearer header.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


# ---------------------------------------------------------------------------
# Passwords
# ---------------------------------------------------------------------------


def hash_password(password: str) -> str:
    """Return a bcrypt hash of ``password``.

    bcrypt adds a random salt and is deliberately slow, so identical passwords
    produce different hashes and brute-forcing a leaked hash is expensive.
    """
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, hashed_password: str) -> bool:
    """Return True if ``password`` matches the stored bcrypt hash."""
    return bcrypt.checkpw(password.encode("utf-8"), hashed_password.encode("utf-8"))


# A real hash used when the e-mail does not exist, so a failed login takes the
# same time whether or not the account exists (prevents user enumeration by
# measuring response time).
DUMMY_PASSWORD_HASH = hash_password("dummy-password-for-timing")


# ---------------------------------------------------------------------------
# JSON Web Tokens
# ---------------------------------------------------------------------------


def create_access_token(user_id: int) -> str:
    """Create a signed JWT that identifies ``user_id`` and expires later."""
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),  # "subject": who the token is about (must be a string)
        "iat": now,  # issued at
        "exp": now + timedelta(minutes=settings.access_token_expire_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> int:
    """Validate a JWT and return the user id it contains.

    Raises ``jwt.InvalidTokenError`` (or a subclass such as
    ``ExpiredSignatureError``) if the token is malformed, tampered with or
    expired.
    """
    # Passing ``algorithms`` explicitly prevents "alg=none" style attacks.
    payload = jwt.decode(
        token,
        settings.jwt_secret_key,
        algorithms=[settings.jwt_algorithm],
        options={"require": ["exp", "sub"]},
    )
    return int(payload["sub"])


# ---------------------------------------------------------------------------
# FastAPI dependency
# ---------------------------------------------------------------------------

CREDENTIALS_ERROR = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Could not validate credentials",
    headers={"WWW-Authenticate": "Bearer"},
)


async def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    """Return the logged-in user, or raise 401.

    Add ``Depends(get_current_user)`` to any route that must be protected.
    ``oauth2_scheme`` itself already answers 401 if the header is missing.
    """
    try:
        user_id = decode_access_token(token)
    except (jwt.InvalidTokenError, ValueError):
        raise CREDENTIALS_ERROR from None

    user = await db.get(User, user_id)
    if user is None:  # valid token, but the account was deleted
        raise CREDENTIALS_ERROR
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
