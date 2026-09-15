"""Explicit, private POSIX storage for this component's OAuth credentials.

No global default, imports from other stores, or token inspection at import time.
Only token records written by this component and bound to a client are accepted.
"""

from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
import json
import math
import os
from pathlib import Path
import secrets
import stat

FORMAT = "poe-trade-mcp.oauth.v1"
SCOPE = "account:characters"
PUBLIC_REFRESH_LIFETIME = 7 * 24 * 3600


class StoreError(ValueError):
    pass


def _positive_number(value):
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(value)
        and value > 0
    )


def _secret(value):
    return (
        isinstance(value, str)
        and 0 < len(value) <= 8192
        and all(32 < ord(c) < 127 for c in value)
    )


@dataclass(frozen=True)
class TokenRecord:
    client_id: str
    access_token: str = field(repr=False)
    expires_at: float
    scope: str = SCOPE
    token_type: str = "bearer"
    refresh_token: str | None = field(default=None, repr=False)
    refresh_expires_at: float | None = None
    reauthorization_required: bool = False

    @classmethod
    def from_response(cls, data, *, client_id, now, previous=None):
        if not isinstance(data, dict) or not _secret(data.get("access_token")):
            raise StoreError("OAuth response has an invalid access token")
        if str(data.get("token_type", "")).lower() != "bearer":
            raise StoreError("OAuth response must use bearer token_type")
        if not isinstance(data.get("scope"), str) or set(data["scope"].split()) != {
            SCOPE
        }:
            raise StoreError("OAuth response must grant exactly account:characters")
        if not _positive_number(data.get("expires_in")):
            raise StoreError("OAuth response has invalid expires_in")
        refresh = data.get("refresh_token")
        if refresh is not None and not _secret(refresh):
            raise StoreError("OAuth response has an invalid refresh token")
        deadline = (
            (previous.refresh_expires_at if previous else now + PUBLIC_REFRESH_LIFETIME)
            if refresh
            else None
        )
        record = cls(
            client_id=client_id,
            access_token=data["access_token"],
            expires_at=now + data["expires_in"],
            refresh_token=refresh,
            refresh_expires_at=deadline,
        )
        record.validate()
        return record

    def validate(self):
        if not _secret(self.access_token) or not _positive_number(self.expires_at):
            raise StoreError("Owned OAuth record has invalid token/expiry metadata")
        if (
            self.scope != SCOPE
            or self.token_type != "bearer"
            or not isinstance(self.reauthorization_required, bool)
        ):
            raise StoreError("Owned OAuth record has invalid scope/type/state")
        if self.refresh_token is not None:
            if not _secret(self.refresh_token) or not _positive_number(
                self.refresh_expires_at
            ):
                raise StoreError("Owned OAuth record has invalid refresh metadata")
        elif self.refresh_expires_at is not None:
            raise StoreError("Owned OAuth record has inconsistent refresh metadata")


class CredentialStore:
    def __init__(self, path, client_id):
        if os.name != "posix" or not hasattr(os, "O_NOFOLLOW"):
            raise StoreError(
                "OAuth file storage requires POSIX permissions (use WSL); native Windows ACL storage is not implemented"
            )
        self.path = Path(path)
        if (
            not self.path.is_absolute()
            or ".." in self.path.parts
            or self.path.name != "poe2-oauth.json"
        ):
            raise StoreError(
                "POE_OAUTH_TOKEN_FILE must be an absolute dedicated path ending in poe2-oauth.json"
            )
        self.client_id = client_id

    def _parent(self, create=False):
        current = Path(self.path.anchor)
        for part in self.path.parent.parts[1:]:
            current = current / part
            try:
                info = current.lstat()
            except FileNotFoundError:
                if not create:
                    return False
                try:
                    current.mkdir(mode=0o700)
                except FileExistsError:
                    pass
                info = current.lstat()
            if not stat.S_ISDIR(info.st_mode):
                raise StoreError(
                    "OAuth store directory must not traverse symlinks or non-directories"
                )
            if info.st_uid not in (0, os.getuid()) or (
                stat.S_IMODE(info.st_mode) & 0o022 and not info.st_mode & stat.S_ISVTX
            ):
                raise StoreError(
                    "OAuth store ancestor must not be replaceable by another user"
                )
        info = self.path.parent.stat()
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
            raise StoreError(
                "OAuth store parent must be owned by this user with mode 0700"
            )
        return True

    @staticmethod
    def _file_info(fd):
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) & 0o077
            or info.st_nlink != 1
        ):
            raise StoreError(
                "OAuth credential/lock file must be a private, single-link, user-owned regular file (0600)"
            )
        return info

    def load(self):
        if not self._parent():
            return None
        try:
            fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            return None
        except OSError:
            raise StoreError("Cannot open owned OAuth store safely") from None
        try:
            info = self._file_info(fd)
            if info.st_size > 65536:
                raise StoreError("Owned OAuth record is oversized")
            raw = os.read(fd, 65537)
        finally:
            os.close(fd)
        try:
            data = json.loads(raw)
        except (ValueError, UnicodeError):
            raise StoreError("Owned OAuth store contains invalid JSON") from None
        if (
            not isinstance(data, dict)
            or data.get("format") != FORMAT
            or data.get("realm") != "poe2"
        ):
            raise StoreError("File is not an owned PoE2 OAuth store")
        if data.get("client_id") != self.client_id:
            raise StoreError("Owned OAuth store belongs to a different client")
        try:
            record = TokenRecord(
                **{k: v for k, v in data.items() if k not in ("format", "realm")}
            )
            record.validate()
        except (TypeError, StoreError):
            raise StoreError("Owned OAuth store has invalid token metadata") from None
        return record

    def save(self, record):
        record.validate()
        if record.client_id != self.client_id:
            raise StoreError("Refusing to save credentials for a different client")
        self._parent(create=True)
        # Refuse an existing foreign/insecure/corrupt file instead of replacing it.
        self.load()
        staging = self.path.with_name(".poe2-oauth-" + secrets.token_hex(16))
        fd = os.open(
            staging, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(
                    {"format": FORMAT, "realm": "poe2", **asdict(record)},
                    stream,
                    allow_nan=False,
                )
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(staging, self.path)
            directory = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except OSError:
            raise StoreError(
                "Could not persist owned OAuth credentials; reauthorization may be required"
            ) from None
        finally:
            staging.unlink(missing_ok=True)

    @contextmanager
    def lock(self):
        """Prevent concurrent processes from redeeming the same refresh token."""
        import fcntl

        self._parent(create=True)
        try:
            fd = os.open(
                self.path.with_suffix(".lock"),
                os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK,
                0o600,
            )
        except OSError:
            raise StoreError("Cannot open private OAuth lock safely") from None
        try:
            self._file_info(fd)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise StoreError(
                    "Another OAuth operation owns this store; retry later"
                ) from None
            yield
        finally:
            os.close(fd)
