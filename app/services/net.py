"""The one place the agent downloads an image it was handed a URL for.

Room photos arrive from anonymous chat visitors, and tool arguments are chosen
by the model (so, indirectly, by whoever is talking to it). None of these URLs
can be trusted, so every image fetch — chat attachments, room analysis,
visualisations, the search indexer — goes through fetch_image(), which only
ever does one of three things:

* "/uploads/..." paths, and URLs on the backend's internal origin with an
  /uploads/ path, are marketplace media: fetched from BACKEND_URL's origin, or,
  when MEDIA_PUBLIC_BASE_URL is set (storage on R2), from that public media host
  as "<MEDIA_PUBLIC_BASE_URL>/<key>" (the backend would only redirect there, and
  redirects are never followed). That host is then fetched like any other public
  URL: every DNS address checked, connection pinned.
* URLs under PUBLIC_BASE_URL/static/ are the agent's own files: read from
  ./static, and only if the resolved path stays inside it.
* Anything else must be http(s) to a host whose every DNS address is public.
  The connection goes to the address that was checked (so DNS can't be
  switched to an internal one in between), redirects are not followed, and
  the download is capped in size and time.
"""
from __future__ import annotations

import ipaddress
import mimetypes
import os
import re
import socket
import time
from dataclasses import dataclass
from urllib.parse import SplitResult, unquote, urlsplit

import httpx

from app.config import (
    BACKEND_URL,
    IMAGE_FETCH_MAX_BYTES,
    IMAGE_FETCH_TIMEOUT_SECONDS,
    MEDIA_PUBLIC_BASE_URL,
    PUBLIC_BASE_URL,
)

STATIC_DIR = "static"

# What the backend's StorageService hands out for local uploads.
_MEDIA_PATH = re.compile(r"/uploads(/[A-Za-z0-9_.\-]+)+", re.ASCII)
_DEFAULT_PORTS = {"http": 80, "https": 443}


class UnsafeImageURL(ValueError):
    """The URL points somewhere the agent must not fetch from."""


@dataclass(frozen=True)
class FetchedImage:
    content: bytes
    content_type: str


def fetch_image(url: str) -> FetchedImage:
    """Download (or read) the image at `url`, refusing anything unsafe."""
    if not isinstance(url, str) or not url.strip():
        raise UnsafeImageURL("no image URL given")
    url = url.strip()

    if url.startswith("/"):
        if not _is_media_path(url):
            raise UnsafeImageURL("only /uploads/ paths can be given without a host")
        return _fetch_media(url)

    parts = urlsplit(url)
    if parts.scheme not in _DEFAULT_PORTS:
        raise UnsafeImageURL("only http and https image URLs are allowed")
    if parts.username or parts.password or not parts.hostname:
        raise UnsafeImageURL("malformed image URL")

    origin = _origin_key(parts)
    if origin == _origin_key(urlsplit(PUBLIC_BASE_URL)) and parts.path.startswith("/static/"):
        return _read_static(parts.path[len("/static/"):])
    if origin == _origin_key(urlsplit(_backend_origin())):
        # The internal backend is trusted for its public media and nothing else.
        if not _is_media_path(parts.path) or parts.query or parts.fragment:
            raise UnsafeImageURL("only /uploads/ media may be fetched from the backend")
        return _fetch_media(parts.path)
    return _download_public(parts)


# ── helpers ─────────────────────────────────────────────────────────────────

def _backend_origin() -> str:
    return BACKEND_URL.rstrip("/").removesuffix("/v1")


def media_public_url(path: str) -> str | None:
    """"/uploads/<key>" -> "<MEDIA_PUBLIC_BASE_URL>/<key>", the same mapping as the
    backend's /uploads redirect (StorageService.publicUrl is "<base>/<key>" and
    the local URL is "/uploads/<key>"). None when no media host is configured or
    the path is not a clean, public media path."""
    if not MEDIA_PUBLIC_BASE_URL or not _is_media_path(path):
        return None
    key = path[len("/uploads/"):]
    if any(seg.startswith(".") for seg in key.split("/")):
        return None  # hidden folders (.incoming) are private, never public media
    return f"{MEDIA_PUBLIC_BASE_URL}/{key}"


def _fetch_media(path: str) -> FetchedImage:
    """Fetch marketplace media given its "/uploads/<key>" path."""
    public = media_public_url(path)
    if public is not None:
        return _download_public(urlsplit(public))
    if MEDIA_PUBLIC_BASE_URL:
        raise UnsafeImageURL("not a public media path")
    return _download(f"{_backend_origin()}{path}")


def _origin_key(parts: SplitResult) -> tuple[str, str, int | None]:
    try:
        port = parts.port or _DEFAULT_PORTS.get(parts.scheme)
    except ValueError:  # a non-numeric port
        raise UnsafeImageURL("malformed image URL") from None
    return (parts.scheme, (parts.hostname or "").lower(), port)


def _is_media_path(path: str) -> bool:
    if not _MEDIA_PATH.fullmatch(path):
        return False
    return not any(seg in (".", "..") for seg in path.split("/"))


def _read_static(relative: str) -> FetchedImage:
    """Read a file the agent itself saved under ./static — never outside it."""
    base = os.path.realpath(STATIC_DIR)
    target = os.path.realpath(os.path.join(base, unquote(relative)))
    if os.path.commonpath([base, target]) != base or not os.path.isfile(target):
        raise UnsafeImageURL("not a file under static/")
    if os.path.getsize(target) > IMAGE_FETCH_MAX_BYTES:
        raise UnsafeImageURL("image is too large")
    with open(target, "rb") as f:
        content = f.read()
    return FetchedImage(content, mimetypes.guess_type(target)[0] or "application/octet-stream")


def _is_public(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        elif ip.sixtofour or ip.teredo or ip in ipaddress.IPv6Network("64:ff9b::/96"):
            return False  # tunnelled v4: could wrap any internal address
    return ip.is_global and not (
        ip.is_multicast or ip.is_reserved or ip.is_loopback or ip.is_link_local
        or ip.is_private or ip.is_unspecified
        or (isinstance(ip, ipaddress.IPv6Address) and ip.is_site_local)
    )


def _download_public(parts: SplitResult) -> FetchedImage:
    """GET an internet URL, refusing loopback, private, link-local, CGNAT and
    metadata addresses. Every address the name resolves to must be public, and
    the request is sent to the one we checked."""
    host = parts.hostname
    port = parts.port or _DEFAULT_PORTS[parts.scheme]
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError) as exc:
        raise UnsafeImageURL(f"cannot resolve {host}") from exc

    addresses = []
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if not _is_public(ip):
            raise UnsafeImageURL(f"{host} is not a public address")
        addresses.append(ip)
    if not addresses:
        raise UnsafeImageURL(f"cannot resolve {host}")

    ip = addresses[0]
    pinned_host = f"[{ip}]" if ip.version == 6 else str(ip)
    pinned_url = parts._replace(netloc=f"{pinned_host}:{port}").geturl()
    host_header = host if parts.port is None else f"{host}:{parts.port}"
    extensions = {"sni_hostname": host} if parts.scheme == "https" else {}
    return _download(pinned_url, headers={"Host": host_header}, extensions=extensions)


def _download(url: str, headers: dict | None = None,
              extensions: dict | None = None) -> FetchedImage:
    """GET with no redirects, no proxy settings from the environment, and hard
    caps on size and total time."""
    deadline = time.monotonic() + IMAGE_FETCH_TIMEOUT_SECONDS
    with (
        httpx.Client(timeout=IMAGE_FETCH_TIMEOUT_SECONDS, follow_redirects=False,
                     trust_env=False) as client,
        client.stream("GET", url, headers=headers, extensions=extensions or {}) as resp,
    ):
        if resp.is_redirect:
            raise UnsafeImageURL("image URLs that redirect are not followed")
        resp.raise_for_status()
        declared = resp.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > IMAGE_FETCH_MAX_BYTES:
            raise UnsafeImageURL("image is too large")
        body = bytearray()
        for chunk in resp.iter_bytes():
            body.extend(chunk)
            if len(body) > IMAGE_FETCH_MAX_BYTES:
                raise UnsafeImageURL("image is too large")
            if time.monotonic() > deadline:
                raise UnsafeImageURL("image download took too long")
        content_type = resp.headers.get("content-type", "").split(";")[0].strip()
    return FetchedImage(bytes(body), content_type)
