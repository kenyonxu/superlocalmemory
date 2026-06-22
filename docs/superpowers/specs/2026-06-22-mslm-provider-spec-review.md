# MSLM MemoryProvider 设计规格审阅

> 审阅日期：2026-06-22
> 审阅者：CC (delegate_task)
> 规格版本：v1 (2026-06-02)

---

## 🔴 必须修（4 条）

### 1. 线程安全：`_write_lock` 只锁了 `store()` 的调用体，没锁线程创建本身

- 第 636-641 行：`sync_turn` 检查 `self._sync_thread.is_alive()` 后创建新线程，但检查和创建之间没有锁
- 如果两个 turn 连续快速触发，可能都判断 `is_alive()=False`，然后同时创建两个写线程，`_write_lock` 只能在 `store()` 内部串行，但两个线程仍然并发
- **建议**：把 `is_alive()` 检查和 `thread.start()` 也用锁保护，或改用 `threading.Lock` 做 turn 级互斥

### 2. `queue_prefetch` 后台线程可能和 `sync_turn` 并发读写同一 SQLite

- 第 591-593 行：`queue_prefetch` 启动后台线程做 `engine.recall()`，虽然读不冲突，但如果 `recall` 内部有写（比如更新访问时间戳、缓存统计），就会和 `_write_lock` 保护的写冲突
- MSLM 的 `recall` 是否纯读？规格说"读操作可以并发"，但没说 `recall` 是否触发写
- **建议**：确认 `engine.recall()` 是否纯读；如果有写，需要纳入锁管理或明确文档

### 3. `initialize()` 的 `init_thread.join(timeout=30)` 后 `is_alive()` 判断，但超时时未清理线程

- 第 550-552 行：超时后设 `self._engine = None`，但 `init_thread` 仍在后台运行（daemon=True），可能继续加载模型占用 2GB RAM
- 更糟的是，如果线程后续成功初始化，引擎对象被孤立，但模型已加载——内存泄漏
- **建议**：超时后显式 `join()` 或设置取消标志；或改用 `concurrent.futures` 的 cancel 机制

### 4. `slm_report_feedback` v1 实现路径未确定

- 第 396 行："v1 可先实现为调用 `slm mcp report_feedback` CLI"
- 但规格第 85 行明确说"不做 REST API 方案"，现在又要走 CLI 桥接，这是矛盾的
- CLI 调用有进程开销和序列化成本，且 `subprocess` 在超时/错误处理上很脆弱
- **建议**：要么 v1 砍掉 `slm_report_feedback`（只留 3 个工具），要么 MSLM 侧必须暴露 Python API

---

## 🟡 建议修（5 条）

### 5. `sync_turn` 的 `clean_user <= 3` 过滤太粗暴

- 第 617 行：3 字符阈值会漏掉很多有意义的信息，比如 `"no"`（否定回答）、`"fix"`（简短指令）
- ByteRover 的实现是 10 条消息摘要，这里是按字符过滤，策略不一致
- **建议**：改为语义过滤（如非空且非纯标点），或至少放宽到 10 字符

### 6. `on_pre_compress` 返回空字符串但注释说"不干扰 compression summary prompt"

- 第 283 行：返回 `""` 确实不干扰，但 `MemoryProvider` ABC 的 `on_pre_compress` 签名是 `-> str`，返回值会被怎么处理？
- 如果 Hermes 期望返回一个 summary 字符串注入 prompt，返回空意味着这个功能完全静默
- **建议**：确认 ABC 契约，如果返回值被忽略，应在文档中明确说明

### 7. `config_override.get("include_global", True)` 类型不安全

- 第 521 行：YAML 解析的布尔值可能是字符串 `"false"`，直接 `get(..., True)` 会把字符串 `"false"` 当 truthy
- 同样问题在 `include_shared`
- **建议**：显式做 `str(v).lower() in ("true", "1", "yes")` 转换

### 8. `system_prompt_block()` 内容未定义

- 第 207 行：只写了"返回静态说明文本（Status 行 + 4 个工具简介）"，但没有给出具体文本
- 这会影响模型对工具的理解和使用频率
- **建议**：补充 prompt 文本，特别是 `scope` 参数的使用说明

### 9. `engine.db` 直接暴露给 provider 查 status，破坏封装

- 第 351 行："通过 `engine.db`（DatabaseManager 公开属性）查询各类统计"
- 如果 MSLM 内部重构数据库 schema，provider 会崩溃
- **建议**：MSLM 侧提供 `engine.get_status()` 封装 API

---

## 🟢 可选（3 条）

### 10. `speaker="user"` 在 `sync_turn` 中把 assistant 内容也标记为 user

- 第 620 行：`combined = f"User: {clean_user}\nHermes: {clean_asst}"`，但 `speaker="user"`
- 虽然内容里有角色标签，但 MSLM 的 speaker 字段用于实体归属和时序分析
- **建议**：v2 考虑拆成两次 `store()`，或 MSLM 支持 `speaker="both"` 模式

### 11. `plugin.yaml` 的 `hooks: - on_session_end` 不完整

- 第 468 行：只声明了一个钩子，但实现里有 `on_memory_write`、`on_pre_compress`、`on_session_switch`
- 如果 Hermes 的 plugin 系统按声明加载钩子，未声明的可能不会触发
- **建议**：补充完整钩子声明

### 12. 缺少 `on_error` / `handle_tool_call` 错误处理细节

- 第 4.1 节生命周期图里有 `[模型调用，可能触发工具调用]`，但 `handle_tool_call` 的错误处理没有详细说明
- **建议**：补充工具调用异常时的降级行为
