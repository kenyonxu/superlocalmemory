# SuperLocalMemory V3 快速上手指南

> 从安装到投入使用的完整指引，覆盖所有常见坑点。
> 基于 V3.4.45 实战经验编写。

---

## 1. 安装

```bash
pip install superlocalmemory
```

验证：

```bash
slm --version   # 应输出 3.4.45+
slm doctor      # 检查依赖完整性
```

---

## 2. 首次初始化

```bash
slm setup
```

交互式向导让你选择运行模式。推荐 **Mode A**（零云端，纯本地），适合绝大多数场景。

`slm setup` 会自动下载嵌入模型（~500MB），缓存于 `~/.cache/huggingface/`。如果下载失败，见下方[网络与代理](#5-网络与代理)。

初始化完成后，数据目录 `~/.superlocalmemory/` 包含：

| 文件 | 用途 |
|------|------|
| `config.json` | 全局配置（模式、profile、代理等） |
| `memory.db` | 主数据库（事实、实体、图边、嵌入） |
| `pending.db` | 异步记忆队列 |
| `learning.db` | 学习信号与演化数据 |

---

## 3. 启动守护进程

SLM 可以按需启动（每次冷启动 ~3 秒），但推荐后台常驻：

```bash
slm serve start     # 后台启动（端口 8765）
slm serve status    # 查看状态
slm serve stop      # 停止
slm restart         # 一键重启（杀僵尸 + 清理 + 启动 + 健康检查）
```

启动后验证：

```bash
curl -s http://127.0.0.1:8765/health | python3 -m json.tool
# {"status":"ok","pid":12345,"engine":"initialized","version":"3.4.45"}
```

### Daemon 内置能力

| 组件 | 功能 |
|------|------|
| Materializer | 每 2 秒轮询 pending.db，将异步记忆写入主库 |
| HealthMonitor | 监控内存/心跳/engine 存活/僵尸进程回收 |
| Maintenance | 每 30 分钟运行 Langevin/Fisher/Sheaf 维护 + 嵌入回填 + 图剪枝 |
| Embed worker | 独立子进程加载 ONNX 模型，生成嵌入向量 |
| Entity backfill | daemon 启动时自动为旧记忆补齐实体和图边 |

---

## 4. 接入 Hermes Agent（MCP）

### 4.1 MCP 配置

在 Hermes Agent 的 MCP 配置中添加：

```json
{
  "mcpServers": {
    "superlocalmemory": {
      "command": "slm",
      "args": ["mcp"],
      "env": {
        "SLM_MODE": "a"
      }
    }
  }
}
```

> **注意**：不推荐设置 `SLM_DATA_DIR`。SLM 默认使用 `~/.superlocalmemory/`，如果 MCP 和 daemon 使用不同的数据目录，记忆会写入两个数据库、互相不可见。

### 4.2 验证连接

在 Hermes Agent 中测试：

```
调用 remember 存储："测试记忆：我正在使用 Hermes Agent 集成 SLM"
调用 recall 搜索："集成 SLM"
```

返回正确结果即表示成功。

### 4.3 核心工具速查

| 工具 | 功能 | 关键参数 |
|------|------|---------|
| `remember` | 存储记忆 | `content`（必填）, `scope`, `tags`, `session_id` |
| `recall` | 语义检索 | `query`（必填）, `limit` |
| `search` | 关键词搜索 | `query` |
| `list_recent` | 最近记忆 | `limit` |
| `get_status` | 系统状态 | — |

三层作用域：

| `scope` | 可见范围 | 适用场景 |
|---------|---------|---------|
| `personal` | 仅自己 | 个人偏好、私密信息 |
| `global` | 所有 Agent | 通用知识、团队规范 |
| `shared` | 指定 Agent 列表 | 协作信息 |

---

## 5. 网络与代理

### 问题背景

Embed worker 子进程需要访问 `huggingface.co` 验证模型文件。在受限网络环境中，直连不可达，镜像站（如 `hf-mirror.com`）可能存在 SSL 兼容问题。SLM V3.4.45+ 支持通过 `config.json` 配置代理。

### 配置代理

```bash
slm config set proxy.http http://127.0.0.1:7890
slm config set proxy.https http://127.0.0.1:7890
```

配置持久化到 `~/.superlocalmemory/config.json`，embed worker 启动时自动读取。不需要 shell 环境变量。

### 调整 Embed Worker 超时

ONNX 模型首次冷加载可能需要 2-3 分钟。如需调大超时：

```bash
# 启动 daemon 时设置（单位秒，默认 180）
SLM_EMBED_RESPONSE_TIMEOUT=300 slm restart
```

### 验证 Embed Worker 状态

```bash
ps aux | grep embedding_worker
tail -f ~/.superlocalmemory/logs/daemon-error.log | grep -i embed
```

看到 `Embedding worker pre-warmed (ONNX model loaded)` 表示加载成功。

---

## 6. 日常维护

```bash
slm status          # 模式、profile、DB 大小
slm health          # 数学层健康（Fisher-Rao、Sheaf、Langevin）
slm list -n 20      # 最近 20 条记忆
slm recall "查询"   # 语义检索
slm dashboard       # Web 仪表盘（http://localhost:8765）
```

记忆整理：

```bash
slm consolidate --cognitive   # 去重合并
slm decay --execute           # 遗忘曲线衰减
slm entity list               # 查看实体
```

---

## 7. 数据目录管理

### 7.1 位置

默认 `~/.superlocalmemory/`。**不要**随意更改。

### 7.2 迁移

推荐用符号链接，而非 `SLM_DATA_DIR`：

```bash
slm serve stop
mv ~/.superlocalmemory /data/slm-data
ln -s /data/slm-data ~/.superlocalmemory
slm serve start
```

### 7.3 备份

```bash
slm serve stop
cp -r ~/.superlocalmemory ~/.superlocalmemory.bak.$(date +%Y%m%d)
slm serve start
```

---

## 8. 故障排查

| 现象 | 原因 | 解决 |
|------|------|------|
| Embed worker 频繁超时被 SIGKILL | 网络不可达或超时太短 | 配置代理 + `SLM_EMBED_RESPONSE_TIMEOUT=300` |
| 检索质量差 | 嵌入列为 NULL | 等待 maintenance 回填（30 分钟间隔），或重启 daemon 加快 |
| MCP 记忆"丢失" | 数据目录不一致 | 统一使用 `~/.superlocalmemory/`，不设 `SLM_DATA_DIR` |
| 知识图谱为空 | 旧记忆无实体 | daemon 重启时自动回溯填充（幂等，跨 profile） |
| Recall 不报错但无结果 | 嵌入未就绪，语义 channel 跳过 | 6/7 channel 正常工作，等 embed worker 就绪 |
| `slm doctor` 报错 | 依赖缺失 | `pip install superlocalmemory[dev]` |
| 端口 8765 被占用 | 旧 daemon 僵尸 uvicorn | `slm restart` 自动清理 |

### 关键日志位置

```bash
~/.superlocalmemory/logs/daemon.log        # 主日志
~/.superlocalmemory/logs/daemon-error.log  # 错误（embed worker 超时等）
~/.superlocalmemory/logs/daemon.json.log   # 结构化 JSON（HealthMonitor）
```

---

## 9. 推荐工作流

```bash
# 一次性配置
pip install superlocalmemory           # 1. 安装
slm setup                              # 2. 初始化（选 Mode A）
slm config set proxy.http ...         # 3. 代理（如需）
slm config set proxy.https ...
SLM_EMBED_RESPONSE_TIMEOUT=300 \       # 4. 启动 daemon
  slm serve start

# 日常
slm serve status                       # 确认在线
slm dashboard                          # 可选：Web 面板
```

之后日常只需 `slm serve start`，开机自启可配置 systemd service。
