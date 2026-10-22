"""出站 LLM 端点安全校验（多用户成员专用）。

背景：侧栏允许用户填写「API Base URL」以接第三方中转。对 owner 这是自有基础设施，
没问题；但普通成员可控的 URL 会让**服务器**去请求任意地址，形成 SSRF：

- `http://127.0.0.1:...` 探测本机服务；
- `http://169.254.169.254/...` 读取云元数据；
- `http://192.168.x.x:...` 探测内网；
- 公网域名解析到上述地址同样能绕过。

这里的策略是**默认只允许公网目标**：scheme 限 http(s)、禁止 userinfo、解析 DNS
后拒绝任何非 global 地址。需要接内网中转时，由 owner 显式设置
``TRADINGAGENTS_ALLOW_PRIVATE_BASE_URLS=1``（服务器级开关，成员不能自己开）。

已知残余风险：DNS rebinding（校验时解析到公网、连接时解析到内网）和公网主机
302 跳转到内网。彻底解决需要把解析结果钉死到连接层，成本远高于本项目场景；
owner 若担心，应把成员可用端点收敛到自己的中转，或关闭成员自定义 Base URL。
"""

from __future__ import annotations

import ipaddress
import os
import socket
from urllib.parse import urlparse

MAX_BASE_URL_LENGTH = 2048
_PRIVATE_HOST_SUFFIXES = (".local", ".internal", ".home.arpa", ".lan")
_PRIVATE_HOSTS = {
    "localhost",
    "localhost.localdomain",
    "metadata",
    "metadata.google.internal",
    "instance-data",
}


def allow_private_base_urls() -> bool:
    return os.environ.get("TRADINGAGENTS_ALLOW_PRIVATE_BASE_URLS", "").strip() == "1"


def _reject(message: str) -> None:
    raise ValueError(message)


def _check_ip(raw_ip: str) -> None:
    text = str(raw_ip).split("%", 1)[0].strip()
    try:
        ip = ipaddress.ip_address(text)
    except ValueError:
        _reject(f"地址无法识别：{raw_ip}")
        return
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if (not ip.is_global) or ip.is_multicast:
        _reject(f"不允许访问内网/保留地址：{ip}")


def validate_base_url(raw: str, *, allow_private: bool | None = None) -> str:
    """校验一个用户可控的 Base URL；不合法抛 ``ValueError``（消息可直接展示）。"""
    if allow_private is None:
        allow_private = allow_private_base_urls()
    text = str(raw or "").strip()
    if not text:
        _reject("地址不能为空")
    if len(text) > MAX_BASE_URL_LENGTH:
        _reject("地址过长")
    if any(ch.isspace() or ord(ch) < 0x20 or ord(ch) == 0x7F for ch in text):
        _reject("地址包含空白或控制字符")

    try:
        parsed = urlparse(text)
        host = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        _reject(f"地址格式不正确：{exc}")
        return text  # pragma: no cover - _reject 必然抛

    if parsed.scheme not in ("http", "https"):
        _reject("只允许 http:// 或 https:// 地址")
    if parsed.username or parsed.password:
        _reject("地址不能包含用户名/密码")
    if not host:
        _reject("地址缺少主机名")
    if port is not None and not (1 <= int(port) <= 65535):
        _reject("端口不合法")

    hostname = host.rstrip(".").lower()
    if not allow_private and (
        hostname in _PRIVATE_HOSTS or hostname.endswith(_PRIVATE_HOST_SUFFIXES)
    ):
        _reject(f"不允许访问本机/内网主机：{hostname}")

    if allow_private:
        return text

    try:
        infos = socket.getaddrinfo(
            hostname,
            port or (443 if parsed.scheme == "https" else 80),
            type=socket.SOCK_STREAM,
        )
    except socket.gaierror as exc:
        _reject(f"主机无法解析：{hostname}（{exc}）")
        return text  # pragma: no cover
    if not infos:
        _reject(f"主机无法解析：{hostname}")
    # 只检查前几个结果，避免一个恶意域名返回成千上万条记录拖死 UI。
    for info in infos[:8]:
        sockaddr = info[4]
        if sockaddr:
            _check_ip(str(sockaddr[0]))
    return text
