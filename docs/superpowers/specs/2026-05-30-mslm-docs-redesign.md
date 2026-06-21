# MSLM 文档重构方案

> 2026-05-30 · 周六上午 brainstorming · 知惠 & 主人

## 核心决策

- MSLM 以 **「Multi-Scope Local Memory」独立品牌** 发布
- 底部标注 "powered by SLM"——给上游一句诚实的 credit，不要求上游承认发行版概念
- PyPI 和 npm 双包发布时，用户看到的是 MSLM 的文档
- **中英双语**：我们自己的文档（品牌层 + 运维层）需要中英双语版本
- **文件命名**：中文版放前面，`README-zh.md` 为首页，英文版 `README-en.md`
- **顶部双语链接**：每篇文档顶部放 `[中文](./xxx-zh.md) | [English](./xxx-en.md)` 切换链接，与主人知乎/博客/GitHub 三版分发的惯例一致

## 文档分层策略

| 层 | 策略 | 文档 |
|:--:|------|------|
| 🔴 品牌层 | **重写**——以 MSLM 身份说话 | README、getting-started、multi-scope-memory |
| 🟡 运维层 | **修订**——保留我们的内容，上游部分加引用 | hermes-agent-guide、configuration、memory-import-guide |
| ⚪ 技术层 | **不动**——归档到子目录 | cli-reference、api-reference、ARCHITECTURE、troubleshooting 等 |

## 目录结构

```
docs/
├── INDEX.md                    ← 🆕 索引主页（MSLM 视角）
├── getting-started.md          ← 🔴 重写
├── multi-scope-memory.md       ← 🔴 重写（原 multi-scope-memory-roadmap.md）
├── hermes-agent-guide.md       ← 🟡 修订
├── configuration.md            ← 🟡 修订
├── memory-import-guide.md      ← 🟡 修订
├── slm/                        ← 🆕 上游原装文档归档
│   ├── INDEX.md                ← 🆕 上游文档索引
│   ├── README.md               ← 完整保留
│   ├── ARCHITECTURE.md
│   ├── cli-reference.md
│   ├── api-reference.md
│   ├── mcp-tools.md
│   ├── compliance.md
│   ├── troubleshooting.md
│   ├── skill-evolution.md
│   ├── ...
│   └── upstream-contribution-strategy.md
└── ...
```

## 发布前待办

- [*] 清理 MagicMock 残留 + .gitignore ✅
- [*] 跑全量测试 ✅
- [ ] 按本方案重写/修订文档
- [ ] 更新根目录 README.md（MSLM 品牌化）
- [ ] 确认 pyproject.toml + package.json 版本号一致
- [ ] PyPI + npm 发布
