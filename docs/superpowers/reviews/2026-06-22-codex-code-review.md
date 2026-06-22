# Codex 代码审查：MSLM Hermes MemoryProvider

> 审查日期：2026-06-22 | 审查工具：Codex (GLM 5.2) | 审查范围：3491a6f..988c5b4 (6 commits)
> SPEC：docs/superpowers/specs/2026-06-02-mslm-hermes-memory-provider.md

---

## 🔴 必须修（2 条）

| # | 问题 | 位置 |
|---|------|------|
| M1 | `slm_remember` 绕过 `_write_lock`，违反 SPEC §7.4 串行化要求 | `__init__.py:768` |
| M2 | `_init_cancelled` 不复位，重新初始化静默失效 | `__init__.py:290/307` |

---

## 🟡 建议修（7 条）

| # | 维度 | 问题 |
|---|------|------|
| S1 | 线程安全 | `recall()` 实际含写操作，SPEC 假定"纯读"不成立 |
| S2 | 错误处理 | `sync_turn` 的 `sanitize_context` 未 try 保护 |
| S3 | 资源 | `on_memory_write`/`on_pre_compress` 线程可能堆积 |
| S4 | 性能 | `system_prompt_block` 每 turn 同步 COUNT 全表扫 |
| S5 | 错误处理 | Mode 导入 `ImportError` 未捕获 |
| S6 | ABC 合规 | `save_config()` 未实现，setup 写不进配置 |
| S7 | 测试 | 并发/重初始化/锁串行化场景未覆盖 |

---

## 🟢 可选（10 条）

死字段、未用参数、flaky sleep 测试、裸 SQL 静默等——详见完整报告。

---

## 优先级

M1 + M2 都是**一行级修复**，修完即可消除静默数据风险。S5/S2/S6 其次。S3/S4 视真实负载评估。

---

*Codex (GLM 5.2) 审查完成 · 沙箱只读无法落盘，知惠代存*
