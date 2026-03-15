"""Docker Hub registry client for searching images and fetching tags."""

from __future__ import annotations

from dataclasses import dataclass

import httpx

DOCKER_HUB_SEARCH_URL = "https://hub.docker.com/v2/search/repositories/"
DOCKER_HUB_TAGS_URL = "https://hub.docker.com/v2/repositories/{repo}/tags/"


@dataclass
class RegistryImage:
    name: str
    description: str
    star_count: int
    pull_count: int
    is_official: bool
    is_automated: bool


def search_images(query: str, page_size: int = 25, page: int = 1) -> list[RegistryImage]:
    """Search Docker Hub for images matching *query*.

    Returns a list of :class:`RegistryImage` results, or an empty list on
    network/API failure.
    """
    params: dict[str, str | int] = {
        "query": query,
        "page_size": page_size,
        "page": page,
    }
    try:
        resp = httpx.get(DOCKER_HUB_SEARCH_URL, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        return [
            RegistryImage(
                name=item.get("repo_name", ""),
                description=item.get("short_description", ""),
                star_count=item.get("star_count", 0),
                pull_count=item.get("pull_count", 0),
                is_official=item.get("is_official", False),
                is_automated=item.get("is_automated", False),
            )
            for item in data.get("results", [])
        ]
    except Exception:
        return []


def fetch_tags(image_name: str, page_size: int = 20) -> list[str]:
    """Fetch the most recent tags for a Docker Hub image.

    For official images (no slash), the repo path is ``library/<name>``.
    Returns ``["latest"]`` on failure.
    """
    repo = image_name if "/" in image_name else f"library/{image_name}"
    url = DOCKER_HUB_TAGS_URL.format(repo=repo)
    try:
        resp = httpx.get(url, params={"page_size": page_size}, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        tags = [t["name"] for t in data.get("results", []) if t.get("name")]
        return tags or ["latest"]
    except Exception:
        return ["latest"]
