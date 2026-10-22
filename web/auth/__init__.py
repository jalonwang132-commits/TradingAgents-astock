"""多用户认证与隔离。

模块划分：

- :mod:`web.auth.passwords` 密码哈希（stdlib scrypt）与输入校验
- :mod:`web.auth.store`    账户 / 邀请码 / 审计的磁盘存储（纯 Python，不依赖 Streamlit）
- :mod:`web.auth.ratelimit` 登录 / 注册 / 邀请码的进程内限流
- :mod:`web.auth.ui`       Streamlit 登录门、账户页、管理员页

存储层刻意不 import streamlit：这样测试可以直接构造临时目录验证隔离规则，
也避免未来换 Web 框架时把认证逻辑绑死在 UI 上。
"""

from .passwords import (
    hash_password,
    normalize_username,
    validate_password,
    verify_password,
)
from .store import AuthError, AuthStore, public_user

__all__ = [
    "AuthError",
    "AuthStore",
    "hash_password",
    "normalize_username",
    "public_user",
    "validate_password",
    "verify_password",
]
