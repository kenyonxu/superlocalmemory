# MSLM 远程使用指南

> **适用范围**：让别的电脑上的 AI 工具（hermes agent、DSH harness、任意 MCP 客户端）远程使用本机（`xukai-Titan-GT77-12UHS`）上已部署的 mslm 记忆库。
> **现状**：远程访问面已于 2026-10-24 启用并通过端到端验证（运维笔记 §31）。服务端就绪，本文其余部分是客户端接入与日常运维。
> **配套阅读**：`docs/distributed-deployment.md`（上游远程访问全册）、`docs/hermes.md`（Hermes 集成专章）、`docs/运维笔记-2026-09-22-桥侧与维护层挂账.md` §31（启用实录）。

---

## 1. 拓扑：谁走哪条路

```
┌─ 本机（记忆服务器）───────────────────────────┐
│  mslm daemon (mslm-memory 4.2.6)              │
│  ├─ 127.0.0.1:8765  主 daemon（回环，本地专用）│◄── 知惠 gateway / 本机 CLI / hooks
│  └─ 0.0.0.0:8443    TLS 远程监听器（对外唯一） │◄── 远端 hermes / DSH / MCP 客户端
└───────────────────────────────────────────────┘
        ▲ LAN 192.168.50.74   ▲ ZeroTier 10.75.181.203
```

| 调用方 | 走哪条路 | 说明 |
|---|---|---|
| 本机知惠（zhihui gateway）、`slm` CLI、hooks | `127.0.0.1:8765` | **与远程化之前完全一致**，零变化 |
| 远端任何 AI 工具 | `https://<地址>:8443/mcp/<agent>` | TLS + Bearer key，唯一对外通道 |
| dashboard、原始 HTTP API、hook 端点 | 仅回环 | 远程监听器上全部 404，不对外 |

远程监听器只服务三样东西：`/mcp`、`/mcp/<agent_id>`、`GET /health`。其余一切（dashboard、`/recall` 等原始 API、内部 hook 端点）在 8443 上不存在。

**服务器地址（证书已覆盖，任选其一）：**

| 地址 | 网络路径 | 备注 |
|---|---|---|
| `192.168.50.74` | 局域网（enp4s0，DHCP） | 同网段机器首选；IP 变了证书要重签 |
| `10.75.181.203` | ZeroTier | 跨网/外网机器走这条（ZeroTier 本身就是加密隧道） |
| `xukai-titan-gt77-12uhs`（或 `.local`） | 需 DNS/hosts/mDNS 解析 | 名字最稳，但依赖解析 |

---

## 2. 接入前的一次性准备（服务端操作）

### 2.1 拷贝 CA 证书到客户端

```bash
# 服务器上执行；ca.pem 是公开证书，不是秘密，可随意传输
scp ~/.superlocalmemory/remote/tls/ca.pem <user>@<客户端>:/目标路径/slm-ca.pem
```

CA 指纹（核对用，`sha256`）：`B9:ED:38:24:FC:9C:E2:3A:2B:BE:34:DC:90:F7:67:17:A5:C3:56:AF:07:86:F0:62:12:FC:AE:35:5F:1F:B1:2E`

### 2.2 发一把 key

先想清楚两件事（见 §4 语义）：

- **绑哪个 profile**：现有 `default` / `zhihui` / `doris`。每个远端工具一把独立 key；新 maid 就先 `slm profile create` 再发 key。
- **要不要写**：默认可读写；`--read-only` 只能 recall/查询。

```bash
SLM_DISABLE_WARMUP_SIDE_EFFECTS=1 ~/miniconda3/bin/slm remote keys add <名字> [--read-only] --profile <profile>
# key 只显示这一次（slmr_...），当场存进客户端的密钥管理，别进 URL、别进代码库
```

### 2.3 自检（服务器侧）

```bash
SLM_DISABLE_WARMUP_SIDE_EFFECTS=1 ~/miniconda3/bin/slm remote check   # 任何问题 exit 1
# 注意：本机 shell 带 mihomo 代理 env，手工 curl 探测必须加 --noproxy '*'（见 §7）
```

---

## 3. 客户端配方

所有配方的三要素：**URL**（`https://<地址>:8443/mcp/<agent_id>`）+ **`Authorization: Bearer <key>`** + **CA 证书**（`ssl_verify` / `cafile` / `NODE_EXTRA_CA_CERTS`）。`<agent_id>` 是自定义路径段（如 `hermes`、`dsh`、`claude`），只影响审计归因，不是认证。

### 3.1 Hermes（远端机器上的 hermes agent）

远端**不需要安装 mslm**，只需 hermes + SLM 的 hermes 插件（本仓 `hermes-plugin/` 树；通过 hermes 插件机制安装，装完默认 disabled → 审查 → enable，`mcp_allowlist` 只授 `superlocalmemory`）。

`~/.hermes/config.yaml`：

```yaml
mcp_servers:
  superlocalmemory:
    url: "https://192.168.50.74:8443/mcp/hermes"
    headers:
      Authorization: "Bearer ${SLM_REMOTE_KEY}"
    ssl_verify: "/path/to/slm-ca.pem"
plugins:
  entries:
    superlocalmemory:
      mcp_allowlist: ["superlocalmemory"]
      settings:
        connection: remote      # 精确值，唯一合法取值；插件所有调用走上面的 MCP 服务器
```

`SLM_REMOTE_KEY` 存 hermes 的 secrets 机制。行为要点（上游 `docs/hermes.md` §"Use an SLM that runs on another computer"）：

- recall / remember / list / delete / update / summary / trace / kinds / health 等全部可用；服务器管理类命令（`serve`/`backup`/`profile`/`remote` 等）只能在服务器上跑。
- 插件自己不开网络连接——地址、TLS、key 全部只存在于 hermes 配置里。
- **断连语义**：服务器不可达时 `/slm remember` 报 **NOT SAVED**、不本地缓存、事后不补投；自动捕获暂停 30 秒一轮。停机窗口里重要的内容要手动重存。
- read-only key：recall 正常，第一次写被拒后自动捕获关闭。

### 3.2 DSH（DeepSeek Harness）

DSH 原生支持 Streamable HTTP MCP（字段以 DSH `docs/config-catalog.md` 的 `StreamableHttpConfig` 为准）：

```json
{
  "transport": "streamable-http",
  "serverName": "mslm",
  "url": "https://192.168.50.74:8443/mcp/dsh",
  "headers": { "Authorization": "Bearer <slmr_...>" },
  "toolCallTimeoutMs": 30000,
  "failOnStartupError": false
}
```

CA 信任：DSH 是 Node 系，把 `slm-ca.pem` 喂给 `NODE_EXTRA_CA_CERTS`（或该机系统信任链）。配置文件的具体位置/格式以所用 DSH 版本的配置文档为准。

### 3.3 通用 HTTP MCP 客户端（Claude Code 等）

```json
"superlocalmemory": {
  "type": "http",
  "url": "https://192.168.50.74:8443/mcp/claude",
  "headers": { "Authorization": "Bearer <slmr_...>" }
}
```

CA 信任同样走 `NODE_EXTRA_CA_CERTS`（Node 系客户端）或对应的 `cafile` 设置。

### 3.4 stdio-only 客户端（mcp-remote 桥接）

```json
{
  "type": "stdio",
  "command": "npx",
  "args": ["-y", "mcp-remote", "https://192.168.50.74:8443/mcp/<agent>",
           "--header", "Authorization:${AUTH_HEADER}"],
  "env": {
    "AUTH_HEADER": "Bearer <slmr_...>",
    "NODE_EXTRA_CA_CERTS": "/path/to/slm-ca.pem"
  }
}
```

**永远不要**对别人的电脑用 `--allow-http`。

### 3.5 所有客户端的共同注意：绕开代理

客户端机器如果跑了系统代理（本生态常见 mihomo/clash），**必须让 SLM 主机直连**，否则表现为连接超时（本机启用当天实测的坑）：把 SLM 主机地址加进该机 `no_proxy`（含 ZeroTier IP），或在客户端配置里显式禁用代理。Python 系客户端（httpx 默认 `trust_env`）尤其会踩。

---

## 4. 密钥管理

| 操作 | 命令 |
|---|---|
| 发 key | `slm remote keys add <名字> [--read-only] --profile <profile>`（key 只显示一次） |
| 列 key | `slm remote keys list`（只显示名字/范围/profile/日期，永不显示秘密） |
| 吊销 | `slm remote keys revoke <名字或key_id>`（**下一请求即生效**，无需重启） |
| mesh/media 加成 | `slm remote keys allow <名字> mesh` / `media`（默认关，见 §5） |

语义要点：

- **一把 key 绑死一个 profile**。工具参数里出现其它 `profile_id`（包括结构化参数内层）一律拒绝 `remote_profile_not_allowed`，不会静默重定向。多 maid = 多 key，各绑各的 profile，天然隔离。
- **key 持有者能读该 profile 的全部记忆全文**（含记忆里写进去的路径、名字、贴过的报错）。给 key 前先想 profile 里有什么。
- **远程保存一律 `scope=personal`**：不写 scope 的保存会被归为 personal（不管服务器当前默认），`shared_with` 直接拒绝——共享操作只能在服务器本机做。recall 可以带 `include_shared` / `include_global`（看到的是别人共享给该 profile 的或全局的）。
- 写入检查与本机写入完全同一套（write key、角色、writer 自身 profile 检查），审计日志记 key 名 + profile + 工具 + 决定（不记参数与内容）。
- 远程建的 cache / 可逆压缩条目归属各自的 key，互不可见。
- **轮换流程**：先 `keys add` 新 key → 客户端换 `SLM_REMOTE_KEY` → `keys revoke` 旧 key。顺序别反，否则有断供窗口。

---

## 5. 远程面能做什么、不能做什么

工具面三档（完整清单见 `src/superlocalmemory/server/remote_tool_policy.py`）：

| 档位 | 代表工具 |
|---|---|
| read key 可用 | `recall` `search` `fetch` `expand` `list_recent` `get_memory_summary` `get_status` `health` `get_version` `slm_retrieve` 等 32 个 |
| write key 追加 | `remember` `update_memory` `delete_memory` `observe` `session_init` `close_session` `core_memory` `correct_pattern` `slm_compress` `slm_cache_set` 等 23 个 |
| **host-only（远程全拒，共 45 个）** | `forget` `run_maintenance` `switch_profile` `set_mode` `set_retention_policy` `backup_status` `compact_memories` 代码图谱族 `build_code_graph` `query_graph`… mesh 管理族；**不出现在远程 tools/list 里** |

协议层限制：远程只接受 `POST` 的 `initialize` / `ping` / `tools/list` / `tools/call`，其余 HTTP 方法 405；批量请求拒绝；请求体上限 1 MiB。限速与其它网络调用共享：**默认每机器每分钟 30 次写**（`SLM_RATE_LIMIT_WRITE` 可调，忙 agent 记得提）。

隐私：远程回包会隐去服务器本机信息（数据目录、home、账户名、pid、环境、报错里的路径）——远端看到的只有记忆内容本身。

mesh / media 工具是**双重 opt-in**：key 侧 `slm remote keys allow <名字> mesh|media` + 授权 scope，默认一律关。

**留在本机的东西**：SLM 自己的 hook 脚本只跟本机 daemon 说话（`SLM_HOOK_DAEMON_URL` 只收回环地址）；install token / hook token / daemon capability 在远程面永远不被接受。存量清理（§30 那类）、维护、备份、profile 管理都得登服务器做。

---

## 6. 服务端运维

### 6.1 重启 daemon 的正确序列（2026-10-24 修订版）

```bash
systemctl --user stop hermes-discord-watchdog.timer hermes-discord-line-watchdog.timer
systemctl --user stop hermes-discord-watchdog.service   # 关键补步：杀掉在跑的 watchdog 实例
systemctl --user stop hermes-gateway.service            # 铁律：daemon 停机窗前先停 gateway
systemctl --user restart superlocalmemory.service       # 必须 systemd 拉起（drop-in 生效）
# 验证：MainPID 换新 / /proc/<pid>/environ 有 HF_HUB_OFFLINE=1 与 SLM_RECALL_NO_SUPPORT_GATE=1 /
#       ss -tln 见 127.0.0.1:8765 + 0.0.0.0:8443 / curl http://127.0.0.1:8765/health
systemctl --user start hermes-gateway.service
systemctl --user start hermes-discord-watchdog.timer hermes-discord-line-watchdog.timer
```

> 为什么补了 watchdog service 那一步：10-24 实录——光停 timer 时恰逢 2 分钟周期触发，service 实例已在跑，它把刚停下的 gateway 又拉了起来（stop job 被 cancel），daemon 初始化窗内 gateway 短暂在线。当天窗口内零 MSLM 调用无损伤，但不能赌。

> 远端视角的停机窗：gateway 侧的锁死风险依旧由"先停 gateway"挡住；**远端客户端在停机窗只会 NOT SAVED**（不缓存不补投），重要写入要手动重存——通知远端使用者避开窗口。

### 6.2 证书生命周期

- 服务器证书 2027-11-25 到期（上限 397 天）；`slm remote check` 提前 30 天警告。
- 续期（同 CA 换叶子证书，客户端 ca.pem 不用动）：
  ```bash
  slm remote tls init --force --name xukai-titan-gt77-12uhs --name xukai-titan-gt77-12uhs.local \
      --ip 192.168.50.74 --ip 10.75.181.203 && <按 §6.1 重启>
  ```
- **加新地址/名字 ≠ 续期**：CA 的 NameConstraints 在建 CA 时定死，`--force` 改不了它。要加就得删 `remote/tls/` 里的 CA 四件套重新 init（客户端要重新分发 ca.pem）。所以当年一次签全了四个 SAN。
- LAN IP 是 DHCP：若 `192.168.50.74` 变了，优先用 ZeroTier IP 或主机名（证书仍有效）；要恢复 LAN IP 直连就得按上一条重签。

### 6.3 开关与配置位置

| 事项 | 位置/命令 |
|---|---|
| 监听配置 | `~/.superlocalmemory/remote/remote.json`（`{"listen": "0.0.0.0:8443"}`，0600） |
| 证书/私钥 | `~/.superlocalmemory/remote/tls/`（私钥必须 600，群/组可读 = 所有远程 key 被拒） |
| 临时改监听地址 | `slm remote enable --listen <IP:PORT>` + 重启 |
| 整体关闭远程访问 | `slm remote disable` + 重启（主 daemon 与本机链路不受影响） |
| 环境变量覆盖 | `SLM_REMOTE_LISTEN`、`SLM_REMOTE_TLS_CERT/KEY`（详见 `docs/distributed-deployment.md` 变量表） |

---

## 7. 排障速查

| 症状 | 第一嫌疑 | 处置 |
|---|---|---|
| 连接超时（http=000） | **客户端走了代理** | 该机 `no_proxy` 加 SLM 主机；服务器本机手工 curl 必加 `--noproxy '*'`（mihomo 教训） |
| 401 | key 缺失/吊销/未绑定 profile | `slm remote keys list` 核对；吊销是下一请求即生效 |
| `remote_profile_not_allowed` | 工具参数带了 key 之外的 profile_id | 改 key 的绑定 profile，或换对应 profile 的 key |
| TLS 验证失败 | 客户端没加载 ca.pem / 服务器证书重签过 / 连了证书外的地址 | 核对 `ssl_verify`/`NODE_EXTRA_CA_CERTS`；`curl --cacert .../ca.pem https://<地址>:8443/health` 复测 |
| 429 | 写限速（默认 30/分/机器） | `SLM_RATE_LIMIT_WRITE` 上调后重启 daemon |
| 503 MATERIALIZATION_INCOMPLETE | 投影未就绪（擦除/写入反压语义） | 稍候重试，属设计行为 |
| 远端 `/slm remember` 报 NOT SAVED | 服务器停机窗 / 网络断 | 检查 `GET /health`；窗口内的重要内容手动重存 |
| 手工探活 | — | `curl -s --noproxy '*' --cacert ~/.superlocalmemory/remote/tls/ca.pem https://192.168.50.74:8443/health` |

带 key 的最小 MCP 探针（服务器本机即可验全链路）：

```bash
curl -s --noproxy '*' --cacert ~/.superlocalmemory/remote/tls/ca.pem \
  -H "Authorization: Bearer <key>" -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -X POST https://192.168.50.74:8443/mcp/probe \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"probe","version":"0"}}}'
# 期望：result.serverInfo = SuperLocalMemory V4 / 4.2.6
```

---

## 8. 安全红线

1. **永远不要** `ssl_verify: false`，永远不要把 key 放 URL。key 泄露即刻 `keys revoke`（即时生效）。
2. 8443 虽是全接口绑定，但本机没有公网 IPv4，实际可达面 = 局域网 + ZeroTier。**不要**在路由器上给 8443 做端口映射；跨公网一律走 ZeroTier/VPN。
3. 不要给"顺手"的机器发 write key——key 即该 profile 的完整读权限 + 写权限，按需最小化（查询型用途给 `--read-only`）。
4. 远程面收不到的凭证（install token / hook token）不要试图从远端塞——设计上就不接受。

---

## 9. 边界与待办（2026-10-24 时点）

- **远端 hermes 未接入**：配方见 §3.1，真 key 待 profile 决策（新 maid 还是共写 zhihui）后发放。
- **远端跑 cron 会话的话，cron 守卫要移植**：fork 的四道守卫（on_memory_write / on_pre_compress / sync_turn / slm_remember，运维笔记 §30）只在**本机** zhihui 的 provider 里；上游插件（remote 模式用的就是它）没有守卫，远端 cron 样板会重新漏进远端 profile 的库。
- **DSH 尚未接入**（`~/.dsh` 零 mslm 引用）：接入即 §3.2 配方 + 一把 key 的事。
- **NAS 方案已评估后搁置**（本仓记忆档案有完整结论）：当前采用反向拓扑——daemon 留本机当服务器。若日后重启 NAS 方案，先看那份评估（fork provider 传输层是主要工程量）。
- mesh（`docs/multi-machine.md`）是另一回事：对等 peer 消息互联，各自独立库，不是本指南的远程方案。

---

*✍️ 主人侧（ZCode 执笔）· 2026-10-24 · 随运维笔记 §31 一同更新；未提交*
