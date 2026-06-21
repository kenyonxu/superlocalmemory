# SLM 上游文档索引

> 本目录包含 SuperLocalMemory (SLM) 上游原装技术文档，作为 MSLM 的技术参考层。
> MSLM (Multi-Scope Local Memory) 基于 SLM 构建，使用相同的底层引擎和 MCP 协议。

---

## 核心架构

- [ARCHITECTURE.md](ARCHITECTURE.md) — 系统架构设计与模块说明
- [api-reference.md](api-reference.md) — REST API 完整参考
- [cli-reference.md](cli-reference.md) — CLI 命令参考（含全局选项和子命令）
- [mcp-tools.md](mcp-tools.md) — MCP 工具完整参考（75 个工具）

## 运维与安全

- [compliance.md](compliance.md) — 合规性文档（EU AI Act、GDPR）
- [troubleshooting.md](troubleshooting.md) — 常见问题排查指南
- [errors.md](errors.md) — 错误码参考
- [DASHBOARD-COVERAGE.md](DASHBOARD-COVERAGE.md) — Dashboard 功能覆盖说明

## 高级功能

- [skill-evolution.md](skill-evolution.md) — 技能演化引擎（自动学习与优化）
- [auto-memory.md](auto-memory.md) — 自动记忆捕获机制
- [multi-machine.md](multi-machine.md) — 多机 Mesh 组网配置
- [cloud-backup.md](cloud-backup.md) — 云端备份指南
- [ide-setup.md](ide-setup.md) — IDE 集成配置（Cursor、Windsurf 等）

## 数据管理

- [migration-from-v2.md](migration-from-v2.md) — 从 V2 迁移指南
- [profiles.md](profiles.md) — 用户画像（Profile）管理

## 项目治理

- [upstream-contribution-strategy.md](upstream-contribution-strategy.md) — MSLM 与上游 SLM 的协作策略

## 基准测试

- [benchmarks/EVO-MEMORY.md](benchmarks/EVO-MEMORY.md) — Evo-Memory 基准测试
- [benchmarks/HOOK-COLDSTART.md](benchmarks/HOOK-COLDSTART.md) — Hook 冷启动基准

## V2 历史归档

- [v2-archive/](v2-archive/) — V2 版本文档归档（含架构、CLI、MCP 等）

## 诊断记录

- [hermes-agent-slmd-busy-diagnosis-2026-05-15.md](hermes-agent-slmd-busy-diagnosis-2026-05-15.md)
- [hermes-agent-slmd-fix-record-2026-05-15.md](hermes-agent-slmd-fix-record-2026-05-15.md)

---

*本目录文档保持上游原样，与 MSLM 品牌层文档互补。用户应优先查阅 MSLM 品牌文档（`docs/INDEX-zh.md`），将本目录作为技术细节参考。*
