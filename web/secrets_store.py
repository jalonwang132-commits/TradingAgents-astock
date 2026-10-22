"""每用户 API Key 的本地存储。

位置：``<user_root>/secrets.json``，权限 0600（目录 0700）。和 ``llm_config.json``
分开是刻意的：

- 模型选择是普通偏好，可能被用户导出/分享；
- API Key 是凭据，**不回显、不跟随导出、不进团队配置**。

安全边界说明：这是**文件权限级**保护，不是硬件级保险箱。服务器 root / 同一个
系统账户可以读；如果威胁模型包含「服务器被攻破」，应改用系统 Keychain / Vault。
对「朋友 B 通过 Web 应用读朋友 A 的 Key」这个主要威胁，per-user 路径 + 不回显
已经足够。详见 MULTIUSER.md。
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_LOCK = threading.RLock()
SECRETS_VERSION = 1
MAX_KEY_LENGTH = 512
# 允许常见可见字符；拒绝换行/控制字符，避免把 header 注入类内容写进配置。
_KEY_RE = re.compile(r"^[\x21-\x7e]+$")


def _normalize_provider(provider: str) -> str:
    value = str(provider or "").strip().lower()
    if not value or not re.match(r"^[a-z0-9_]+$", value):
        raise ValueError(f"非法供应商名: {provider!r}")
    return value


def validate_api_key(key: str) -> str:
    value = str(key or "").strip()
    if not value:
        raise ValueError("API Key 不能为空")
    if len(value) > MAX_KEY_LENGTH:
        raise ValueError(f"API Key 过长（上限 {MAX_KEY_LENGTH} 字符）")
    if not _KEY_RE.match(value):
        raise ValueError("API Key 含空白或控制字符")
    return value


def mask_key(key: str) -> str:
    """只显示末 4 位；太短的一律不显示。"""
    value = str(key or "")
    if len(value) < 8:
        return "****"
    return f"****{value[-4:]}"


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, path)


def load_secrets(path: Path | str) -> dict[str, str]:
    """读取 ``{provider: key}``；文件缺失/损坏返回空表（用户重填即可）。"""
    target = Path(path)
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        logger.warning("API Key 文件读取失败，按未配置处理: %s", exc)
        return {}
    if not isinstance(raw, dict):
        return {}
    keys = raw.get("api_keys")
    if not isinstance(keys, dict):
        return {}
    cleaned: dict[str, str] = {}
    for provider, value in keys.items():
        try:
            cleaned[_normalize_provider(provider)] = validate_api_key(str(value))
        except (TypeError, ValueError):
            continue
    return cleaned


def save_secrets(path: Path | str, keys: dict[str, str]) -> dict[str, str]:
    cleaned: dict[str, str] = {}
    for provider, value in (keys or {}).items():
        cleaned[_normalize_provider(provider)] = validate_api_key(value)
    with _LOCK:
        _atomic_write(
            Path(path),
            {
                "version": SECRETS_VERSION,
                "api_keys": cleaned,
            },
        )
    return cleaned


def set_api_key(path: Path | str, provider: str, key: str) -> dict[str, str]:
    with _LOCK:
        keys = load_secrets(path)
        keys[_normalize_provider(provider)] = validate_api_key(key)
        return save_secrets(path, keys)


def delete_api_key(path: Path | str, provider: str) -> dict[str, str]:
    with _LOCK:
        keys = load_secrets(path)
        keys.pop(_normalize_provider(provider), None)
        return save_secrets(path, keys)


def has_api_key(path: Path | str, provider: str) -> bool:
    return bool(load_secrets(path).get(_normalize_provider(provider)))
