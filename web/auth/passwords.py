"""密码哈希与账户输入校验（只用标准库）。

为什么是 scrypt：Python 标准库自带，部署给朋友时不需要额外装 bcrypt/argon2；
scrypt 是内存硬函数，服务端单次登录 ~50ms，暴力破解成本足够高。

存储格式：``scrypt$n$r$p$<salt_b64>$<hash_b64>``。参数写在哈希串里，将来调高
N 时旧账户仍能登录（用旧参数验证，登录成功后可重新哈希）。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets

# OWASP 推荐的低成本档位；16 MiB 内存 / 单次 ~40-60ms。
_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SALT_BYTES = 16
_DK_LEN = 32

USERNAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{2,23}$")
MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 128
MAX_DISPLAY_NAME = 32

# 未知用户也跑一次哈希，避免通过响应时间枚举用户名。
_DUMMY_HASH: str | None = None


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.b64decode(text.encode("ascii"), validate=True)


def _derive(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=n,
        r=r,
        p=p,
        dklen=_DK_LEN,
        maxmem=0,
    )


def hash_password(password: str) -> str:
    """返回带参数的 scrypt 哈希串。空密码直接拒绝。"""
    if not isinstance(password, str) or not password:
        raise ValueError("密码不能为空")
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = _derive(password, salt, _SCRYPT_N, _SCRYPT_R, _SCRYPT_P)
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    """常量时间校验；格式非法一律返回 False（不抛异常，避免登录页崩）。"""
    if not isinstance(password, str) or not isinstance(stored, str):
        return False
    # 登录接口不能拿一个巨大的字符串去跑 scrypt：攻击者可以借此消耗 CPU。
    if len(password) > 1024:
        return False
    try:
        scheme, n_s, r_s, p_s, salt_s, hash_s = stored.split("$")
        if scheme != "scrypt":
            return False
        n, r, p = int(n_s), int(r_s), int(p_s)
        if n < 2**12 or n > 2**18 or r < 1 or r > 32 or p < 1 or p > 16:
            return False
        salt = _unb64(salt_s)
        expected = _unb64(hash_s)
        actual = _derive(password, salt, n, r, p)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


def burn_unknown_user_hash() -> None:
    """对不存在的用户名也执行一次同等成本的哈希（防时序枚举）。"""
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        _DUMMY_HASH = hash_password("dummy-password-for-timing")
    verify_password("dummy-password-for-timing", _DUMMY_HASH)


def normalize_username(raw: str) -> str:
    """用户名统一小写；只允许字母数字下划线连字符，3~24 位。"""
    value = str(raw or "").strip().lower()
    if not USERNAME_RE.match(value):
        raise ValueError("用户名需为 3~24 位字母、数字、下划线或连字符，且以字母/数字开头")
    return value


def normalize_display_name(raw: str, *, fallback: str = "") -> str:
    value = str(raw or "").strip()
    if not value:
        value = fallback
    value = value.replace("\n", " ").replace("\r", " ")[:MAX_DISPLAY_NAME]
    return value or "未命名"


def validate_password(password: str) -> None:
    """密码强度：长度 8~128，不能全是空格。"""
    if not isinstance(password, str):
        raise ValueError("密码格式不正确")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"密码至少 {MIN_PASSWORD_LENGTH} 位")
    if len(password) > MAX_PASSWORD_LENGTH:
        raise ValueError(f"密码最多 {MAX_PASSWORD_LENGTH} 位")
    if password.strip() == "":
        raise ValueError("密码不能全是空格")
