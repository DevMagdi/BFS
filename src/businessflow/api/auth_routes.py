from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from businessflow.api.dependencies import get_current_user, get_session, get_settings
from businessflow.config import Settings
from businessflow.models import UserAccount
from businessflow.schemas import (
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    TokenResponse,
    UserResponse,
)
from businessflow.services.authentication import (
    AuthFailure,
    IssuedTokens,
    login,
    logout,
    refresh,
    register,
)

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
    "/register",
    response_model=TokenResponse,
    status_code=201,
    responses={409: {"description": "Registration conflict"}},
)
def register_user(
    body: RegisterRequest,
    request: Request,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> TokenResponse | Response:
    result = register(session, body, settings, request.state.request_id)
    if isinstance(result, AuthFailure):
        return _failure_response(request, result)
    return _token_response(result)


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


@router.post("/logout", status_code=204)
def logout_session(
    request: Request,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> Response:
    claims = request.state.access_claims
    logout(session, user, claims.session_id, request.state.request_id)
    return Response(status_code=204)


@router.get("/me", response_model=UserResponse)
def current_user(user: UserAccount = Depends(get_current_user)) -> UserAccount:
    return user
