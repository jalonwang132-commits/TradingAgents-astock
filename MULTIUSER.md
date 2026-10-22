# 多用户部署协议（Multi-User Protocol v1）

> 目标：把这套**单用户本地应用**安全地部署给几个朋友用，做到「每人一套历史、
> 每人一套模型配置和 API Key」，同时不引入运维噩梦。
>
> 本文是协议说明 + 部署手册。代码实现遵循的硬规则在文末「验收清单」里。

---

## 0. 一句话结论

- **一个 Streamlit 进程** + **应用内登录** + **每账户目录隔离** + **邀请码注册** +
  **每账户 API Key**，即可满足「朋友之间互不可见、互不覆盖」。
- **不要**把 8501 端口直接暴露公网；前面必须有一个做 TLS 的反向代理。
- **owner**（第一个账户）继续使用旧的 `~/.tradingagents/`，CLI 与 Web 共享同一份
  历史；朋友的数据在 `~/.tradingagents/users/<uid>/`，与 owner 完全分开。

---

## 1. 为什么原来不能直接多开

单用户设计下的共享点不止「文件路径」：

| 资源 | 原位置 | 多用户下的后果 |
|---|---|---|
| 分析报告 | `~/.tradingagents/logs` | A 的历史对 B 可见 |
| 未完成任务 / 断点 | `~/.tradingagents/incomplete_tasks.json`、`cache/checkpoints` | A 的任务出现在 B 侧栏，B 可续跑/清空 A 的进度 |
| 模型选择 | `~/.tradingagents/llm_config.json` | B 改模型会覆盖 A |
| 团队 / 提示词 / 战法 | `~/.tradingagents/team_config.json` | A 的战法会进 B 的提示词 |
| API Key | `.env`（进程级环境变量） | 所有人共用 owner 的额度；成员无法各自配置 |
| 缺失数据 / 记忆日志 | `~/.tradingagents/missing_data*`、`memory/` | 数据串号 |
| **运行期配置** | `dataflows/config.py` 的模块级 `_config` | **最隐蔽**：两个分析线程同时跑时，后启动的会覆盖前一次的 `strategy_packs` / `output_language` / 缓存目录，A 的战法真的会进 B 的提示词 |

最后一项是关键：**只隔离文件目录不够**。运行期配置必须线程隔离，否则并发分析
仍会串。实现上已改成 `contextvars.ContextVar`。

---

## 2. 身份模型

### 2.1 角色

| 角色 | 数量 | 权限 |
|---|---|---|
| `owner` | 1 | 全部功能 + 用户管理 + 邀请码 + 注册策略 + 审计日志 + Claude 订阅覆盖 |
| `member` | 0..N | 自己的分析、历史、模型/Key、团队/战法；**不能**看别人数据、不能用 owner 的 `.env` Key 或订阅额度 |

### 2.2 注册：邀请制，不是开放注册

- 首次启动、账户库为空时，只显示「创建 owner」。
- 之后所有账户都必须凭 owner 生成的**邀请码**注册；邀请码默认
  **72 小时 / 1 次使用 / 只显示一次**（库里只存 SHA-256）。
- owner 可以随时：关闭注册、调低账户上限、撤销邀请码、停用账户、重置成员密码。
- 默认硬上限 `TRADINGAGENTS_MAX_USERS=10`，管理员只能在此之下调低。

这比「让朋友随便注册」多一步，但换来的是：链接被转发到群里也不会无限长账户；
每个账户都能追溯到邀请人。

### 2.3 会话

- 登录态只存在 Streamlit 服务端 `session_state`，**不写 cookie、不进 URL**。
- 绝对有效期 12 小时，空闲 2 小时失效。
- 修改/重置密码后，旧会话立即失效（会话里存了 `password_changed_at` 版本号）。
- owner 停用账户后，该用户下一次 rerun 就会被踢回登录页。
- **刷新页面会掉登录**：Streamlit 刷新即新会话。这是安全取舍，不是 bug。
  想「刷新不掉登录」请用第 7 节的 trusted-header 模式，由反向代理维护会话。

### 2.4 防爆破

| 动作 | 限制 |
|---|---|
| 单 IP + 单用户名登录失败 | 5 次 / 15 分钟 |
| 单 IP 登录失败 | 50 次 / 15 分钟 |
| 单 IP 注册尝试 | 10 次 / 小时 |
| 单账户并发分析 | 1 个 |
| 全服务器并发分析 | 默认 4 个 |
| 单成员每日分析 | 默认 20 次（owner 不限） |
| 单账户磁盘占用 | 默认 2048 MB（owner 不限） |

登录对不存在的用户名也执行一次等价 scrypt 哈希，避免通过响应时间枚举账户。
密码用 stdlib `scrypt`（N=2^14, r=8, p=1）加盐存储；没有引入 bcrypt/argon2 依赖。

---

## 3. 存储布局

```
~/.tradingagents/
├── .env                          # 服务器级 Key：只对 owner 生效
├── auth/                         # 账户库（0700 / 文件 0600）
│   ├── accounts.json             # 用户、邀请码哈希、注册策略
│   ├── .initialized              # 已初始化标记：防删除账户库后重建 owner
│   └── audit.log                 # 登录/注册/密码/状态变更审计（不含密码/Key）
│
├── logs/  cache/  memory/        # owner 的业务数据（= 旧单用户目录，CLI 共用）
├── incomplete_tasks.json
├── missing_data_tasks.json
├── missing_data_cache/
├── llm_config.json               # owner 的模型选择（无 Key）
├── team_config.json              # owner 的团队/提示词/战法
├── secrets.json                  # owner 的 API Key（0600）
└── users/
    └── u_<12hex>/                # 每个成员一套，结构与 owner 相同
        ├── logs/
        ├── cache/                # 行情缓存 + LangGraph 断点
        ├── memory/trading_memory.md
        ├── incomplete_tasks.json
        ├── missing_data_tasks.json
        ├── missing_data_cache/
        ├── llm_config.json
        ├── team_config.json
        ├── secrets.json
        └── usage.json            # 每日运行次数
```

> **owner 为什么复用旧目录？** 升级时不能把已有的历史报告/缓存搬走——CLI 还在用
> `~/.tradingagents/`，搬走会让老用户升级后「历史全没了」。因此 owner 的
> `UserPaths.root` 就是 `~/.tradingagents/`，成员才在 `users/<uid>/`。
> 隔离性不受影响：owner 之外的账户没有任何路径能指向旧目录。
> 如果旧配置用 `TRADINGAGENTS_RESULTS_DIR` / `TRADINGAGENTS_CACHE_DIR` /
> `TRADINGAGENTS_MEMORY_LOG_PATH` 改写了个别路径，owner 仍沿用这些环境变量
> （与 CLI 一致）；成员一律不继承。

### 3.1 文件权限

- `auth/`、`users/<uid>/`：`0700`
- `accounts.json`、`secrets.json`、`audit.log`：`0600`
- 其他报告/缓存：继承目录权限。

### 3.2 账户库损坏时绝不自动重建

`accounts.json` 损坏或缺失但 `.initialized` 存在时，应用会**拒绝进入创建 owner
流程**并提示从备份恢复。否则任何人都能通过删文件接管系统。

---

## 4. 隔离协议（代码必须遵守的规则）

### 4.1 路径

- 所有用户态路径必须来自 `web.user_paths.UserPaths`，**禁止**在业务代码里直接拼
  `Path.home()/".tradingagents"`。
- 读取报告前必须过 `paths.contains(path)`（只允许 `logs/` 内），防止构造
  `session_state["viewing_history"]` 读取任意文件。
- 用户 ID 由服务端生成（`u_` + 12 hex），不来自表单；用户名只用于登录/展示。

### 4.2 运行期配置

- `tradingagents/dataflows/config.py` 使用 `ContextVar`，每个线程/任务一份。
- `TradingAgentsGraph.__init__` 在**分析线程**里 `set_config()`；UI 线程在认证门里
  `activate()`。两者互不影响。
- 任何新增的、收 `curr_date` 或路径的数据接口，都要从 `get_config()` 取路径，
  不能读 `DEFAULT_CONFIG` 的模块级值。

### 4.3 密钥优先级

```
用户 secrets.json 里的 Key
    > （仅 owner）服务器 .env 里的环境变量
    > 报错，提示去「账户 → API Key」配置
```

普通成员的 `config["allow_env_api_keys"] = False`，客户端不会回落到 `.env`。
这条规则覆盖：OpenAI 原生、OpenAI 兼容、DeepSeek/Qwen/GLM/MiniMax/xAI/OpenRouter、
Anthropic、Google、Azure。Ollama 不需要 Key。

### 4.4 出站端点（防 SSRF）

普通成员可以填自己的 API Base URL（第三方中转），但**服务器会去请求这个地址**。
默认只允许公网目标：http(s)、禁止 userinfo、DNS 解析后拒绝 loopback / 私有 /
link-local / 保留地址（包括 `169.254.169.254` 云元数据、`127.0.0.1`、`::1`、
`192.168/10/172.16` 等）。内网中转由 owner 显式开启
`TRADINGAGENTS_ALLOW_PRIVATE_BASE_URLS=1`（成员不能自己开）。

残余风险：DNS rebinding 和公网主机 302 跳内网未在连接层钉死；对更高安全要求的部署，
把成员可用端点收敛到自己的中转，或关闭成员自定义 Base URL。owner 自己的 `.env`
`BACKEND_URL` 不受此限制。

### 4.5 Claude 订阅覆盖

`claude_agent_sdk` 用的是**服务器本机**的 claude 登录凭据/`CLAUDE_CODE_OAUTH_TOKEN`，
属于服务器级资源，只对 owner 开放。成员侧栏不显示这个开关，`_build_config` 也会
强制 `subscription_scope=off`。

### 4.6 并发与配额

- 每个用户同时只能跑 1 个分析；全服务器默认 4 个。
- 成员每日次数持久化在 `usage.json`，进程重启不清零。
- 磁盘配额按账户目录统计，**只约束成员**（owner 负责整台服务器，避免升级时被旧缓存锁死）；
owner 的统计只算业务目录，不把 `auth/` 和别人的 `users/` 算进去。
- 运行租约在分析线程 `finally` 里释放；异常/停止/登出都不会把用户永久锁死。

---

## 5. 快速开始（本机 / 局域网）

```bash
# 1) 启动（首次会要求创建 owner）
jalon-web

# 2) 浏览器打开 http://127.0.0.1:8501
# 3) 创建 owner → 左下「👤 账户」→「邀请码」→ 生成一个码
# 4) 把「网址 + 邀请码」发给朋友；朋友在登录页「注册」里填码
```

朋友注册后：

1. 「👤 账户」→「API Key」保存自己的 Key（或侧栏「⚙️ 模型配置」里保存）；
2. 选供应商/模型；
3. 回工作台开始分析。

owner 如果继续用 `.env` 也可以，不冲突；账户里保存的 Key 优先级更高。

---

## 6. 正式部署（公网 / 多朋友）

### 6.1 拓扑

```
朋友浏览器 ──HTTPS──> Caddy / nginx（TLS、限流、访问日志）
                         │ 127.0.0.1:8501（只监听回环）
                         ▼
                   Streamlit 单进程
                         │
                   ~/.tradingagents/
```

**绝对不要**把 `--server.address 0.0.0.0` 的 8501 直接映射到公网。

### 6.2 只监听回环

```bash
python -m streamlit run web/app.py \
  --server.address 127.0.0.1 \
  --server.port 8501 \
  --server.headless true
```

### 6.3 Caddy 示例

```caddyfile
friends.example.com {
    encode gzip
    reverse_proxy 127.0.0.1:8501 {
        # Streamlit 的 websocket 需要长连接
        flush_interval -1
    }
    # 可选：访问日志；注意日志里可能有 URL 参数，不要记录敏感信息
    log {
        output file /var/log/caddy/friends.log
    }
}
```

Caddy 自动签发 TLS。若需要按 IP 限流，装 `caddy-ratelimit` 插件后加
`rate_limit` 指令；应用内也有一层限流。

### 6.4 nginx 示例

```nginx
server {
    listen 443 ssl http2;
    server_name friends.example.com;

    ssl_certificate     /etc/letsencrypt/live/friends.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/friends.example.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8501;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }
}
```

nginx 后面部署时，设置：

```bash
TRADINGAGENTS_TRUST_PROXY=1
```

应用才会从 `X-Forwarded-For` 取真实 IP 做限流。**只在你信任的代理解析头时开启**；
直接暴露 8501 时开启等于让攻击者伪造 IP。

### 6.5 systemd 示例

```ini
[Unit]
Description=Jalon A-share research desk
After=network-online.target

[Service]
Type=simple
User=jalon
WorkingDirectory=/home/jalon/apps/tradingagents-astock
EnvironmentFile=/home/jalon/apps/tradingagents-astock/.env
Environment=TRADINGAGENTS_TRUST_PROXY=1
ExecStart=/home/jalon/apps/tradingagents-astock/.venv/bin/python -m streamlit run web/app.py \
  --server.address 127.0.0.1 --server.port 8501 --server.headless true
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

### 6.6 Docker

`docker-compose.yml` 的 `web` 服务默认把端口映射到宿主机；公网部署时改成只绑回环：

```yaml
ports:
  - "127.0.0.1:8501:8501"
```

数据卷仍然是一个 `tradingagents_data`；多用户目录都在里面。升级镜像前先备份。

---

## 7. 可选：反向代理认证（刷新不掉登录）

如果你已经在用 nginx basic auth / Authelia / oauth2-proxy，可以让代理把用户名
传给应用，应用按 header 自动登录，不再显示登录页：

```bash
TRADINGAGENTS_TRUSTED_HEADER=X-Auth-User
TRADINGAGENTS_TRUSTED_SECRET=<随机长字符串>
TRADINGAGENTS_TRUSTED_SECRET_HEADER=X-Auth-Secret
```

nginx：

```nginx
location / {
    auth_basic "Friends";
    auth_basic_user_file /etc/nginx/friends.htpasswd;

    proxy_set_header X-Auth-User $remote_user;
    proxy_set_header X-Auth-Secret "与上面一致的长随机串";
    proxy_pass http://127.0.0.1:8501;
    # websocket 头略，同 6.4
}
```

要求：

- **应用只监听 127.0.0.1**，否则 header 可被伪造；
- header 里的用户名必须已经存在于账户库（应用不会自动开户）；
- 共享密钥必须足够长且只放在代理配置里。

这个模式下「注册」由代理的 htpasswd/用户系统完成，应用内邀请码可以关闭。

---

## 8. 运维手册

### 备份

至少备份：

```bash
tar czf tradingagents-backup-$(date +%F).tgz \
  -C ~ .tradingagents/auth \
       .tradingagents/users \
       .tradingagents/logs \
       .tradingagents/memory \
       .tradingagents/missing_data_cache \
       .tradingagents/incomplete_tasks.json \
       .tradingagents/missing_data_tasks.json \
       .tradingagents/team_config.json \
       .tradingagents/llm_config.json \
       .tradingagents/secrets.json
```

缓存（`cache/`、`missing_data_cache/`）可以不备份，重建即可。`.env` 单独保管，
不要进 Git/网盘。

### 恢复

停服务 → 覆盖 `~/.tradingagents/` → 确认 `auth/accounts.json` 和 `.initialized`
都在 → 启动。**不要**只恢复 accounts.json 而丢掉 `.initialized`；反之亦然。

### 添加朋友

owner：「👤 账户」→「管理」→「邀请码」→ 生成 → 把网址和码发过去。
朋友注册后若没有 Key，点「开始分析」会被明确拦住，不会误用 owner 的额度。

### 停用 / 离职

owner：「管理」→「用户管理」→「停用」。数据保留；重新启用即可继续。
如需彻底清理，停用后手动归档 `users/<uid>/`（先确认没有正在跑的任务）。

### 成员忘记密码

owner：「管理」→「用户管理」→「重置成员密码」。重置后旧会话立即失效。

### owner 忘记密码

只能在服务器上操作（没有邮箱找回）：

1. 停服务；
2. 备份 `auth/accounts.json`；
3. 用 Python 生成新哈希：
   ```bash
   .venv/bin/python - <<'PY'
   from web.auth.passwords import hash_password
   print(hash_password("你的新密码"))
   PY
   ```
4. 把 `accounts.json` 里 owner 的 `password_hash` 替换掉，并把
   `password_changed_at` 改成当前时间戳；
5. 启动，用新密码登录。

### 轮换 API Key

「👤 账户」→「API Key」→ 清除 → 保存新 Key。旧 Key 从 `secrets.json` 删除；
如怀疑泄露，去供应商控制台吊销。

---

## 9. 威胁模型

### 能防住

- 朋友 B 通过 Web 读/覆盖朋友 A 的历史、模型、团队配置、Key；
- 未登录访问业务页面；
- 猜密码 / 批量注册（限流 + 邀请码 + 账户上限）；
- 构造历史路径读取任意文件（`paths.contains` 只允许 `logs/`）；
- 两个分析并发时提示词/缓存串号（ContextVar）；
- 普通成员偷偷使用 owner 的 `.env` Key、Claude 订阅额度，或把服务器当跳板请求内网；
- 账户库损坏后被重建 owner 接管（`.initialized` 标记）。

### 不能防住

- **服务器 root / 同一系统账户**：能直接读 `secrets.json`、`.env`、别人的目录。
  这是「文件权限级」而非「密码学级」隔离。
- **owner 本人**：owner 是管理员，能停用/重置账户，也能读服务器文件。
- **服务器被入侵**：此时任何本地密钥存储都不安全；应迁移到系统 Keychain / Vault。
- **DDoS / 大流量**：应用内限流只能挡自动化撞库，挡不住流量攻击；需要 CDN/反代层。
- **DNS rebinding / 开放重定向**：Base URL 校验在解析时拒绝内网 IP，但未把解析结果
  钉死到连接层；公网主机 302 到内网仍有理论可能。
- **多进程 / 多 worker**：账户写锁、运行租约、限流都是**进程内**的。本项目的
  部署形态是单 Streamlit 进程；不要用 `--server.enableXsrfProtection` 之外的方式
  起多个 worker 共享同一数据目录。
- **浏览器被恶意扩展/木马控制**：登录态在服务端，但已登录的浏览器可以为所欲为。

### 已知限制

1. 页面刷新需要重新登录（用 trusted-header 可解决）。
2. 没有邮箱验证/找回密码/2FA。
3. 没有用户自助删除账户（owner 停用即可）。
4. 行情缓存按用户各存一份，朋友多时磁盘会成倍增长；配额会兜底。
5. `role_llms` 多供应商分角色配置仍只在 `config` 层使用（Web 没有编辑入口）；
   账户页可以按供应商逐个保存 Key，`_build_role_llms` 会按目标 provider 取对应的
   `llm_api_keys`。手工编辑 `secrets.json` 的格式见 `web/secrets_store.py`。
6. 每日次数是自然日，跨时区用户按服务器本地日期算。
7. **不要同时用 CLI 和 Web（owner）跑分析**：两者共享 owner 的 `logs/cache/memory`，
   checkpoint SQLite 可能互相争用。CLI 适合单人维护/调试，Web 是多用户入口。

---

## 10. 验收清单（改代码时对照）

- [ ] 所有用户态路径来自 `UserPaths`；没有新增 `Path.home()/".tradingagents"` 直连。
- [ ] 新增的持久化文件进 `UserPaths`，并加入 `ensure()`/`disk_usage`。
- [ ] 新增的 provider 必须支持显式 `api_key`，并尊重 `allow_env_api_key=False`。
- [ ] 新增的后台线程启动时调用 `activate(paths)`，结束释放运行租约。
- [ ] 新增的全局可变配置必须用 `ContextVar`，不能退回模块级 dict。
- [ ] 账户/邀请/密码相关写操作走 `AuthStore`，不要在 UI 里直接改 JSON。
- [ ] 新增 UI 页面在 `require_login()` 之后渲染；不要在认证前读用户文件。
- [ ] 跑 `pytest tests/`，新增隔离用例至少覆盖「A 写、B 读不到」。
