from fastapi import APIRouter, Depends, Request, Response

from .dependencies import current_user, require_origin, session_payload
from .schemas import PhoneRequest, VerifyRequest

router = APIRouter(prefix="/api/auth")


@router.post("/request", dependencies=[Depends(require_origin)])
def request_code(body: PhoneRequest, request: Request):
    ip = request.client.host if request.client else "unknown"
    return request.app.state.auth.request_code(body.phone, ip)


@router.post("/verify", dependencies=[Depends(require_origin)])
def verify(body: VerifyRequest, request: Request, response: Response):
    raw, user = request.app.state.auth.verify(str(body.challenge_id), body.code)
    response.set_cookie(request.app.state.cookie_name, raw, max_age=86400,
        secure=request.app.state.cookie_secure, httponly=True, samesite="lax", path="/")
    return session_payload(request, user, raw)


@router.get("/session")
def session(request: Request, user=Depends(current_user)):
    return session_payload(request, user, request.cookies[request.app.state.cookie_name])


@router.post("/logout")
def logout(request: Request, user=Depends(current_user)):
    request.app.state.auth.logout(request.cookies[request.app.state.cookie_name])
    # Revoke server-side without Set-Cookie: a delayed logout response must not
    # erase the cookie from a newer login. The revoked identifier cannot authenticate
    # and expires normally or is replaced by the next login.
    return {"ok": True}
