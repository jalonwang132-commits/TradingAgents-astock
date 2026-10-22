"""账户 / 邀请码 / 审计的磁盘存储。

设计约束（多用户部署的关键）：

1. **账户文件损坏时绝不当作空库**——否则 ``has_users()`` 返回 False，页面会走
   「创建 owner」引导，任何人都能重新接管。损坏一律抛 :class:`AuthError`。
2. 所有写操作走 ``临时文件 + os.replace``，并在持有进程内锁时完成 read-modify-write。
   本项目的部署形态是单 Streamlit 进程；多进程部署需要把锁换成文件锁/Redis（见
   MULTIUSER.md「已知限制」）。
3. 邀请码只存 SHA-256，不存明文；明文只在创建时返回一次。
4. 审计日志只记录事件与用户名，**绝不记录密码、API Key、邀请码明文**。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Mapping

from web.user_paths import tradingagents_home

from .passwords import (
    burn_unknown_user_hash,
    hash_password,
    normalize_display_name,
    normalize_username,
    validate_password,
    verify_password,
)

ACCOUNTS_VERSION = 1
AUTH_DIR_NAME = "auth"
ACCOUNTS_FILE = "accounts.json"
AUDIT_FILE = "audit.log"
AUDIT_MAX_BYTES = 5 * 1024 * 1024

_UID_RE = re.compile(r"^u_[0-9a-f]{12}$")
# 归一化后的邀请码是 12 位大写字母/数字（展示时按 4-4-4 分组）。
_INVITE_RE = re.compile(r"^[A-Z0-9]{12}$")
_INVITE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # 去掉 I/O/0/1，便于口述
_INVITE_NORMALIZE_RE = re.compile(r"[^A-Z0-9]")

ROLE_OWNER = "owner"
ROLE_MEMBER = "member"
STATUS_ACTIVE = "active"
STATUS_DISABLED = "disabled"

DEFAULT_MAX_USERS = 10
MAX_USERS_CAP = 500
DEFAULT_INVITE_TTL_HOURS = 72
MAX_INVITE_TTL_HOURS = 24 * 30
DEFAULT_INVITE_USES = 1
MAX_INVITE_USES = 50
MAX_ACTIVE_INVITES = 100

# Streamlit 每个 rerun 都会 new 一个 AuthStore；如果把锁放成实例字段，两个浏览器
# 会话同时注册/改密码时各拿各的锁，read-modify-write 会丢更新。锁必须是进程级的。
_PROCESS_LOCK = threading.RLock()


class AuthError(Exception):
    """认证/账户操作的业务错误。``code`` 供 UI 分支，``message`` 给用户看。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    def __str__(self) -> str:  # pragma: no cover - 直接 str() 时走这里
        return self.message


def _env_int(name: str, default: int, *, low: int, high: int) -> int:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(low, min(high, value))


def max_users_cap() -> int:
    """环境变量给的硬上限。管理员设置只能在此基础上调低。"""
    return _env_int("TRADINGAGENTS_MAX_USERS", DEFAULT_MAX_USERS, low=1, high=MAX_USERS_CAP)


def _now() -> float:
    return time.time()


def _iso(ts: float | None) -> str:
    if not ts:
        return ""
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


def public_user(user: Mapping[str, Any]) -> dict[str, Any]:
    """去掉密码哈希后的用户视图；UI / session 只能拿这个。"""
    return {
        "id": str(user.get("id", "")),
        "username": str(user.get("username", "")),
        "display_name": str(user.get("display_name") or user.get("username") or ""),
        "role": str(user.get("role", ROLE_MEMBER)),
        "status": str(user.get("status", STATUS_ACTIVE)),
        "created_at": user.get("created_at"),
        "last_login_at": user.get("last_login_at"),
        "login_count": int(user.get("login_count") or 0),
        "password_changed_at": user.get("password_changed_at"),
        "created_by": user.get("created_by"),
    }


def _public_invite(invite: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": str(invite.get("id", "")),
        "note": str(invite.get("note", "")),
        "created_by": invite.get("created_by"),
        "created_at": invite.get("created_at"),
        "expires_at": invite.get("expires_at"),
        "max_uses": int(invite.get("max_uses") or 0),
        "uses": int(invite.get("uses") or 0),
        "status": str(invite.get("status", "active")),
        "last_used_at": invite.get("last_used_at"),
        "redeemed_usernames": list(invite.get("redeemed_usernames") or []),
    }


def _generate_invite_code() -> str:
    groups = [
        "".join(secrets.choice(_INVITE_ALPHABET) for _ in range(4))
        for _ in range(3)
    ]
    return "-".join(groups)


def _normalize_invite_code(raw: str) -> str:
    return _INVITE_NORMALIZE_RE.sub("", str(raw or "").upper())


def _hash_invite(code: str) -> str:
    return hashlib.sha256(_normalize_invite_code(code).encode("utf-8")).hexdigest()


def _new_uid() -> str:
    return f"u_{secrets.token_hex(6)}"


def _new_invite_id() -> str:
    return f"inv_{secrets.token_hex(6)}"


class AuthStore:
    """账户库。一个实例绑定一个数据根目录；线程安全，可在 UI 里复用。"""

    def __init__(self, home: Path | str | None = None) -> None:
        base = Path(home).expanduser() if home is not None else tradingagents_home()
        self.home = base
        self.auth_dir = base / AUTH_DIR_NAME
        self.accounts_path = self.auth_dir / ACCOUNTS_FILE
        self.audit_path = self.auth_dir / AUDIT_FILE
        # 标记「这台机器已经初始化过账户」。accounts.json 被误删时，有标记就
        # 拒绝进入 bootstrap，避免任何人重建 owner 接管系统。
        self.initialized_marker = self.auth_dir / ".initialized"
        self._lock = _PROCESS_LOCK

    # ── 读写底座 ────────────────────────────────────────────────────────
    def _default_state(self) -> dict[str, Any]:
        return {
            "version": ACCOUNTS_VERSION,
            "created_at": _now(),
            "users": {},
            "invites": {},
            "settings": {
                "allow_registration": True,
                "max_users": max_users_cap(),
            },
        }

    def _validate_state(self, state: Any) -> dict[str, Any]:
        if not isinstance(state, dict) or state.get("version") != ACCOUNTS_VERSION:
            raise AuthError("corrupt", "账户文件版本无法识别，已停止自动初始化以防账户被重置。")
        users = state.get("users")
        if not isinstance(users, dict):
            raise AuthError("corrupt", "账户文件结构损坏，已停止自动初始化以防账户被重置。")
        for uid, user in users.items():
            if not _UID_RE.match(str(uid)) or not isinstance(user, dict):
                raise AuthError("corrupt", "账户文件包含非法用户记录。")
            if str(user.get("id")) != str(uid):
                raise AuthError("corrupt", "账户文件用户记录 ID 不一致。")
            if not str(user.get("username") or "").strip():
                raise AuthError("corrupt", "账户文件包含空用户名。")
            if not str(user.get("password_hash") or "").startswith("scrypt$"):
                raise AuthError("corrupt", "账户文件包含无效密码哈希。")
            if user.get("role") not in (ROLE_OWNER, ROLE_MEMBER):
                raise AuthError("corrupt", "账户文件包含非法角色。")
            if user.get("status") not in (STATUS_ACTIVE, STATUS_DISABLED):
                raise AuthError("corrupt", "账户文件包含非法状态。")
            try:
                user["login_count"] = max(0, int(user.get("login_count") or 0))
            except (TypeError, ValueError):
                user["login_count"] = 0
            if user.get("last_login_at") is not None:
                try:
                    user["last_login_at"] = float(user["last_login_at"])
                except (TypeError, ValueError):
                    user["last_login_at"] = None
            try:
                # 会话校验会把它转成 float 比较；坏值必须在读盘时归一化，
                # 否则每次 rerun 都会在认证门里抛 ValueError。
                user["password_changed_at"] = float(user.get("password_changed_at") or 0)
            except (TypeError, ValueError):
                user["password_changed_at"] = 0
            try:
                user["created_at"] = float(user.get("created_at") or 0)
            except (TypeError, ValueError):
                user["created_at"] = 0
        usernames = [str(u.get("username", "")).lower() for u in users.values()]
        if len(usernames) != len(set(usernames)):
            raise AuthError("corrupt", "账户文件包含重复用户名。")
        invites = state.get("invites")
        if invites is None:
            state["invites"] = {}
            invites = state["invites"]
        elif not isinstance(invites, dict):
            raise AuthError("corrupt", "账户文件包含非法邀请码结构。")
        for invite_id, invite in invites.items():
            if not isinstance(invite, dict) or str(invite.get("id")) != str(invite_id):
                raise AuthError("corrupt", "账户文件包含非法邀请码记录。")
            code_hash = invite.get("code_hash")
            if not isinstance(code_hash, str) or len(code_hash) != 64:
                raise AuthError("corrupt", "账户文件包含非法邀请码哈希。")
            if invite.get("status") not in ("active", "used", "revoked"):
                raise AuthError("corrupt", "账户文件包含非法邀请码状态。")
            try:
                invite["uses"] = max(0, int(invite.get("uses") or 0))
                invite["max_uses"] = max(1, int(invite.get("max_uses") or 1))
                invite["expires_at"] = float(invite.get("expires_at") or 0)
                invite["created_at"] = float(invite.get("created_at") or 0)
            except (TypeError, ValueError):
                raise AuthError("corrupt", "账户文件包含非法邀请码计数。") from None
        settings = state.get("settings")
        if not isinstance(settings, dict):
            state["settings"] = {"allow_registration": True, "max_users": max_users_cap()}
        settings = state["settings"]
        settings["allow_registration"] = bool(settings.get("allow_registration", True))
        try:
            settings["max_users"] = max(1, int(settings.get("max_users") or max_users_cap()))
        except (TypeError, ValueError):
            settings["max_users"] = max_users_cap()
        return state

    def _load(self) -> dict[str, Any]:
        if not self.accounts_path.exists():
            if self.initialized_marker.exists():
                raise AuthError(
                    "corrupt",
                    "账户文件缺失，但系统已初始化过。请从备份恢复 accounts.json，"
                    "不要删除后重建（否则任何人都能重新创建 owner）。",
                )
            return self._default_state()
        try:
            raw = json.loads(self.accounts_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AuthError("corrupt", f"账户文件读取失败（{exc}）。请从备份恢复，勿删除重建。")
        state = self._validate_state(raw)
        # 文件存在但 users 为空 + 已初始化标记 = 恢复/截断后的可疑状态。
        # 不能当成全新安装，否则会重新出现「创建 owner」入口。
        if self.initialized_marker.exists() and not state.get("users"):
            raise AuthError(
                "corrupt",
                "账户文件已初始化但没有任何用户。请从备份恢复，不要重新创建 owner。",
            )
        return state

    def _save(self, state: dict[str, Any]) -> None:
        self.auth_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.auth_dir, 0o700)
        except OSError:
            pass
        payload = json.dumps(state, ensure_ascii=False, indent=2)
        tmp = self.accounts_path.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, self.accounts_path)
        try:
            # 无条件写：只要发生过一次账户写盘，就认为这台机器已初始化。
            self.initialized_marker.write_text("1", encoding="utf-8")
        except OSError:
            # 标记写不进去不应该让账户操作失败；下次写盘会再试。
            pass

    def record_proxy_login(self, user_id: str, *, ip: str = "") -> dict[str, Any]:
        """反向代理 trusted-header 登录：更新登录统计并写审计，不校验密码。"""
        with self._lock:
            state = self._load()
            user = (state.get("users") or {}).get(str(user_id))
            if user is None or user.get("status") != STATUS_ACTIVE:
                raise AuthError("not_found", "账户不存在或已停用。")
            user["last_login_at"] = _now()
            user["login_count"] = int(user.get("login_count") or 0) + 1
            self._save(state)
            self._audit(
                "login_ok",
                username=user.get("username"),
                ip=ip,
                user_id=user_id,
                via="trusted_header",
            )
            return public_user(user)

    def _audit(self, event: str, *, username: str = "", ip: str = "", **detail: Any) -> None:
        """追加审计事件；失败不影响主流程（磁盘满也不该让登录崩掉）。"""
        record = {
            "ts": _now(),
            "event": event,
            "username": username,
            "ip": ip or "",
        }
        if detail:
            record["detail"] = {
                key: value
                for key, value in detail.items()
                if key not in {"password", "api_key", "invite_code", "code"}
            }
        try:
            self.auth_dir.mkdir(parents=True, exist_ok=True)
            try:
                os.chmod(self.auth_dir, 0o700)
            except OSError:
                pass
            if self.audit_path.exists() and self.audit_path.stat().st_size > AUDIT_MAX_BYTES:
                rotated = self.audit_path.with_suffix(".log.1")
                try:
                    os.replace(self.audit_path, rotated)
                except OSError:
                    pass
            with open(self.audit_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
            try:
                os.chmod(self.audit_path, 0o600)
            except OSError:
                pass
        except OSError:
            pass

    # ── 查询 ────────────────────────────────────────────────────────────
    def has_users(self) -> bool:
        with self._lock:
            return bool(self._load().get("users"))

    def user_count(self) -> int:
        with self._lock:
            return len(self._load().get("users") or {})

    def list_users(self) -> list[dict[str, Any]]:
        with self._lock:
            users = self._load().get("users") or {}
        result = [public_user(u) for u in users.values()]
        result.sort(key=lambda u: (u["role"] != ROLE_OWNER, u["created_at"] or 0))
        return result

    def get_user(self, user_id: str) -> dict[str, Any] | None:
        with self._lock:
            user = (self._load().get("users") or {}).get(str(user_id))
        return public_user(user) if user else None

    def get_user_by_username(self, username: str) -> dict[str, Any] | None:
        target = str(username or "").strip().lower()
        with self._lock:
            users = self._load().get("users") or {}
            for user in users.values():
                if str(user.get("username", "")).lower() == target:
                    return public_user(user)
        return None

    def settings(self) -> dict[str, Any]:
        with self._lock:
            settings = dict(self._load().get("settings") or {})
        cap = max_users_cap()
        settings["max_users_cap"] = cap
        settings["max_users"] = max(1, min(int(settings.get("max_users") or cap), cap))
        settings["allow_registration"] = bool(settings.get("allow_registration", True))
        return settings

    def list_invites(self) -> list[dict[str, Any]]:
        with self._lock:
            invites = (self._load().get("invites") or {}).values()
            result = [_public_invite(i) for i in invites]
        result.sort(key=lambda i: i.get("created_at") or 0, reverse=True)
        return result

    def audit_tail(self, limit: int = 50) -> list[dict[str, Any]]:
        try:
            lines = self.audit_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        out: list[dict[str, Any]] = []
        for line in lines[-limit:]:
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                out.append(item)
        return out

    # ── 账户创建 ────────────────────────────────────────────────────────
    def create_owner(
        self,
        username: str,
        password: str,
        display_name: str = "",
        *,
        ip: str = "",
    ) -> dict[str, Any]:
        with self._lock:
            state = self._load()
            if state.get("users"):
                raise AuthError("forbidden", "系统已有账户，不能再创建 owner。")
            name = normalize_username(username)
            validate_password(password)
            uid = _new_uid()
            now = _now()
            state["users"][uid] = {
                "id": uid,
                "username": name,
                "display_name": normalize_display_name(display_name, fallback=name),
                "role": ROLE_OWNER,
                "status": STATUS_ACTIVE,
                "password_hash": hash_password(password),
                "created_at": now,
                "created_by": None,
                "last_login_at": None,
                "login_count": 0,
                "password_changed_at": now,
            }
            state["settings"]["max_users"] = max_users_cap()
            self._save(state)
            self._audit("owner_created", username=name, ip=ip)
            return public_user(state["users"][uid])

    def register(
        self,
        invite_code: str,
        username: str,
        password: str,
        display_name: str = "",
        *,
        ip: str = "",
    ) -> dict[str, Any]:
        with self._lock:
            state = self._load()
            settings = state.get("settings") or {}
            if not settings.get("allow_registration", True):
                raise AuthError("forbidden", "管理员已关闭注册。")
            max_users = min(
                int(settings.get("max_users") or max_users_cap()),
                max_users_cap(),
            )
            if len(state.get("users") or {}) >= max_users:
                raise AuthError("full", f"账户数已达上限（{max_users}），请联系管理员。")

            invite = self._find_active_invite(state, invite_code)
            if invite is None:
                raise AuthError("invite_invalid", "邀请码无效、已过期或已用完。")

            name = normalize_username(username)
            if any(
                str(u.get("username", "")).lower() == name
                for u in (state.get("users") or {}).values()
            ):
                raise AuthError("conflict", "用户名已被占用。")
            validate_password(password)

            uid = _new_uid()
            now = _now()
            state["users"][uid] = {
                "id": uid,
                "username": name,
                "display_name": normalize_display_name(display_name, fallback=name),
                "role": ROLE_MEMBER,
                "status": STATUS_ACTIVE,
                "password_hash": hash_password(password),
                "created_at": now,
                "created_by": invite.get("created_by"),
                "last_login_at": None,
                "login_count": 0,
                "password_changed_at": now,
            }
            invite["uses"] = int(invite.get("uses") or 0) + 1
            invite["last_used_at"] = now
            invite["redeemed_usernames"] = list(invite.get("redeemed_usernames") or []) + [name]
            if invite["uses"] >= int(invite.get("max_uses") or 1):
                invite["status"] = "used"
            self._save(state)
            self._audit(
                "user_registered",
                username=name,
                ip=ip,
                invite_id=invite.get("id"),
                user_id=uid,
            )
            return public_user(state["users"][uid])

    def _find_active_invite(self, state: dict[str, Any], code: str) -> dict[str, Any] | None:
        if not _INVITE_RE.match(_normalize_invite_code(code)):
            return None
        digest = _hash_invite(code)
        now = _now()
        for invite in (state.get("invites") or {}).values():
            if invite.get("status") != "active":
                continue
            if int(invite.get("uses") or 0) >= int(invite.get("max_uses") or 1):
                continue
            if float(invite.get("expires_at") or 0) <= now:
                continue
            if hmac.compare_digest(str(invite.get("code_hash") or ""), digest):
                return invite
        return None

    # ── 登录 / 密码 / 状态 ──────────────────────────────────────────────
    def authenticate(
        self, username: str, password: str, *, ip: str = ""
    ) -> tuple[dict[str, Any] | None, str]:
        """返回 ``(public_user, "")`` 或 ``(None, error_code)``。

        error_code: ``bad_credentials`` / ``disabled`` / ``corrupt``。
        未知用户也跑一次哈希，避免通过耗时枚举用户名。
        """
        target = str(username or "").strip().lower()
        # 用户名输入也设上限；超长值不查库，直接按失败处理（仍跑一次 dummy 哈希）。
        if len(target) > 64:
            burn_unknown_user_hash()
            return None, "bad_credentials"
        # 锁内只做「读快照」，scrypt 校验放到锁外：单次校验约 20-50ms，
        # 放在进程级锁里会让匿名登录洪泛把所有人的 rerun/会话校验一起堵住。
        with self._lock:
            state = self._load()
            user = None
            for candidate in (state.get("users") or {}).values():
                if str(candidate.get("username", "")).lower() == target:
                    user = candidate
                    break
            snapshot = None
            if user is not None:
                snapshot = (
                    str(user.get("id") or ""),
                    str(user.get("password_hash") or ""),
                    str(user.get("status") or ""),
                )

        if snapshot is None:
            burn_unknown_user_hash()
            self._audit("login_failed", username=target, ip=ip, reason="unknown")
            return None, "bad_credentials"

        user_id, stored_hash, status = snapshot
        if not verify_password(password, stored_hash):
            self._audit("login_failed", username=target, ip=ip, reason="password")
            return None, "bad_credentials"
        if status != STATUS_ACTIVE:
            self._audit("login_failed", username=target, ip=ip, reason="disabled")
            return None, "disabled"

        with self._lock:
            state = self._load()
            user = (state.get("users") or {}).get(user_id)
            # 校验期间密码被改/账户被停用时，不能凭旧哈希放行。
            if (
                user is None
                or str(user.get("password_hash") or "") != stored_hash
                or user.get("status") != STATUS_ACTIVE
            ):
                self._audit("login_failed", username=target, ip=ip, reason="changed")
                return None, "bad_credentials"
            user["last_login_at"] = _now()
            user["login_count"] = int(user.get("login_count") or 0) + 1
            self._save(state)
            self._audit("login_ok", username=target, ip=ip, user_id=user.get("id"))
            return public_user(user), ""

    def change_password(
        self,
        user_id: str,
        current_password: str,
        new_password: str,
        *,
        ip: str = "",
    ) -> None:
        with self._lock:
            state = self._load()
            user = (state.get("users") or {}).get(str(user_id))
            if user is None:
                raise AuthError("not_found", "账户不存在。")
            if not verify_password(current_password, str(user.get("password_hash") or "")):
                self._audit("password_change_failed", username=user.get("username"), ip=ip)
                raise AuthError("bad_credentials", "当前密码不正确。")
            validate_password(new_password)
            now = _now()
            user["password_hash"] = hash_password(new_password)
            user["password_changed_at"] = now
            self._save(state)
            self._audit(
                "password_changed", username=user.get("username"), ip=ip, user_id=user_id
            )

    def reset_password(
        self,
        actor_id: str,
        user_id: str,
        new_password: str,
        *,
        ip: str = "",
    ) -> None:
        with self._lock:
            state = self._load()
            actor = (state.get("users") or {}).get(str(actor_id))
            if actor is None or actor.get("role") != ROLE_OWNER:
                raise AuthError("forbidden", "只有 owner 可以重置他人密码。")
            user = (state.get("users") or {}).get(str(user_id))
            if user is None:
                raise AuthError("not_found", "账户不存在。")
            validate_password(new_password)
            now = _now()
            user["password_hash"] = hash_password(new_password)
            user["password_changed_at"] = now
            self._save(state)
            self._audit(
                "password_reset_by_admin",
                username=user.get("username"),
                ip=ip,
                actor=actor.get("username"),
                user_id=user_id,
            )

    def set_status(
        self,
        actor_id: str,
        user_id: str,
        status: str,
        *,
        ip: str = "",
    ) -> None:
        if status not in (STATUS_ACTIVE, STATUS_DISABLED):
            raise AuthError("invalid", "非法状态。")
        with self._lock:
            state = self._load()
            actor = (state.get("users") or {}).get(str(actor_id))
            if actor is None or actor.get("role") != ROLE_OWNER:
                raise AuthError("forbidden", "只有 owner 可以停用/启用账户。")
            if str(actor_id) == str(user_id):
                raise AuthError("forbidden", "不能停用自己的账户。")
            user = (state.get("users") or {}).get(str(user_id))
            if user is None:
                raise AuthError("not_found", "账户不存在。")
            if status == STATUS_DISABLED:
                active_owners = [
                    u
                    for u in (state.get("users") or {}).values()
                    if u.get("role") == ROLE_OWNER and u.get("status") == STATUS_ACTIVE
                ]
                if user.get("role") == ROLE_OWNER and len(active_owners) <= 1:
                    raise AuthError("forbidden", "至少要保留一个可用的 owner。")
            user["status"] = status
            self._save(state)
            self._audit(
                "user_status_changed",
                username=user.get("username"),
                ip=ip,
                actor=actor.get("username"),
                status=status,
            )

    # ── 邀请码 ──────────────────────────────────────────────────────────
    def create_invite(
        self,
        actor_id: str,
        *,
        note: str = "",
        ttl_hours: int = DEFAULT_INVITE_TTL_HOURS,
        max_uses: int = DEFAULT_INVITE_USES,
        ip: str = "",
    ) -> tuple[dict[str, Any], str]:
        with self._lock:
            state = self._load()
            actor = (state.get("users") or {}).get(str(actor_id))
            if actor is None or actor.get("role") != ROLE_OWNER:
                raise AuthError("forbidden", "只有 owner 可以生成邀请码。")
            now = _now()
            active = [
                i
                for i in (state.get("invites") or {}).values()
                if i.get("status") == "active"
                and float(i.get("expires_at") or 0) > now
            ]
            if len(active) >= MAX_ACTIVE_INVITES:
                raise AuthError("full", f"活动邀请码过多（上限 {MAX_ACTIVE_INVITES}），请先撤销旧码。")
            ttl = max(1, min(MAX_INVITE_TTL_HOURS, int(ttl_hours)))
            uses = max(1, min(MAX_INVITE_USES, int(max_uses)))
            code = _generate_invite_code()
            invite_id = _new_invite_id()
            invite = {
                "id": invite_id,
                "code_hash": _hash_invite(code),
                "created_by": actor_id,
                "created_at": now,
                "expires_at": now + ttl * 3600,
                "max_uses": uses,
                "uses": 0,
                "status": "active",
                "note": str(note or "").strip()[:80],
                "last_used_at": None,
                "redeemed_usernames": [],
            }
            state["invites"][invite_id] = invite
            self._save(state)
            self._audit(
                "invite_created",
                username=actor.get("username"),
                ip=ip,
                invite_id=invite_id,
                max_uses=uses,
                ttl_hours=ttl,
            )
            return _public_invite(invite), code

    def revoke_invite(self, actor_id: str, invite_id: str, *, ip: str = "") -> None:
        with self._lock:
            state = self._load()
            actor = (state.get("users") or {}).get(str(actor_id))
            if actor is None or actor.get("role") != ROLE_OWNER:
                raise AuthError("forbidden", "只有 owner 可以撤销邀请码。")
            invite = (state.get("invites") or {}).get(str(invite_id))
            if invite is None:
                raise AuthError("not_found", "邀请码不存在。")
            invite["status"] = "revoked"
            self._save(state)
            self._audit(
                "invite_revoked",
                username=actor.get("username"),
                ip=ip,
                invite_id=invite_id,
            )

    def update_settings(
        self,
        actor_id: str,
        *,
        allow_registration: bool | None = None,
        max_users: int | None = None,
        ip: str = "",
    ) -> dict[str, Any]:
        with self._lock:
            state = self._load()
            actor = (state.get("users") or {}).get(str(actor_id))
            if actor is None or actor.get("role") != ROLE_OWNER:
                raise AuthError("forbidden", "只有 owner 可以修改注册策略。")
            settings = state.setdefault("settings", {})
            if allow_registration is not None:
                settings["allow_registration"] = bool(allow_registration)
            if max_users is not None:
                cap = max_users_cap()
                wanted = max(1, min(cap, int(max_users)))
                current = len(state.get("users") or {})
                if wanted < current:
                    raise AuthError(
                        "invalid", f"上限不能小于当前账户数（{current}）。"
                    )
                settings["max_users"] = wanted
            self._save(state)
            self._audit(
                "settings_updated",
                username=actor.get("username"),
                ip=ip,
                allow_registration=settings.get("allow_registration"),
                max_users=settings.get("max_users"),
            )
            return dict(settings)
