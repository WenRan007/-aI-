"""Small, dependency-free client for the Changke team account API.

The desktop application intentionally keeps the session only in memory.  A
copy of the executable therefore never contains a phone number, cookie, or
translation-provider key.  The server remains the source of truth for access
on every translation request.
"""

from __future__ import annotations

import json
import random
import re
import threading
import urllib.error
import urllib.request
from email.message import Message
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


BASE_URL = "https://jhssydzswyxgs.top"
SESSION_COOKIE = "changke_session"

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class AccountError(RuntimeError):
    """Base class for expected account API failures."""

    def __init__(self, message: str, status: Optional[int] = None):
        super().__init__(message)
        self.status = status


class AuthRequired(AccountError):
    """No valid session remains (or the server revoked it)."""


class AccessDenied(AccountError):
    """The signed-in account does not have the requested permission."""


class NetworkError(AccountError):
    """The HTTPS request could not be completed."""


def _valid_phone(phone: str) -> str:
    value = re.sub(r"\s+", "", str(phone or ""))
    if not re.fullmatch(r"1\d{10}", value):
        raise AccountError("请输入有效的手机号", 400)
    return value


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _cookie_from_headers(headers: Any) -> Optional[str]:
    """Extract only our session cookie from a Set-Cookie header."""
    values: List[str] = []
    if hasattr(headers, "get_all"):
        values = list(headers.get_all("Set-Cookie") or [])
    if not values:
        value = headers.get("Set-Cookie") if headers is not None else None
        if value:
            values = [value]
    pattern = re.compile(r"(?:^|;)\s*" + re.escape(SESSION_COOKIE) + r"=([^;]*)", re.I)
    for header in values:
        match = pattern.search(header)
        if match:
            token = match.group(1).strip()
            if token:
                return token
    return None


class DesktopAccountClient:
    """Thread-safe authenticated client used by the native Windows UI.

    ``opener_factory`` is a private test seam; production callers should use
    the default HTTPS opener.  No credentials are written to disk.
    """

    def __init__(
        self,
        timeout: float = 25.0,
        translation_timeout: float = 180.0,
        opener_factory: Optional[Callable[[], Any]] = None,
    ) -> None:
        self.timeout = float(timeout)
        self.translation_timeout = float(translation_timeout)
        self._opener_factory = opener_factory or (lambda: urllib.request.build_opener(_NoRedirect()))
        self._lock = threading.RLock()
        self._cookie: Optional[str] = None
        self._generation = 0
        self._user: Optional[Dict[str, Any]] = None

    @property
    def is_authenticated(self) -> bool:
        with self._lock:
            return bool(self._cookie)

    @property
    def user(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            return dict(self._user) if self._user else None

    def _state(self, required: bool = True) -> Tuple[Optional[str], int]:
        with self._lock:
            cookie, generation = self._cookie, self._generation
        if required and not cookie:
            raise AuthRequired("请先登录")
        return cookie, generation

    def _assert_generation(self, generation: int) -> None:
        with self._lock:
            if generation != self._generation:
                raise AuthRequired("账号状态已变化，请重新登录")

    def _request(
        self,
        path: str,
        method: str = "GET",
        body: Optional[bytes] = None,
        content_type: str = "application/json",
        authenticated: bool = True,
        cookie_override: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> Tuple[Any, Any, int]:
        cookie, generation = self._state(authenticated)
        if cookie_override is not None:
            cookie = cookie_override
        headers = {"Accept": "application/json", "User-Agent": "ChangkeAI-Windows/1.0"}
        if body is not None:
            headers["Content-Type"] = content_type
        if cookie:
            headers["Cookie"] = f"{SESSION_COOKIE}={cookie}"
        request = urllib.request.Request(BASE_URL + path, data=body, headers=headers, method=method)
        try:
            response = self._opener_factory().open(request, timeout=timeout or self.timeout)
            try:
                raw = response.read(8 * 1024 * 1024 + 1)
                headers_obj = response.headers
                status = getattr(response, "status", 200)
            finally:
                try:
                    response.close()
                except Exception:
                    pass
        except urllib.error.HTTPError as error:
            status = int(getattr(error, "code", 0) or 0)
            try:
                raw = error.read(2 * 1024 * 1024)
            except Exception:
                raw = b""
            headers_obj = getattr(error, "headers", Message())
            if status == 401:
                with self._lock:
                    if generation == self._generation:
                        self._cookie = None
                        self._user = None
                        self._generation += 1
                raise AuthRequired("登录已失效，请重新登录", status)
            if status == 403:
                raise AccessDenied(self._error_message(raw, "当前账号没有此权限"), status)
            raise AccountError(self._error_message(raw, "服务器暂时无法处理请求"), status)
        except (urllib.error.URLError, TimeoutError, OSError):
            self._assert_generation(generation)
            raise NetworkError("网络连接失败，请检查网络后重试")
        self._assert_generation(generation)
        if len(raw) > 8 * 1024 * 1024:
            raise AccountError("服务器返回内容过大", status)
        try:
            payload = json.loads(raw.decode("utf-8")) if raw else {}
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise AccountError("服务器返回格式无效", status)
        if not isinstance(payload, dict):
            raise AccountError("服务器返回格式无效", status)
        if status >= 400 or payload.get("error"):
            if status == 401:
                raise AuthRequired(str(payload.get("error") or "登录已失效，请重新登录"), status)
            if status == 403:
                raise AccessDenied(str(payload.get("error") or "当前账号没有此权限"), status)
            raise AccountError(str(payload.get("error") or "服务器暂时无法处理请求"), status)
        return payload, headers_obj, generation

    @staticmethod
    def _error_message(raw: bytes, fallback: str) -> str:
        try:
            data = json.loads(raw.decode("utf-8"))
            value = data.get("error") if isinstance(data, dict) else None
            if isinstance(value, str) and value.strip():
                return value.strip()[:240]
        except Exception:
            pass
        return fallback

    def send_code(self, phone: str, mode: str = "login") -> Dict[str, Any]:
        phone = _valid_phone(phone)
        if mode not in ("login", "register"):
            raise AccountError("登录方式不正确", 400)
        payload, _, _ = self._request("/api/auth/send-code", "POST", _json_bytes({"phone": phone, "mode": mode}), authenticated=False)
        return payload

    def verify_code(self, phone: str, code: str, mode: str = "login") -> Dict[str, Any]:
        phone = _valid_phone(phone)
        code = str(code or "").strip()
        if not re.fullmatch(r"\d{4,6}", code):
            raise AccountError("验证码格式不正确", 400)
        if mode not in ("login", "register"):
            raise AccountError("登录方式不正确", 400)
        payload, headers, generation = self._request(
            "/api/auth/verify-code", "POST",
            _json_bytes({"phone": phone, "code": code, "mode": mode, "deviceLabel": "常客AI Windows"}),
            authenticated=False,
        )
        cookie = _cookie_from_headers(headers)
        if not cookie:
            raise AuthRequired("登录服务未返回有效会话，请重试")
        self._assert_generation(generation)
        user = payload.get("user")
        with self._lock:
            self._cookie = cookie
            self._user = dict(user) if isinstance(user, dict) else {}
            self._generation += 1
        return dict(self._user)

    def get_access(self) -> Dict[str, Any]:
        """Return profile plus ``canTranslate``; probe translate permission.

        The current web API exposes permissions in the owner-only team route,
        so a zero-segment translation probe is used for members.  It never
        calls DeepL, but still exercises the same server-side permission gate.
        """
        payload, _, generation = self._request("/api/auth/me")
        user = payload.get("user")
        if not isinstance(user, dict):
            with self._lock:
                if generation == self._generation:
                    self._cookie = None
                    self._user = None
                    self._generation += 1
            raise AuthRequired("请先登录")
        if "canTranslate" in user:
            can_translate = bool(user.get("canTranslate"))
        else:
            can_translate = str(user.get("role", "")) == "owner"
            packed = self._multipart({"local_segments": "[]", "local_source_language": ""})
            boundary, body = packed.split(b"\0", 1)
            try:
                self._request("/api/translate", "POST", body, f"multipart/form-data; boundary={boundary.decode('ascii')}", timeout=self.timeout)
                can_translate = True
            except AccessDenied:
                can_translate = False
        with self._lock:
            self._assert_generation(generation)
            self._user = dict(user)
            result = dict(user); result["canTranslate"] = can_translate
            return result

    @staticmethod
    def _multipart(fields: Mapping[str, str]) -> bytes:
        boundary = "----ChangkeAI%08x" % random.getrandbits(32)
        chunks: List[bytes] = []
        for name, value in fields.items():
            chunks.extend([
                ("--" + boundary + "\r\n").encode(),
                (f'Content-Disposition: form-data; name="{name}"\r\n\r\n').encode(),
                str(value).encode("utf-8"), b"\r\n",
            ])
        chunks.append(("--" + boundary + "--\r\n").encode())
        # Caller needs the boundary.  Prefixing it lets _translation_body split
        # without exposing another public implementation detail.
        return boundary.encode("ascii") + b"\0" + b"".join(chunks)

    def translate_segments(self, segments: Sequence[Mapping[str, Any]], source_language: str = "") -> List[Dict[str, Any]]:
        if not isinstance(segments, Sequence) or isinstance(segments, (str, bytes)):
            raise AccountError("识别结果格式无效")
        normalized: List[Dict[str, Any]] = []
        for item in segments:
            if not isinstance(item, Mapping) or not str(item.get("text", "")).strip():
                raise AccountError("识别结果包含空文案")
            try:
                start, end = float(item.get("start", 0) or 0), float(item.get("end", item.get("start", 0)) or 0)
            except (TypeError, ValueError):
                raise AccountError("识别时间格式无效")
            normalized.append({"start": start, "end": end, "text": str(item["text"]).strip()})
        if not normalized:
            return []
        packed = self._multipart({"local_segments": json.dumps(normalized, ensure_ascii=False, separators=(",", ":")), "local_source_language": str(source_language or "")})
        boundary, body = packed.split(b"\0", 1)
        payload, _, _ = self._request("/api/translate", "POST", body, f"multipart/form-data; boundary={boundary.decode('ascii')}", timeout=self.translation_timeout)
        result = payload.get("segments")
        if not isinstance(result, list) or len(result) != len(normalized):
            raise AccountError("服务器未返回完整译文")
        translated: List[Dict[str, Any]] = []
        for original, item in zip(normalized, result):
            if not isinstance(item, Mapping) or not str(item.get("translated", "")).strip():
                raise AccountError("服务器返回了空译文")
            translated.append({"start": original["start"], "end": original["end"], "source": original["text"], "translated": str(item["translated"]).strip()})
        return translated

    def update_profile(self, display_name: str) -> Dict[str, Any]:
        name = str(display_name or "").strip()
        if not name:
            raise AccountError("用户名不能为空", 400)
        payload, _, _ = self._request("/api/profile", "POST", _json_bytes({"displayName": name[:40]}))
        with self._lock:
            if self._user is not None:
                self._user["displayName"] = payload.get("displayName", name)
        return payload

    def list_members(self) -> List[Dict[str, Any]]:
        payload, _, _ = self._request("/api/team")
        members = payload.get("members")
        if not isinstance(members, list):
            raise AccountError("团队成员数据格式无效")
        return [dict(item) for item in members if isinstance(item, Mapping)]

    def update_member(self, user_id: str, payload: Mapping[str, Any]) -> Dict[str, Any]:
        if not str(user_id or "").strip() or not isinstance(payload, Mapping):
            raise AccountError("成员参数不正确", 400)
        body = dict(payload)
        body["userId"] = str(user_id)
        result, _, _ = self._request("/api/team", "POST", _json_bytes(body))
        return result

    def logout(self) -> None:
        with self._lock:
            old_cookie = self._cookie
            self._cookie = None
            self._user = None
            self._generation += 1
        if old_cookie:
            # Best effort only. Local logout is already complete; this request
            # cannot clear a newer login because it uses the old cookie.
            def notify_logout():
                try:
                    self._request("/api/auth/logout", "POST", _json_bytes({}), authenticated=False, cookie_override=old_cookie)
                except AccountError:
                    pass
            threading.Thread(target=notify_logout, daemon=True).start()
