"""/sandbox routes: one static page shell plus a small JSON API.

Every route depends on ``_guard``: a 404 unless the sandbox is on, outside a
container, for a loopback peer. The page shell (harvey/web/sandbox/sandbox.html)
carries the DEMO banner server-side; sandbox.js renders all content through
escHtml under the app's CSP (no inline script or style).

Posting needs the sandbox CSRF token from ``GET /sandbox/api/session`` in the
``X-Sandbox-CSRF`` header (custom header + JSON body: a cross-site page can
neither read the token nor send the request without a CORS preflight).
"""

import secrets
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from pydantic import BaseModel, Field

from harvey import auth, sandbox
from harvey.sandbox import demo_content, urls
from harvey.sandbox.store import SandboxStore

WEB_DIR = Path(__file__).resolve().parent.parent / "web" / "sandbox"
ASSET_TYPES = {".css": "text/css", ".js": "text/javascript"}
CSRF_HEADER = "X-Sandbox-CSRF"
_CSRF = secrets.token_urlsafe(32)  # fresh per process
PAGE_SIZE = 50


def _guard(request: Request) -> None:
    if not sandbox.request_allowed(request):
        raise HTTPException(status_code=404, detail="Not Found")


router = APIRouter(prefix="/sandbox", dependencies=[Depends(_guard)], include_in_schema=False)


def get_store() -> SandboxStore:
    return SandboxStore(sandbox.db_path())


def _shell(status: int = 200) -> HTMLResponse:
    html = (WEB_DIR / "sandbox.html").read_text(encoding="utf-8").replace("{{BANNER}}", sandbox.BANNER)
    return HTMLResponse(html, status_code=status)


def _thread_or_404(store: SandboxStore, thread_id: int, kind: str, community: str = "") -> dict:
    thread = store.thread(thread_id)
    if thread is None or thread["kind"] != kind or (community and thread["community"] != community):
        raise HTTPException(status_code=404, detail="thread not found")
    return thread


def absolute_permalink(request: Request, thread: dict, comment_id: int | None = None) -> str:
    base = str(request.base_url).rstrip("/")
    return base + urls.permalink(thread["kind"], thread["community"], thread["id"], comment_id)


# --- Pages ----------------------------------------------------------------------


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def index_page():
    return _shell()


def _community_shell(store: SandboxStore, name: str, kind: str) -> HTMLResponse:
    info = store.community(name)
    return _shell(200 if info is not None and info["kind"] == kind else 404)


@router.get("/f/c/{community}", response_class=HTMLResponse)
def community_page(community: str, store: SandboxStore = Depends(get_store)):
    return _community_shell(store, community, "forum")


@router.get("/u/{account}", response_class=HTMLResponse)
def account_page(account: str, store: SandboxStore = Depends(get_store)):
    """A Demo Photos account's posts."""
    return _community_shell(store, account, "photo")


@router.get("/l/{listing}", response_class=HTMLResponse)
def listing_page(listing: str, store: SandboxStore = Depends(get_store)):
    """A Demo Reviews listing."""
    return _community_shell(store, listing, "review")


@router.get("/f/c/{community}/{thread_id}", response_class=HTMLResponse)
@router.get("/f/c/{community}/{thread_id}/comment/{comment_id}", response_class=HTMLResponse)
def forum_thread_page(community: str, thread_id: int, comment_id: int | None = None,
                      store: SandboxStore = Depends(get_store)):
    _thread_or_404(store, thread_id, "forum", community)
    return _shell()


@router.get("/p/{thread_id}", response_class=HTMLResponse)
@router.get("/p/{thread_id}/comment/{comment_id}", response_class=HTMLResponse)
def photo_page(thread_id: int, comment_id: int | None = None, store: SandboxStore = Depends(get_store)):
    _thread_or_404(store, thread_id, "photo")
    return _shell()


@router.get("/r/{thread_id}", response_class=HTMLResponse)
@router.get("/r/{thread_id}/comment/{comment_id}", response_class=HTMLResponse)
def review_page(thread_id: int, comment_id: int | None = None, store: SandboxStore = Depends(get_store)):
    _thread_or_404(store, thread_id, "review")
    return _shell()


@router.get("/guides", response_class=HTMLResponse)
@router.get("/guides/{slug}", response_class=HTMLResponse)
def guide_page(slug: str = ""):
    if slug and demo_content.guide(slug) is None:
        return _shell(404)
    return _shell()


@router.get("/assets/{name}")
def asset(name: str):
    target = (WEB_DIR / name).resolve()
    if target.parent != WEB_DIR.resolve() or target.suffix not in ASSET_TYPES or not target.is_file():
        return PlainTextResponse("not found", status_code=404)
    return PlainTextResponse(target.read_text(encoding="utf-8"), media_type=ASSET_TYPES[target.suffix],
                             headers={"Cache-Control": "no-store"})


# --- JSON API -------------------------------------------------------------------


@router.get("/api/session")
def session():
    return {"csrf": _CSRF, "brand": {"handle": sandbox.brand_handle(), "flair": sandbox.BRAND_FLAIR},
            "banner": sandbox.BANNER}


@router.get("/api/index")
def api_index(store: SandboxStore = Depends(get_store)):
    return {"communities": store.communities(), "recent": store.threads(limit=12)}


@router.get("/api/communities/{community}")
def api_community(community: str, offset: int = Query(0, ge=0, le=100000),
                  store: SandboxStore = Depends(get_store)):
    info = store.community(community)
    if info is None:
        raise HTTPException(status_code=404, detail="community not found")
    return {"community": info, "threads": store.threads(community, limit=PAGE_SIZE, offset=offset),
            "total": store.count_threads(community), "offset": offset, "limit": PAGE_SIZE}


@router.get("/api/threads/{thread_id}")
def api_thread(thread_id: int, store: SandboxStore = Depends(get_store)):
    thread = store.thread(thread_id)
    if thread is None:
        raise HTTPException(status_code=404, detail="thread not found")
    return {"thread": thread, "community": store.community(thread["community"]),
            "comments": store.comments(thread_id),
            "permalink": urls.thread_path(thread["kind"], thread["community"], thread["id"])}


@router.get("/api/guides")
def api_guides():
    return {"guides": demo_content.guides()}


@router.get("/api/guides/{slug}")
def api_guide(slug: str):
    guide = demo_content.guide(slug)
    if guide is None:
        raise HTTPException(status_code=404, detail="guide not found")
    return guide


class CommentBody(BaseModel):
    thread_id: int
    parent_id: int | None = None
    body: str = Field(max_length=sandbox.MAX_BODY_CHARS)


def post_brand_comment(store: SandboxStore, thread_id: int, parent_id: int | None, body: str) -> tuple[dict, int]:
    """Add a comment as the brand account; returns (thread, comment id).

    Raises HTTPException 400/404 for an empty body, a missing thread or a
    parent from another thread.
    """
    body = (body or "").strip()
    if not body:
        raise HTTPException(status_code=400, detail="the comment is empty")
    if len(body) > sandbox.MAX_BODY_CHARS:
        raise HTTPException(status_code=400, detail=f"at most {sandbox.MAX_BODY_CHARS} characters")
    thread = store.thread(thread_id)
    if thread is None:
        raise HTTPException(status_code=404, detail="thread not found")
    if parent_id is not None:
        parent = store.comment(parent_id)
        if parent is None or parent["thread_id"] != thread["id"]:
            raise HTTPException(status_code=400, detail="the parent comment is not in this thread")
    comment_id = store.add_comment(thread["id"], parent_id, sandbox.brand_handle(), body, None, 1,
                                   is_brand=True, flair=sandbox.BRAND_FLAIR)
    return thread, comment_id


@router.post("/api/comments")
def api_post_comment(body: CommentBody, request: Request, store: SandboxStore = Depends(get_store)):
    if not auth.tokens_match(_CSRF, request.headers.get(CSRF_HEADER, "")):
        raise HTTPException(status_code=403, detail="missing or invalid sandbox CSRF token")
    thread, comment_id = post_brand_comment(store, body.thread_id, body.parent_id, body.body)
    return {"id": comment_id, "permalink": absolute_permalink(request, thread, comment_id),
            "comment": store.comment(comment_id)}
