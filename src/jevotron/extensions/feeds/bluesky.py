"""Bounded unauthenticated GETs; no links/media fetched and no source-provided hosts."""

import json
from datetime import datetime, timezone

import httpx

from .common import timestamp

BASE = "https://public.api.bsky.app/xrpc/"
METHODS = {"getAuthorFeed", "searchPosts", "getPosts"}


def post_uri(value: str) -> str:
    if not isinstance(value, str) or not value.startswith("at://did:"):
        raise ValueError("Expected a DID-based post URI")
    parts = value[5:].split("/")
    if len(parts) != 3 or parts[1] != "app.bsky.feed.post" or not parts[2]:
        raise ValueError("Expected a post URI")
    return value


def brief(post: dict) -> dict:
    record = post.get("record", post.get("value", {}))
    text = record.get("text")
    if not isinstance(text, str) or not isinstance(post.get("cid"), str):
        raise ValueError("Invalid text post view")
    timestamp(record["createdAt"])
    return {
        "id": post_uri(post["uri"]),
        "version": post["cid"],
        "author": post["author"]["did"],
        "text": text,
        "created_at": record["createdAt"],
    }


def references(post: dict) -> dict:
    record = post["record"]
    refs = {k: v for k, v in record.get("reply", {}).items() if k in {"parent", "root"}}
    embed = record.get("embed", {})
    if embed.get("$type") == "app.bsky.embed.recordWithMedia":
        embed = embed.get("record", {})
    if embed.get("$type") == "app.bsky.embed.record":
        ref = embed.get("record", {})
        # Quote embeds can point at non-post records; keep them as unavailable
        # context without handing them to the post hydrator.
        refs["quote"] = ref
    return refs


def inline_context(item: dict) -> dict:
    available = {}
    for value in item.get("reply", {}).values():
        if isinstance(value, dict) and "uri" in value:
            available[value["uri"]] = value
    embed = item["post"].get("embed", {})
    if embed.get("$type") == "app.bsky.embed.recordWithMedia#view":
        embed = embed.get("record", {})
    if embed.get("$type") == "app.bsky.embed.record#view":
        value = embed.get("record", {})
        if "uri" in value:
            available[value["uri"]] = value
    return available


def normalize(item: dict, observed_at: str, hydrated: dict) -> dict:
    post = item["post"]
    result = brief(post)
    available = inline_context(item) | hydrated
    context = {}
    for role, ref in references(post).items():
        uri = ref["uri"]
        view = available.get(uri, {})
        summary = {
            "id": uri,
            "expected_version": ref.get("cid"),
            "status": "not_fetched",
        }
        if view.get("blocked"):
            summary["status"] = "blocked"
        elif view.get("notFound") or view.get("not_found") or view.get("detached"):
            summary["status"] = "unavailable"
        elif view.get("cid"):
            if view["cid"] != ref.get("cid"):
                summary["status"] = "version_mismatch"
            else:
                try:
                    summary.update(brief(view), status="available")
                except (KeyError, ValueError):
                    summary["status"] = "unsupported"
        context[role] = summary
    embed = post["record"].get("embed", {})
    media = embed.get("media", embed)
    result.update(
        source="bluesky-public",
        status="active",
        observed_at=observed_at,
        handle=post["author"].get("handle"),
        context=context,
        media={
            "type": media.get("$type"),
            "alt": [image.get("alt", "") for image in media.get("images", [])],
            "external": {
                key: media.get("external", {}).get(key)
                for key in ("uri", "title", "description")
            }
            if "external" in media
            else None,
            "content_fetched": False,
        },
    )
    return result


class PublicClient:
    def __init__(self, transport=None):
        self.http = httpx.Client(
            timeout=20,
            follow_redirects=False,
            trust_env=False,
            transport=transport,
            headers={"User-Agent": "jevotron-local-preview/0.1"},
        )
        self.requests = 0

    def get(self, method: str, params) -> dict:
        if method not in METHODS:
            raise ValueError("Unsupported public method")
        self.requests += 1
        with self.http.stream(
            "GET", BASE + "app.bsky.feed." + method, params=params
        ) as response:
            if response.status_code != 200:
                raise RuntimeError(
                    f"Public Bluesky HTTP {response.status_code}; no checkpoint advanced"
                )
            content = bytearray()
            for chunk in response.iter_bytes(chunk_size=65_536):
                content.extend(chunk)
                if len(content) > 2_000_000:
                    raise ValueError("Public response exceeded 2 MB bound")
            return json.loads(content)

    def posts(self, uris: list[str]) -> dict:
        if not uris:
            return {}
        if len(uris) > 25:
            raise ValueError("Hydration is limited to 25 posts")
        requested = {post_uri(uri) for uri in uris}
        response = self.get("getPosts", [("uris", uri) for uri in sorted(requested)])
        posts = {
            post["uri"]: post for post in response["posts"] if post["uri"] in requested
        }
        return {
            uri: posts.get(uri, {"uri": uri, "not_found": True}) for uri in requested
        }

    def collect(
        self, *, actor=None, query=None, limit=10, pages=1, cursor=None
    ) -> tuple:
        if bool(actor) == bool(query) or not 1 <= limit <= 25 or not 1 <= pages <= 3:
            raise ValueError("Select one actor/query, limit 1–25, pages 1–3")
        method = "getAuthorFeed" if actor else "searchPosts"
        params = (
            {"actor": actor, "filter": "posts_with_replies", "includePins": "false"}
            if actor
            else {
                "q": query,
                "sort": "latest",
            }
        )
        params["limit"] = limit
        items = []
        visited = set()
        for _ in range(pages):
            if cursor:
                if cursor in visited:
                    raise ValueError("Server repeated a pagination cursor")
                visited.add(cursor)
                params["cursor"] = cursor
            data = self.get(method, params)
            page = data["feed"] if actor else [{"post": p} for p in data["posts"]]
            if len(page) > limit:
                raise ValueError("Server exceeded requested page size")
            items.extend(page)
            next_cursor = data.get("cursor")
            if next_cursor and next_cursor == cursor:
                raise ValueError("Server repeated a pagination cursor")
            cursor = next_cursor
            if not cursor or not page:
                break
        missing = set()
        for item in items:
            inline = inline_context(item)
            for ref in references(item["post"]).values():
                try:
                    uri = post_uri(ref["uri"])
                except ValueError:
                    continue
                if uri not in inline:
                    missing.add(uri)
        # One bounded context lookup, never recursive traversal.
        hydrated = self.posts(sorted(missing)[:25])
        now = datetime.now(timezone.utc).isoformat()
        records = {
            item["post"]["uri"]: normalize(item, now, hydrated) for item in items
        }
        return list(records.values()), cursor

    def close(self):
        self.http.close()


def reconcile(records: list[dict], views: dict, now: str) -> list[dict]:
    """An explicit getPosts miss suppresses content; absence from a page never does."""
    result = []
    for record in records:
        uri = record["id"]
        if uri in views:
            if views[uri].get("not_found"):
                # Do not claim this proves deletion: moderation/indexing may hide it.
                record = {
                    "id": uri,
                    "source": "bluesky-public",
                    "status": "unavailable",
                    "observed_at": now,
                }
            else:
                record = normalize({"post": views[uri]}, now, views)
        else:
            record = dict(record)
            record["context"] = {
                k: dict(v) for k, v in record.get("context", {}).items()
            }
            for context in record["context"].values():
                if context["id"] in views:
                    view = views[context["id"]]
                    expected = context.get("expected_version")
                    context.clear()
                    context.update(
                        id=view["uri"], expected_version=expected, status="unavailable"
                    )
                    if view.get("cid") == expected:
                        context.update(brief(view), status="available")
                    elif view.get("cid"):
                        context["status"] = "version_mismatch"
        result.append(record)
    return result
