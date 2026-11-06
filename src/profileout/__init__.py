"""Official synchronous Python client for the ProfileOut API."""

import json
import math
import re
import time
import uuid
from os import PathLike
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

__all__ = ["ProfileOut", "ProfileOutError"]


def _seconds(value: float, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a positive finite number.")
    return value


def _id_path(resource: str, identifier: str) -> str:
    if not isinstance(identifier, str) or not re.fullmatch(r"[a-f\d]{8}(?:-[a-f\d]{4}){3}-[a-f\d]{12}", identifier, re.I | re.ASCII):
        raise ValueError("A UUID request_id or session_id is required.")
    return f"/v1/{resource}/{identifier}"


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward API keys or replay a paid POST through a redirect.
        return None


class ProfileOutError(Exception):
    """HTTP/API protocol failure; processing failures remain in the response."""

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        code: str | None = None,
        request_id: str | None = None,
        body: Any = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.request_id = request_id
        self.body = body
        self.headers = headers or {}


class ProfileOut:
    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "https://api.profileout.com",
        timeout: float = 120,
    ) -> None:
        if not isinstance(api_key, str) or not re.fullmatch(r"[\x21-\x7e]+", api_key):
            raise ValueError("api_key must be a nonempty API key without whitespace.")
        if not isinstance(base_url, str) or re.search(r"[\x00-\x20\x7f\\]", base_url):
            raise ValueError("base_url must be a valid URL.")
        url = urlsplit(base_url)
        local = url.hostname in {"localhost", "127.0.0.1", "::1"}
        if (
            not url.hostname
            or (url.scheme != "https" and not (url.scheme == "http" and local))
            or url.username is not None or url.password is not None or url.query or url.fragment
        ):
            raise ValueError("base_url must use HTTPS (HTTP is allowed on localhost) without credentials, query, or fragment.")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout = _seconds(timeout, "timeout")
        self._opener = build_opener(_NoRedirect())

    def _request(
        self,
        method: str,
        path: str,
        data: dict[str, Any] | bytes | None = None,
        *,
        content_type: str | None = None,
        timeout: float | None = None,
        binary: bool = False,
    ) -> Any:
        headers = {"Accept": "image/*" if binary else "application/json"}
        if path != "/health":
            headers["Authorization"] = f"Bearer {self._api_key}"
        if data is not None and not isinstance(data, bytes):
            data = json.dumps(data, ensure_ascii=False, allow_nan=False).encode("utf-8")
            content_type = "application/json"
        if content_type:
            headers["Content-Type"] = content_type
        request = Request(self._base_url + path, data=data, headers=headers, method=method)
        try:
            response = self._opener.open(request, timeout=self._timeout if timeout is None else timeout)
        except HTTPError as error:
            response = error
        with response:
            status = response.status
            response_headers = {key.lower(): value for key, value in response.headers.items()}
            raw = response.read()
        if 200 <= status < 300 and binary:
            return raw
        try:
            body = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            body = raw.decode("utf-8", errors="replace")
        details = dict(status=status, request_id=response_headers.get("x-request-id"), body=body, headers=response_headers)
        if not 200 <= status < 300:
            issue = body.get("error") if isinstance(body, dict) else None
            issue = issue if isinstance(issue, dict) else {}
            message = issue.get("message")
            code = issue.get("code")
            raise ProfileOutError(
                message if isinstance(message, str) else f"ProfileOut returned HTTP {status}.",
                code=code if isinstance(code, str) else None,
                **details,
            )
        if not isinstance(body, dict):
            raise ProfileOutError("ProfileOut returned an invalid JSON object.", code="invalid_response", **details)
        return body

    def _wait(self, path: str, timeout: float) -> dict[str, Any]:
        deadline = time.monotonic() + _seconds(timeout, "timeout")
        delay = 5.0
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Timed out waiting for ProfileOut; server-side processing may continue.")
            time.sleep(min(delay, remaining))
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Timed out waiting for ProfileOut; server-side processing may continue.")
            data = self._request("GET", path, timeout=min(self._timeout, remaining))
            if time.monotonic() >= deadline:
                raise TimeoutError("Timed out waiting for ProfileOut; server-side processing may continue.")
            if data.get("status") in ("completed", "failed"):
                return data
            if data.get("status") not in ("queued", "pending", "processing"):
                raise ProfileOutError("ProfileOut returned an unknown processing status.", code="invalid_response", body=data)
            hint = data.get("next_poll_after_seconds")
            delay = max(5.0, hint) if isinstance(hint, (int, float)) and math.isfinite(hint) and hint > 0 else 5.0

    def create_people_data(self, input: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/v1/people-data", input)

    def get_people_data(self, request_id: str) -> dict[str, Any]:
        return self._request("GET", _id_path("people-data", request_id))

    def get_people_data_image(self, request_id: str) -> bytes:
        return self._request("GET", _id_path("people-data", request_id) + "/image", binary=True)

    def wait_for_people_data(self, request_id: str, *, timeout: float = 1200) -> dict[str, Any]:
        return self._wait(_id_path("people-data", request_id), timeout)

    def create_profile_parsing(self, file: str | PathLike[str]) -> dict[str, Any]:
        path = Path(file)
        filename = path.name
        media_types = {
            ".pdf": "application/pdf", ".png": "image/png",
            ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".tif": "image/tiff", ".tiff": "image/tiff",
        }
        if (
            len(filename.encode("utf-16-le")) // 2 > 255
            or re.search(r"[\x00-\x1f\x7f\\]", filename)
            or path.suffix.lower() not in media_types
        ):
            raise ValueError("file must have a PDF, PNG, JPEG, or TIFF filename of at most 255 characters.")
        with path.open("rb") as source:
            content = source.read(10 * 1024 * 1024 + 1)
        if not content or len(content) > 10 * 1024 * 1024:
            raise ValueError("file must be nonempty and no larger than 10 MiB.")
        boundary = uuid.uuid4().hex
        filename = filename.replace('"', "%22")
        body = (
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            f'Content-Type: {media_types[path.suffix.lower()]}\r\n\r\n'
        ).encode("utf-8") + content + f"\r\n--{boundary}--\r\n".encode("ascii")
        return self._request("POST", "/v1/profile-parsings", body, content_type=f"multipart/form-data; boundary={boundary}")

    def get_profile_parsing(self, request_id: str) -> dict[str, Any]:
        return self._request("GET", _id_path("profile-parsings", request_id))

    def wait_for_profile_parsing(self, request_id: str, *, timeout: float = 1200) -> dict[str, Any]:
        return self._wait(_id_path("profile-parsings", request_id), timeout)

    def create_contact_enrichment(self, input: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/v1/contact-enrichments", input)

    def get_contact_enrichment(self, request_id: str) -> dict[str, Any]:
        return self._request("GET", _id_path("contact-enrichments", request_id))

    def wait_for_contact_enrichment(self, request_id: str, *, timeout: float = 1200) -> dict[str, Any]:
        return self._wait(_id_path("contact-enrichments", request_id), timeout)

    def create_people_search(self, input: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/v1/people-searches", input)

    def continue_people_search(self, session_id: str, input: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", _id_path("people-searches", session_id), input)

    def get_people_search(self, session_id: str) -> dict[str, Any]:
        return self._request("GET", _id_path("people-searches", session_id))

    def wait_for_people_search(self, session_id: str, *, timeout: float = 1200) -> dict[str, Any]:
        return self._wait(_id_path("people-searches", session_id), timeout)

    def get_health(self) -> dict[str, Any]:
        return self._request("GET", "/health")
