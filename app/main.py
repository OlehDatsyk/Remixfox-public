"""FastAPI app: the REST API under /api and the front end (public/) at /."""

import logging
import mimetypes
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Cookie, FastAPI, HTTPException, Request, status
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app import ai, api, auth, billing, email_login, jobs, passwords, telegram
from app.config import settings

logging.basicConfig(level=logging.INFO, format="%(levelname)s [%(name)s] %(message)s")
# httpx logs every request URL, and the Telegram Bot API puts the bot's token in it.
logging.getLogger("httpx").setLevel(logging.WARNING)

PUBLIC_DIR = Path(__file__).resolve().parent.parent / "public"
mimetypes.add_type("font/woff2", ".woff2")  # not known to every system's MIME table


@asynccontextmanager
async def lifespan(_: FastAPI):
    jobs.fail_interrupted_jobs()
    ai.start_worker()
    threading.Thread(target=telegram.setup, daemon=True, name="telegram-setup").start()
    yield


app = FastAPI(
    title="Remixfox API",
    version="0.9.0",
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url=None,
    openapi_url="/api/openapi.json",
)
app.add_middleware(GZipMiddleware, minimum_size=1000)


# Everything the pages use comes from this site (covers and photos are served by the API);
# inline scripts are the small theme and layout snippets in the HTML. /api/docs is left
# out: Swagger UI loads from a CDN.
PAGE_POLICY = (
    "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; font-src 'self'; connect-src 'self'; object-src 'none'; "
    "base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
)
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",  # never shown inside another site's frame
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}


@app.middleware("http")
async def revalidate_by_default(request: Request, call_next):
    # Browsers revalidate (a cheap 304 via ETag) so every deploy shows up at once.
    response = await call_next(request)
    response.headers.setdefault("Cache-Control", "no-cache")
    for name, value in SECURITY_HEADERS.items():
        response.headers.setdefault(name, value)
    if not request.url.path.startswith("/api/docs"):
        response.headers.setdefault("Content-Security-Policy", PAGE_POLICY)
    if request.url.scheme == "https":  # browsers then never try plain HTTP for a year
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
    return response


@app.middleware("http")
async def move_to_own_domain(request: Request, call_next):
    """Pages opened at reels-remix.onrender.com move to PUBLIC_URL (one address,
    one sign-in cookie). API calls are left alone, so nothing that calls the API
    by the old address breaks; www and plain HTTP are redirected by Render."""
    host = request.url.hostname or ""
    if (
        settings.public_url
        and host.endswith(".onrender.com")
        and request.method in ("GET", "HEAD")
        and not request.url.path.startswith("/api/")
    ):
        query = f"?{request.url.query}" if request.url.query else ""
        return RedirectResponse(f"{settings.public_url}{request.url.path}{query}", status_code=301)
    return await call_next(request)


@app.get("/", include_in_schema=False)
def home(session: auth.SessionDep, remixfox_session: Annotated[str | None, Cookie()] = None) -> FileResponse:
    """remixfox.co.uk itself: the app when signed in, else the landing page (the address to share)."""
    signed_in = auth.optional_user(session, remixfox_session) is not None
    page = PUBLIC_DIR / ("index.html" if signed_in else "welcome.html")
    return FileResponse(page, headers={"Vary": "Cookie"})


app.include_router(auth.router)
app.include_router(email_login.router)
app.include_router(passwords.router)
app.include_router(telegram.router)
app.include_router(billing.router)
app.include_router(api.router)


@app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"], include_in_schema=False)
def unknown_api_path(path: str) -> None:
    """Unknown API addresses answer in JSON; unknown pages get public/404.html."""
    raise HTTPException(status.HTTP_404_NOT_FOUND, "Not Found")


app.mount("/", StaticFiles(directory=PUBLIC_DIR, html=True), name="public")
