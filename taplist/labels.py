"""Label image storage: downloads remote artwork and stores uploads locally."""

import logging
import os
import uuid

import requests

from .providers import USER_AGENT

log = logging.getLogger(__name__)

MAX_BYTES = 8 * 1024 * 1024
ALLOWED = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/svg+xml": ".svg",
}


class LabelStore:
    def __init__(self, directory):
        self.directory = directory
        os.makedirs(directory, exist_ok=True)

    def path(self, filename):
        return os.path.join(self.directory, filename)

    def _new_name(self, beer_id, ext):
        return f"{beer_id}-{uuid.uuid4().hex[:8]}{ext}"

    def save_upload(self, beer_id, stream, content_type):
        ext = ALLOWED.get((content_type or "").split(";")[0].strip().lower())
        if not ext:
            raise ValueError("unsupported image type")
        data = stream.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise ValueError("image too large (8 MB max)")
        if not data:
            raise ValueError("empty upload")
        name = self._new_name(beer_id, ext)
        with open(self.path(name), "wb") as fh:
            fh.write(data)
        return name

    def fetch(self, beer_id, url, timeout=10):
        """Download a remote label. Returns the local filename, or None on failure."""
        if not url or not url.lower().startswith(("http://", "https://")):
            return None
        try:
            with requests.get(url, timeout=timeout, stream=True, headers={"User-Agent": USER_AGENT}) as resp:
                resp.raise_for_status()
                ctype = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
                ext = ALLOWED.get(ctype)
                if not ext:
                    # Guess from the URL as a fallback for servers that send octet-stream.
                    lower = url.lower().split("?")[0]
                    for candidate in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
                        if lower.endswith(candidate):
                            ext = ".jpg" if candidate == ".jpeg" else candidate
                            break
                if not ext:
                    log.warning("label %s has unsupported type %r", url, ctype)
                    return None
                name = self._new_name(beer_id, ext)
                size = 0
                with open(self.path(name), "wb") as fh:
                    for chunk in resp.iter_content(64 * 1024):
                        size += len(chunk)
                        if size > MAX_BYTES:
                            fh.close()
                            os.remove(self.path(name))
                            log.warning("label %s exceeds size limit", url)
                            return None
                        fh.write(chunk)
                return name
        except Exception as exc:
            log.warning("could not fetch label %s: %s", url, exc)
            return None

    def remove(self, filename):
        if not filename:
            return
        try:
            os.remove(self.path(filename))
        except FileNotFoundError:
            pass
