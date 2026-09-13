# provenance 受控标注(provenance_kind)规格设计

- 日期:2026-09-13
- 状态:设计已批准(四节逐节确认),待 spec 审阅
- 需求书:`docs/deepmaid-provenance-需求书-2026-09-08.md`(R1–R6;deepmaid 侧消费见其 §5)
- 词表通用化决策:不带平台前缀;SLM 提供受控词表与读写面,deepmaid 业务语义在他们自己的映射层翻译
- 上游基线:已含 4.1.17 merge(eddac2e7);基线就绪

## 1. 背景与目标

deepmaid M3b 共享知识层治理(写入门槛/读出边界/策展修订三闸门)需要一个 SLM 侧数据模型前提:**fact 级的性质/治理标注**——一条 global 记忆是「世界事实」还是「历史遗产未分拣」,读面必须能区分。受控词表 `world` / `private` / `curated` / `legacy`,缺省 null(未标注)。

**目标**:`AtomicFact` 增 `provenance_kind` 受控标注(数据模型层),写入(remember 可选参数)、修订(update_memory 原位修订 scope/标注/profile_id)、读面(四读工具回显 + 策展扫描过滤)全链路支持。

**非目标**:写入门禁判定、策展规则、注入降权策略全在 deepmaid 侧(SLM 不背平台规则);不动既有 `provenance` 血缘表(来源追踪,与治理标注分层);不动模型工具面(host 侧管理面与数据模型需求)。

## 2. 已批准的关键决策

| 决策点 | 结论 |
|---|---|
| 字段命名 | **provenance_kind**(避开与 DB 既有 provenance 血缘表的同名混淆;治理语义与来源追踪明确分层) |
| 词表 | `world` / `private` / `curated` / `legacy`(通用语义,不带 maid- 前缀;可扩展,缺省 null) |
| 词表外取值 | **写入路径归 null**(宽容,未知=未分拣是最安全缺省);**扫描路径 400**(受控性) |
| 修订面形式 | **扩展 update_memory**(增可选 provenance_kind/scope/profile_id 参数;不新增 annotate_memory) |
| R5 过滤落点 | **daemon /list + MCP list_recent** 都增 scope/provenance_kind 过滤参数,含 `provenance_kind=null` 显式筛选 |
| scope 迁移 | M016 已开(DB 层 `_UPDATABLE_FACT_COLUMNS` 含 scope/shared_with);本 PR 把它暴露到 API 面 |

## 3. 数据模型与存储层

`AtomicFact` 加字段(放 scope 附近):

```python
provenance_kind: str | None = None   # 治理门禁标注,受控词表;None=未标注
```

词表常量与校验(单一权威源):

```python
PROVENANCE_KINDS: Final[frozenset[str]] = frozenset({
    "world", "private", "curated", "legacy",
})

def validate_provenance_kind(value: str | None) -> str | None:
    """写入路径校验:词表外归 null(需求书 R1 归 null 语义)。"""
    if value is None:
        return None
    v = value.strip().lower()
    return v if v in PROVENANCE_KINDS else None
```

存储:`atomic_facts` 加 `provenance_kind TEXT NULL` 列(新迁移 M052,additive IF NOT EXISTS);`_UPDATABLE_FACT_COLUMNS` 加 `provenance_kind`(与 scope/shared_with 同族)。DB 层不重复词表校验(校验在 engine/daemon 边界做,与 scope 处理一致)。存量事实全部 NULL,无 backfill。

## 4. 写入与修订面

**写入(R2)**:remember 增可选 `provenance_kind: str = ""`;缺省归 None(未标注),旧调用字节不变;词表外归 null;profile_id 穿透不动。

**修订(R3)**:update_memory 扩展可选 `provenance_kind?` / `scope?` / `profile_id: str = ""`:

- 选择性更新:只传提供的参数,空值不改;content 缺省时修订合法
- provenance_kind 可设可清(传空串/null 显式清标注)
- scope 迁移 personal ↔ global(shared_with 联动);scope 值受控(personal/shared/global)
- profile_id 穿透:带则只作用该 profile、不读不改全局档;delete_memory 同款增补;不带时维持 active-profile 约束

**不加新工具**:allowlist 域不变(扩的是既有 update_memory/delete_memory 参数面)。

## 5. 读面回显与策展扫描

**R4**:recall/search/fetch/list_recent 结果项统一增 `scope` + `provenance_kind` 两键(additive)。

**R5**:daemon `/list` 与 MCP `list_recent` 增 `scope` + `provenance_kind` 过滤参数:

| 参数 | 语义 |
|---|---|
| 不传 | 不过滤(全部) |
| `provenance_kind=world` | 只返 world 标注 |
| `provenance_kind=null` | **只返未标注**(NULL 列,策展扫描核心查询) |
| 词表外 | 400 参数错误 |

`scope` 过滤同理(personal/shared/global 受控,词表外 400)。DB 层 `get_all_facts` 加可选过滤,`engine.list_facts` 透传。

## 6. 测试与验收

1. 写入回环:remember 带 world → 三读工具回显 provenance_kind/scope
2. 词表约束:remember 词表外 → 落 null;扫描词表外 → 400
3. 原位修订:personal 迁 global 标 curated → 读回显;content 缺省合法;provenance_kind=null 清标
4. 穿透与隔离:带 profile_id 的修订/删除只作用该 profile,前后 /status 指针/generation 不变
5. 策展扫描:global 混合集(3 标注+2 未标注)→ scope=global+provenance_kind=null 恰返 2;provenance_kind=world 恰返 3
6. 迁移:M052 additive 可重入;存量 NULL
7. 兼容:不带新参数的旧调用字节不变

MCP stdio:SLM_MCP_TOOLS 含 update_memory 时全通。

并发:update_fact 的 tenant 约束并发不串库;scope 迁移不影响既有索引。

回归门:全量(4.1.17 基线)+ hermes 118 + per-request/list_recent 全部回归。

## 7. 上游 PR 打包

- 叙事:M016 已开 scope 迁移的库内能力,本 PR 把它暴露到读写面 + 增 provenance_kind 受控标注;词表通用化,SLM 不背平台规则。
- 附带:README 契约 12 红修复 + test_is_stale 日期炸弹 + 两个上游 bug 报告(test_hermes_plugin .venv 守卫 / enrich 守卫)可捆绑小 PR。
- 预期 review 焦点:provenance 与血缘表的命名区分理由;**scope 迁移对图缓存/向量的影响**(迁移后旧 scope 的邻接缓存行是否失效——实施时审计实体图/邻接缓存对 scope 变化的敏感性,这是真正的 review 地雷)。

## 8. 后续(不在本 spec 范围)

- deepmaid 侧消费(需求书 §5):写入门禁、读出降权、策展技能(知惠任策展人)
- per-profile ACL(独立需求)
