# MSLM Hermes MemoryProvider

MSLM（SuperLocalMemory）作为 Hermes Agent 的原生 MemoryProvider，提供本地优先的 AI 记忆引擎。随 `mslm-memory` 包发布，安装即用。

## 快速开始

### 安装

```bash
pip install mslm-memory>=4.0.0
```

Provider 随包自动安装在 `src/superlocalmemory/integrations/hermes/`，无需额外 `pip install`。

### 启用

```bash
hermes memory setup
# 选择 superlocalmemory，按提示配置（默认全本地 Mode A 即可）
```

或在 `~/.hermes/config.yaml` 中直接配置：

```yaml
memory:
  provider: superlocalmemory
  superlocalmemory:
    mslm_profile: ""        # 空 = 自动使用 Hermes profile 名
    mode: "A"               # A: 完全本地 | B: 本地 Ollama | C: 云端 LLM
    include_global: true    # 检索时包含跨 profile 共享的事实
    include_shared: false   # 检索时包含 agent 间共享的事实
```

### 验证

启动 Hermes 后观察日志：

```
MSLM provider ready — profile=coder mode=A
```

或使用工具查询：

```
/slm_status  → 查看记忆库统计信息
/slm_recall "关于这个项目的偏好"  → 语义搜索
/slm_remember "用户偏好暗色主题"  → 显式记忆
```

## 工具参考

### `slm_recall(query, limit=10, fast=false)`

语义搜索本地记忆库。7 通道并行检索 + RRF 融合排序。

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| query | string | *required* | 自然语言搜索查询 |
| limit | integer | 10 | 最大返回数（上限 20） |
| fast | boolean | false | 跳过扩散激活（不推荐） |

返回格式：

```json
{
  "results": [{
    "fact_id": "a1b2c3...",
    "content": "用户偏好使用暗色主题",
    "score": 0.92,
    "confidence": 0.87,
    "channel_scores": {"semantic": 0.91, "bm25": 0.45}
  }],
  "count": 8,
  "query_type": "factual",
  "retrieval_time_ms": 234
}
```

### `slm_remember(content, scope="personal")`

显式存储信息到本地记忆库。自动完成实体提取、图谱构建、向量嵌入。Importance 由系统根据内容语义自动分配。

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| content | string | *required* | 要记住的信息，写成清晰的事实陈述 |
| scope | string | "personal" | "personal"（仅当前 profile 可见）或 "global"（跨 profile 共享） |

返回格式：

```json
// 成功
{"status": "stored", "fact_ids": ["a1b2c3..."], "message": "Stored 3 facts from your content."}

// 无新事实
{"status": "noop", "message": "No new facts extracted (content may be redundant)."}
```

### `slm_status()`

查看本地记忆库统计信息。

返回格式：

```json
{
  "profile": "coder",
  "mode": "A",
  "facts": {"total": 12847},
  "entities": 3840,
  "graph_edges": 12300,
  "db_size_mb": 856,
  "embedding_model": "nomic-ai/nomic-embed-text-v1.5",
  "embedding_dim": 768
}
```

## 自动行为（无需模型感知）

Provider 在每个 turn 前后自动执行：

| 阶段 | 行为 |
|------|------|
| **Turn 开始** | `prefetch()` 注入上轮后台预取的记忆上下文到 system prompt |
| **Turn 结束** | `sync_turn()` 后台线程将对话事实持久化到 MSLM |
| **Turn 结束后** | `queue_prefetch()` 后台预取下一轮可能需要的记忆 |
| **内置 memory 写入** | `on_memory_write()` 镜像到 MSLM |
| **上下文压缩前** | `on_pre_compress()` 将即将丢弃的最后 10 条消息摘要存入 MSLM |
| **Session 结束** | `on_session_end()` 调用 `close_session()` 生成时间摘要 |
| **Session 切换** | `on_session_switch()` 更新 session ID + 清空预取缓存 |

## Profile ↔ 作用域映射

```
Hermes profile "coder"   ──auto──►  MSLM profile = "coder"
Hermes profile "writer"  ──auto──►  MSLM profile = "writer"

可通过 config.yaml 中 memory.superlocalmemory.mslm_profile 覆盖
```

三层作用域：

| 作用域 | 存储时机 | 检索条件 |
|--------|---------|---------|
| **personal** | sync_turn / on_memory_write / 工具 remember 默认 | 始终参与检索 |
| **global** | 工具 remember scope="global" | include_global=true 时参与 |
| **shared** | 暂不开放给模型（多 agent mesh 高级场景） | include_shared=true 时参与 |

## 故障降级

| 场景 | 行为 |
|------|------|
| MSLM 未安装 | `is_available()` 返回 `False`，Hermes 不激活此 provider |
| 配置加载失败 | `initialize()` 静默返回，provider 不激活 |
| engine 初始化超时 (>30s) | 设置取消标志，释放引擎，provider 降级 |
| engine 运行时崩溃 | 所有方法静默返回，不抛异常到上层 |
| 工具调用异常 | 返回 `tool_error` JSON，不中断主流程 |
| cron/flush 上下文 | 跳过模型加载，避免污染用户数据 |

## 线程安全

- **读操作**（`recall`/`prefetch`）：WAL 模式下可并发
- **写操作**（`store`）：通过 `_write_lock` 串行化
- **sync_turn 竞态**：`_sync_turn_lock` 保护 `is_alive()` 检查和 `thread.start()`
- **缓存读写**：`_prefetch_lock` 保护 `_prefetch_cache`
- 上一轮写入未完成时跳过本轮（丢弃策略，不堆积队列）

## 性能特征

| 指标 | 典型值 |
|------|--------|
| engine 初始化 | 5–15s（加载 sentence-transformers） |
| 初始化超时 | 30s（超时后 provider 禁用） |
| prefetch 首次同步召回 | <8s |
| sync_turn 后台写入 | 不阻塞主线程 (<1ms) |
| 内存占用 | ~2GB（sentence-transformers 模型） |

## 已知限制 (v1)

- `slm_status` 直接访问 `engine.db`（v2 迁移到 `engine.get_status()` 封装 API）
- `slm_report_feedback` 暂不提供（v2 待 MSLM 暴露 Python API 后恢复）
- Speaker entities 硬编码 `scope="global"`（v2 支持 personal scope）
- `shared` scope 不向模型暴露（v2 多 agent mesh 场景）

## 兼容性

- Python: 3.11–3.14
- MSLM: >=4.0.0
- Hermes Agent: >=0.17.0（MemoryProvider ABC 接口）
- 数据层: SQLite WAL 模式，`~/.superlocalmemory/memory.db`
