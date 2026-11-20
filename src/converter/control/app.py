"""Authentication, user administration and a protected workspace shell."""

from contextlib import asynccontextmanager
import hmac
import os
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from converter.security.store import AccessError, IdentityStore
from converter.hardware.encoder_capabilities import EncoderCapabilityManager
from converter.hardware.decklink_capabilities import DeckLinkCapabilityManager


ROOT = Path(__file__).resolve().parents[3]
WEB = ROOT / "frontend" / "web"
COOKIE = "ip_media_tool_session"
STORE = IdentityStore(os.environ.get("IP_MEDIA_TOOL_SECURITY_DIR", "/var/lib/ip-media-tool/security"))
SECURE_COOKIE = os.environ.get("IP_MEDIA_TOOL_COOKIE_SECURE", "false").lower() == "true"
ENCODERS = EncoderCapabilityManager(os.environ.get(
    "IP_MEDIA_TOOL_GPU_CACHE", "/var/lib/ip-media-tool/hardware/encoder-capabilities.json"
))
DECKLINK = DeckLinkCapabilityManager(os.environ.get(
    "IP_MEDIA_TOOL_DECKLINK_CACHE", "/var/lib/ip-media-tool/hardware/decklink-capabilities.json"
))


@asynccontextmanager
async def lifespan(application):
    STORE.initialize()
    ENCODERS.initialize()
    DECKLINK.initialize()
    try:
        yield
    finally:
        ENCODERS.close()
        DECKLINK.close()


app = FastAPI(title="IP Media Tool", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)


@app.exception_handler(AccessError)
async def access_error(request, error):
    return JSONResponse({"detail": str(error)}, status_code=error.status)


@app.middleware("http")
async def security_headers(request, call_next):
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        origin = request.headers.get("origin")
        if origin != str(request.base_url).rstrip("/"):
            return JSONResponse({"detail": "Запит має надходити з цього інтерфейсу."}, status_code=403)
        length = request.headers.get("content-length", "")
        if not length.isdecimal() or len(length) > 6 or int(length) > 16384 or "transfer-encoding" in request.headers:
            return JSONResponse({"detail": "Неприпустимий розмір запиту."}, status_code=413)
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    )
    return response


def identity(request: Request):
    user, csrf = STORE.identity(request.cookies.get(COOKIE))
    if request.method not in {"GET", "HEAD"}:
        provided = request.headers.get("x-csrf-token", "")
        if not provided.isascii() or not hmac.compare_digest(provided, csrf):
            raise HTTPException(403, "Оновіть сторінку та повторіть дію.")
    return user, csrf


def administrator(current=Depends(identity)):
    if current[0]["role"] != "admin":
        raise HTTPException(403, "Потрібні права адміністратора.")
    return current[0]


class Login(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class CreateUser(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    display_name: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=12, max_length=128)
    role: Literal["admin", "operator", "viewer"]


class UpdateUser(BaseModel):
    revision: int = Field(ge=1)
    display_name: str = Field(min_length=1, max_length=80)
    role: Literal["admin", "operator", "viewer"]
    enabled: bool


class Password(BaseModel):
    password: str = Field(min_length=12, max_length=128)


class OwnPassword(Password):
    current_password: str = Field(min_length=1, max_length=128)


@app.get("/healthz")
def health():
    return {"status": "ok", "application": "IP_Media_tool"}


@app.get("/api/setup")
def setup():
    return {"admin_ready": STORE.ready()}


@app.post("/api/auth/login")
def login(body: Login, request: Request):
    if not STORE.ready():
        raise HTTPException(503, "Спочатку створіть адміністратора командою ip-media-tool-admin bootstrap на сервері.")
    token, csrf, user = STORE.login(body.username, body.password, request.client.host if request.client else "unknown")
    response = JSONResponse({"user": user, "csrf": csrf})
    response.set_cookie(COOKIE, token, max_age=STORE.session_seconds, httponly=True, secure=SECURE_COOKIE, samesite="strict", path="/")
    return response


@app.get("/api/session")
def session(current=Depends(identity)):
    return {"user": current[0], "csrf": current[1]}


@app.get("/api/hardware/encoder-capabilities")
def encoder_capabilities(current=Depends(identity)):
    return ENCODERS.snapshot()


@app.post("/api/hardware/encoder-capabilities/refresh", status_code=202)
def refresh_encoder_capabilities(current=Depends(identity)):
    if current[0]["role"] not in {"admin", "operator"}:
        raise HTTPException(403, "Повторна перевірка доступна адміністратору та оператору.")
    started = ENCODERS.start_query(force=True)
    if started:
        STORE.record("hardware.encoder_refresh", current[0]["username"])
    return {"started": started, **ENCODERS.snapshot()}


@app.get("/api/hardware/decklink-capabilities")
def decklink_capabilities(current=Depends(identity)):
    return DECKLINK.snapshot()


@app.post("/api/hardware/decklink-capabilities/refresh", status_code=202)
def refresh_decklink_capabilities(current=Depends(identity)):
    if current[0]["role"] not in {"admin", "operator"}:
        raise HTTPException(403, "Повторна перевірка доступна адміністратору та оператору.")
    started = DECKLINK.start_query(force=True)
    if started:
        STORE.record("hardware.decklink_refresh", current[0]["username"])
    return {"started": started, **DECKLINK.snapshot()}


@app.post("/api/auth/logout")
def logout(request: Request, current=Depends(identity)):
    STORE.logout(request.cookies[COOKIE], current[0]["username"])
    response = JSONResponse({"ok": True})
    response.delete_cookie(COOKIE, path="/", secure=SECURE_COOKIE, httponly=True, samesite="strict")
    return response


@app.post("/api/auth/password")
def own_password(body: OwnPassword, current=Depends(identity)):
    STORE.change_password(current[0]["id"], body.password, old_password=body.current_password)
    response = JSONResponse({"ok": True})
    response.delete_cookie(COOKIE, path="/")
    return response


@app.get("/api/users")
def users(actor=Depends(administrator)):
    return {"users": STORE.list_users(actor["id"])}


@app.post("/api/users", status_code=201)
def create_user(body: CreateUser, actor=Depends(administrator)):
    return STORE.create_user(actor["id"], **body.dict())


@app.patch("/api/users/{user_id}")
def update_user(user_id: str, body: UpdateUser, actor=Depends(administrator)):
    return STORE.modify_user(actor["id"], user_id, **body.dict())


@app.delete("/api/users/{user_id}")
def delete_user(user_id: str, revision: int, actor=Depends(administrator)):
    STORE.modify_user(actor["id"], user_id, revision, "", "viewer", False, delete=True)
    return {"ok": True}


@app.post("/api/users/{user_id}/password")
def reset_password(user_id: str, body: Password, actor=Depends(administrator)):
    STORE.change_password(user_id, body.password, actor_id=actor["id"])
    return {"ok": True}


@app.get("/")
def root(request: Request):
    try:
        STORE.identity(request.cookies.get(COOKIE))
        destination = "/app"
    except AccessError:
        destination = "/login"
    return RedirectResponse(destination, status_code=303)


@app.get("/login")
def login_page():
    return FileResponse(WEB / "login.html")


@app.get("/app")
def workspace(request: Request):
    try:
        STORE.identity(request.cookies.get(COOKIE))
    except AccessError:
        return RedirectResponse("/login", status_code=303)
    return FileResponse(WEB / "app.html")


app.mount("/assets", StaticFiles(directory=WEB / "assets"), name="assets")
