CLI_CONFIG = {
    # 独立部署后不再请求上游的公告接口：留空即完全关闭，CLI 不会外联。
    # 想接自己的公告服务时，把 URL 填回来即可（返回 JSON:
    # {"announcements": [...], "require_attention": false}）。
    "announcements_url": "",
    "announcements_timeout": 1.0,
    "announcements_fallback": "",
}
