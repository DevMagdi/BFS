from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Request, Response
from fastapi.responses import JSONResponse
from jwt import InvalidTokenError
from sqlalchemy.orm import Session

from businessflow.api.dependencies import get_current_user, get_session, get_settings
from businessflow.config import Settings
from businessflow.models import UserAccount
from businessflow.schemas import (
    LoginRequest,
    LogoutRequest,
    RefreshRequest,
    TokenResponse,
    UserResponse,
)
from businessflow.security.tokens import decode_access_token
from businessflow.services.authentication import AuthFailure, IssuedTokens, login, logout, refresh

router = APIRouter(prefix="/auth", tags=["authentication"])


def _token_response(result: IssuedTokens) -> TokenResponse:
    return TokenResponse(
        access_token=result.access_token,
        refresh_token=result.refresh_token,
        expires_in=result.expires_in,
        session_expires_at=result.session_expires_at,
    )


def _failure_response(request: Request, failure: AuthFailure) -> JSONResponse:
    response = JSONResponse(
        status_code=failure.status_code,
        content={
            "error": {
                "code": failure.code,
                "message": failure.message,
                "request_id": str(request.state.request_id),
            }
        },
    )
    if failure.retry_after is not None:
        response.headers["Retry-After"] = str(failure.retry_after)
    return response


@router.post(
    "/login", response_model=TokenResponse, responses={401: {"description": "Invalid credentials"}}
)
def login_user(
    body: LoginRequest,
    request: Request,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> TokenResponse | Response:
    remote_ip = request.client.host if request.client is not None else "unknown"
    result = login(session, body, settings, request.state.request_id, remote_ip)
    if isinstance(result, AuthFailure):
        return _failure_response(request, result)
    return _token_response(result)


@router.post(
    "/refresh",
    response_model=TokenResponse,
    responses={401: {"description": "Invalid refresh token"}},
)
def refresh_tokens(
    body: RefreshRequest,
    request: Request,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> TokenResponse | Response:
    result = refresh(session, body.refresh_token, settings, request.state.request_id)
    if isinstance(result, AuthFailure):
        return _failure_response(request, result)
    return _token_response(result)


@router.post(
    "/logout",
    status_code=204,
    openapi_extra={"security": [{"BearerAuth": []}, {}]},
)
def logout_session(
    request: Request,
    body: LogoutRequest | None = None,
    authorization: str | None = Header(default=None, alias="Authorization"),
    # Flush/commit logout before the 204 is sent so a database failure cannot be reported
    # as successful revocation while the refresh chain remains active.
    session: Session = Depends(get_session, scope="function"),
    settings: Settings = Depends(get_settings),
) -> Response:
    if body is not None and body.refresh_token is not None:
        result = logout(
            session,
            request.state.request_id,
            raw_refresh_token=body.refresh_token,
        )
    else:
        if authorization is None:
            return _failure_response(
                request,
                AuthFailure(401, "authentication_required", "Authentication is required."),
            )
        scheme, separator, access_token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not separator or not access_token.strip():
            return _failure_response(
                request,
                AuthFailure(401, "invalid_token", "Access token is invalid or expired."),
            )
        try:
            claims = decode_access_token(access_token.strip(), settings)
        except InvalidTokenError:
            return _failure_response(
                request,
                AuthFailure(401, "invalid_token", "Access token is invalid or expired."),
            )
        result = logout(
            session,
            request.state.request_id,
            user_id=claims.user_id,
            session_id=claims.session_id,
        )
    if isinstance(result, AuthFailure):
        return _failure_response(request, result)
    return Response(status_code=204)


@router.get("/me", response_model=UserResponse)
def current_user(user: UserAccount = Depends(get_current_user)) -> UserAccount:
    return user
