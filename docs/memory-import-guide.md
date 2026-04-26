# SLM 记忆导入指南：三层作用域批量导入

> SuperLocalMemory V3.4+ | Multi-Scope Memory Import Guide

本指南介绍如何将外部记忆服务（其他 Agent 记忆、知识库、笔记系统等）的记忆批量导入 SLM，并合理分配到三层作用域（personal / shared / global）。

---

## 导入前准备

### 1. 整理数据格式

将外部记忆整理为统一的 JSON 格式，每条记忆包含以下字段：

```json
{
  "content": "记忆内容文本",
  "scope": "personal|global|shared",
  "shared_with": ["agent_id_1", "agent_id_2"],
  "tags": "标签1,标签2",
  "agent_id": "来源 Agent 标识",
  "imported_from": "来源系统名称"
}
```

**字段说明**：

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `content` | 是 | 记忆内容文本，SLM 会自动提取原子事实 |
| `scope` | 否 | 作用域，默认 `personal` |
| `shared_with` | 否 | `scope=shared` 时指定共享的 Agent ID 列表 |
| `tags` | 否 | 逗号分隔的标签 |
| `agent_id` | 否 | 来源 Agent 标识，用于溯源 |
| `imported_from` | 否 | 来源系统名称（如 `mem0`、`langchain_memory`） |

### 2. 确定作用域分配策略

导入前需要决定每条记忆归属哪一层：

| 记忆类型 | 推荐 scope | 原因 |
| --- | --- | --- |
| 通用技术知识（React、Docker 用法等） | `global` | 所有 Agent 共享，避免重复 |
| 团队约定、项目规范 | `global` | 团队共识，全员可见 |
| 特定领域的协作知识 | `shared` | 仅相关 Agent 可见 |
| 个人偏好、私有项目信息 | `personal` | 仅自己可见 |

---

## 方式一：Python 脚本批量导入（推荐）

适合大量导入，可精确控制每条记忆的 scope。

### 基本脚本

```python
#!/usr/bin/env python3
"""批量导入记忆到 SLM，支持三层作用域分配。"""

import json
import sys
import time

# 方式 A：通过 SLM Python API 导入
from superlocalmemory.core.engine import MemoryEngine
from superlocalmemory.storage.database import DatabaseManager
from superlocalmemory.storage import schema
from pathlib import Path


def import_memories(data_file: str, profile_id: str = "default") -> None:
    """从 JSON 文件批量导入记忆。

    Args:
        data_file: 包含记忆数组的 JSON 文件路径。
        profile_id: 目标 profile。
    """
    with open(data_file) as f:
        data = json.load(f)

    memories = data if isinstance(data, list) else data.get("memories", [])
    if not memories:
        print("没有找到记忆数据")
        return

    # 初始化引擎
    engine = MemoryEngine(profile_id=profile_id)
    engine.initialize()

    imported = 0
    errors = 0

    for i, mem in enumerate(memories):
        content = mem.get("content", "").strip()
        if not content:
            continue

        scope = mem.get("scope", "personal")
        shared_with = mem.get("shared_with")
        if isinstance(shared_with, str):
            shared_with = [s.strip() for s in shared_with.split(",") if s.strip()]

        try:
            fact_ids = engine.store(
                content=content,
                scope=scope,
                shared_with=shared_with,
                metadata={
                    "tags": mem.get("tags", ""),
                    "agent_id": mem.get("agent_id", "import"),
                    "imported_from": mem.get("imported_from", "external"),
                },
            )
            imported += 1
            if (i + 1) % 50 == 0:
                print(f"  已导入 {i + 1}/{len(memories)} 条...")
        except Exception as e:
            errors += 1
            print(f"  导入失败 [{i}]: {e}")

    print(f"\n导入完成：{imported} 成功, {errors} 失败")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python import_memories.py <memories.json> [profile_id]")
        sys.exit(1)
    import_memories(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "default")
```

### 运行

```bash
python import_memories.py exported_memories.json zhihui
```

---

## 方式二：通过 MCP 工具逐条导入

适合 Hermes Agent 运行时动态导入。在 Hermes Agent 中直接调用 `remember` 工具。

### 单条导入

```
调用 remember：
  content = "React 18 引入了 useId Hook 用于生成唯一 ID"
  scope = "global"
  tags = "react,frontend,hooks"
```

### 批量导入（伪代码）

```python
import json

memories = json.load(open("exported_memories.json"))

for mem in memories:
    scope = mem.get("scope", "personal")
    shared_with = ",".join(mem.get("shared_with", []))

    # 通过 Hermes Agent 的 MCP 调用
    hermes.call_tool("remember", {
        "content": mem["content"],
        "scope": scope,
        "shared_with": shared_with,
        "tags": mem.get("tags", ""),
    })

    time.sleep(0.1)  # 避免 pending queue 过载
```

---

## 方式三：通过 Dashboard API 导入

适合通过 HTTP 接口批量导入。

### 启动 Dashboard

```bash
slm dashboard    # http://localhost:8765
```

### 准备 JSON 文件

```json
{
  "memories": [
    {
      "content": "团队约定：所有 API 响应使用 snake_case 命名",
      "tags": "api,convention"
    },
    {
      "content": "Kubernetes HPA 基于 CPU 使用率自动扩缩容",
      "tags": "k8s,devops"
    }
  ]
}
```

### 调用导入 API

```bash
curl -X POST http://localhost:8765/api/import \
  -F "file=@memories.json"
```

> **注意**：Dashboard API 的 `/api/import` 当前不直接支持 scope 参数。
> 导入的记忆默认为 `personal` scope。
> 如需指定 scope，请使用方式一（Python 脚本）或方式二（MCP remember）。

---

## 常见导入场景

### 场景一：从 Mem0 / LangChain Memory 迁移

```python
#!/usr/bin/env python3
"""从 Mem0 导出格式转换并导入 SLM。"""

import json
from superlocalmemory.core.engine import MemoryEngine

def import_from_mem0(mem0_export_file: str, profile_id: str = "default"):
    """Mem0 导出格式通常是 [{"id": ..., "memory": "内容", "metadata": {...}}]"""
    with open(mem0_export_file) as f:
        mem0_data = json.load(f)

    engine = MemoryEngine(profile_id=profile_id)
    engine.initialize()

    for item in mem0_data:
        content = item.get("memory", "")
        if not content:
            continue

        # Mem0 的记忆通常是通用的，分配到 global
        engine.store(
            content=content,
            scope="global",
            metadata={
                "tags": ",".join(item.get("metadata", {}).get("tags", [])),
                "imported_from": "mem0",
                "original_id": item.get("id", ""),
            },
        )

    print(f"从 Mem0 导入 {len(mem0_data)} 条记忆")

if __name__ == "__main__":
    import_from_mem0(sys.argv[1])
```

### 场景二：从 Markdown 笔记导入

```python
#!/usr/bin/env python3
"""从 Markdown 文件导入笔记到 SLM。"""

import re
from pathlib import Path
from superlocalmemory.core.engine import MemoryEngine

def import_markdown_notes(
    notes_dir: str,
    scope: str = "personal",
    profile_id: str = "default",
):
    """扫描目录下所有 .md 文件，按段落拆分后导入。"""
    engine = MemoryEngine(profile_id=profile_id)
    engine.initialize()

    md_files = list(Path(notes_dir).glob("**/*.md"))
    total = 0

    for md_file in md_files:
        text = md_file.read_text()

        # 去掉 YAML frontmatter
        text = re.sub(r'^---\n.*?\n---\n', '', text, flags=re.DOTALL)

        # 按段落拆分（连续空行分隔）
        paragraphs = [p.strip() for p in re.split(r'\n{2,}', text) if p.strip()]

        for para in paragraphs:
            if len(para) < 20:  # 跳过太短的片段
                continue
            engine.store(
                content=para,
                scope=scope,
                metadata={
                    "tags": "",
                    "imported_from": "markdown",
                    "source_file": str(md_file.relative_to(notes_dir)),
                },
            )
            total += 1

    print(f"从 {len(md_files)} 个 Markdown 文件导入 {total} 条记忆")
```

### 场景三：多 Agent 协作初始化

多个 Hermes Agent 同时上线时，预加载共享知识：

```python
#!/usr/bin/env python3
"""多 Agent 协作初始化：预加载共享知识到 global scope。"""

from superlocalmemory.core.engine import MemoryEngine

# 团队共享知识（所有 Agent 都应知道的信息）
TEAM_KNOWLEDGE = [
    "项目 Phoenix 使用 React 18 + TypeScript + Tailwind CSS",
    "API 网关部署在 Kong，后端是 Go microservices",
    "数据库使用 PostgreSQL 16，ORM 是 Prisma",
    "CI/CD 使用 GitHub Actions，部署到 AWS EKS",
    "代码仓库组织：monorepo，使用 Turborepo 管理",
    "团队编码规范：ESLint + Prettier，严格模式",
    "测试策略：Jest 单元测试 + Playwright E2E 测试",
    "发布流程：feature branch → PR review → merge to main → auto deploy",
]

def bootstrap_team_knowledge(profile_id: str = "default"):
    engine = MemoryEngine(profile_id=profile_id)
    engine.initialize()

    for knowledge in TEAM_KNOWLEDGE:
        engine.store(
            content=knowledge,
            scope="global",
            metadata={"tags": "team,convention", "imported_from": "bootstrap"},
        )

    print(f"已为 profile={profile_id} 加载 {len(TEAM_KNOWLEDGE)} 条团队共享知识")

# 为每个 Agent 初始化
for agent in ["zhihui", "xiaoming", "xiaohong"]:
    bootstrap_team_knowledge(agent)
```

---

## 导入后操作

导入完成后，建议运行以下命令优化记忆：

```bash
# 认知整合：去重、合并相似记忆
slm consolidate --cognitive

# 检查系统健康状态
slm health

# 查看导入结果
slm recall "导入的知识" --limit 5
```

---

## 注意事项

1. **去重**：SLM 的熵门控（entropy gate）会自动过滤高度相似的重复记忆，但仍建议导入前做基本去重
2. **速率控制**：批量导入时建议每 50 条暂停 0.5s，避免 pending queue 积压
3. **scope 选择**：不确定时默认用 `personal`，后续可通过 MCP 工具调整
4. **实体共享**：Phase 3 的全局实体机制意味着导入 "React" 相关知识时，所有 Agent 自动共享 React 实体——无需手动同步
5. **向后兼容**：导入的记忆如果未指定 scope，默认为 `personal`，不会影响其他 Agent
