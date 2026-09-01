# Ontology Runtime Kernel v0.1

> 一个足够小、结构稳定、可嵌入、与 Agent 天然兼容的**本体语义运行时内核**。
>
> 不是一个小型本体平台，而是 Platform 的运行时底座：
> **Platform 负责生产和治理语义；Kernel 负责运行语义。**

```text
Schema     定义世界
   ↓
State      保存世界
   ↓
Function   改变世界
   ↓
Change     描述世界发生了什么
```

统一运行入口只有五个 API：

```text
describe / get / find / related / invoke
```

---

## 1. 定位

Kernel 只负责五件事：

```text
1. 描述业务世界        (Schema)
2. 保存业务世界的状态  (State)
3. 描述业务能力        (Function)
4. 执行业务能力        (Execution)
5. 返回状态变化        (Change)
```

面向 `Agent Skill / CLI / MCP / Python / HTML / React / HTTP` 等一切接入方式。

## 2. 七条设计原则

| # | 原则 | 落地 |
|---|------|------|
| 1 | 语义优先 | Runtime 只操作 `Object / Relation / Function`，不暴露表、SQL、HTTP |
| 2 | Schema 与 State 分离 | Schema 默认不可变；State 可变；两者是不同的 Core 模块 |
| 3 | Function 是能力不是方法 | `FunctionDef` 只描述"能做什么"，实现由 Executor Binding 决定 |
| 4 | 状态修改必须经过 Runtime | `Function → ctx.set/link/unlink → Mutation → Validate → Apply → Change` |
| 5 | 外部协议都是 Adapter | CLI / MCP / HTTP 只做协议转换，语义同源（`serialize.py`） |
| 6 | Core 不依赖技术实现 | Core 零依赖，不知道 SQLite / YAML / Python / MCP / HTTP |
| 7 | 只抽象稳定的变化点 | 仅两个 Port：`StateStore` + `FunctionExecutor` |

## 3. 架构

```text
┌──────────────────────────────────────────────┐
│          Adapters: CLI / MCP / HTTP          │
└─────────────────────────┬────────────────────┘
                          ▼
┌──────────────────────────────────────────────┐
│   Runtime: describe/get/find/related/invoke  │
└─────────────────────────┬────────────────────┘
                          ▼
┌──────────────────────────────────────────────┐
│  Core: Schema / State / Execution / Change   │
└─────────────────────────┬────────────────────┘
              ┌───────────┴───────────┐
              ▼                       ▼
       StateStore Port         FunctionExecutor Port
         ├─ Memory (默认)         ├─ Python (默认)
         └─ SQLite                └─ builtin/http/wasm (未来)
```

依赖方向：`Adapter → Runtime → Core`；`Infrastructure → Ports`。
严格禁止 `Core → SQLite / Python / MCP / YAML`。

## 4. 快速开始

```bash
pip install -e ".[dev,mcp]"        # dev: pytest + pyyaml; mcp: MCP SDK
python examples/vehicle/demo.py    # 完整本体运行时闭环
python examples/agent/simple_agent.py   # Agent 五步闭环验证
python examples/mcp/smoke_test.py  # MCP 工具暴露验证
```

CLI：

```bash
ontology --schema examples/vehicle/ontology.yaml \
         --bindings examples/vehicle/bindings.yaml \
         --seed examples/vehicle/seed.json describe

ontology ... get Vehicle V001
ontology ... find Vehicle --filter balance__gt=50
ontology ... related Vehicle/V001 passes
ontology ... invoke Vehicle.recharge --target Vehicle/V001 --arg amount=100
```

HTTP Adapter（供 HTML/React 使用，`examples/html/index.html` 可直接打开）：

```bash
python -m ontology_runtime.adapters.http \
  --schema examples/vehicle/ontology.yaml \
  --bindings examples/vehicle/bindings.yaml \
  --seed examples/vehicle/seed.json --port 8000
```

MCP Adapter（供 Claude / Cursor 等 Agent 使用）：

```bash
ontology-mcp --schema examples/vehicle/ontology.yaml \
             --bindings examples/vehicle/bindings.yaml \
             --seed examples/vehicle/seed.json
```

## 5. Runtime API（冻结）

| API | 语义 | 备注 |
|-----|------|------|
| `describe()` | 本体索引：Object Types / Relations / Functions | 只返回索引级，避免塞爆上下文 |
| `describe_object/relation/function(name)` | 按需获取详细 Schema | Agent 第二跳 |
| `get(ObjectRef)` | 取单个对象 | `ObjectNotFound` |
| `find(type, **filters)` | Django 风格过滤：`balance__gt=100` | 不建 Query DSL |
| `related(ref, relation, direction)` | 关系导航，`out` / `in` | 返回 `list[ObjectRef]` |
| `invoke(function, target, args)` | 执行业务能力 | 返回 `FunctionResult` |

**写操作不是 Agent API**。`create/update/delete/link/unlink` 是 State Mutation
Primitive，只存在于 Function 内部（通过 `ctx`）。Agent 通过"业务能力"改变世界，
而不是直接操纵状态——这是 Ontology Runtime 与 ORM 的本质区别。

## 6. 执行管线（冻结）

```text
FunctionCall
  → Resolve FunctionDef        (FunctionNotFound)
  → Validate args              (InvalidArgument)
  → Resolve Target             (InvalidArgument / ObjectNotFound)
  → Build ExecutionContext     (只暴露 ctx，不暴露 Store)
  → Execute Function           (ctx.set / ctx.link / ctx.unlink 缓存 Mutation)
  → Collect Mutations
  → Validate against Schema    (SchemaViolation / RelationNotFound)
  → Apply atomically           (Store.apply，一次逻辑单元)
  → Generate Changes
  → FunctionResult
```

一次 Invocation 是一个原子单元：函数中途失败（业务错误、校验失败），
**所有 Mutation 都不会生效**。不做分布式事务 / Saga / Event Sourcing。

## 7. 领域模型（冻结）

| 类别 | 定义 |
|------|------|
| Object Identity | `ObjectRef(type, id)`，如 `Vehicle/V001`；id 永不全局 |
| Object | `ref + properties`；不鼓励直接改 dict |
| Property | ObjectType 内部结构，非独立运行时实体 |
| TypeDef | `string/number/integer/boolean/object/array/datetime` |
| Relation | `source + relation + target`；`(source, relation, target)` 唯一，重复 link 幂等 |
| Relation 属性 | 预留不实现（不实现 ≠ 架构封死） |
| Function | `FunctionDef(name, target_type, description, inputs)`，实现绑定分离 |
| FunctionCall | `function + target + args` |
| FunctionResult | `execution_id + output + changes + error`；**output ≠ changes** |
| Change | `set_property / link / unlink` |
| Mutation | 函数产生的待提交状态变化 |
| Schema | Immutable（load → resolve → validate → freeze） |
| State | Mutable，只含 Object + Relation |

错误模型：`KernelError`（即规格中的 RuntimeError，为避免遮蔽 Python 内置异常而改名）
→ `ObjectNotFound / RelationNotFound / FunctionNotFound / InvalidArgument /
SchemaViolation / FunctionExecutionError`，由 Adapter 转换为 MCP error /
HTTP 4xx/5xx / CLI exit code。

## 8. 最小 Ontology（examples/vehicle/ontology.yaml）

语义（ontology.yaml）与实现（bindings.yaml）分离：

```yaml
ontology: { name: traffic, version: 1 }
objects:
  Vehicle:
    properties: { plate: {type: string}, type: {type: string}, balance: {type: number} }
relations:
  passes: { source: Vehicle, target: TollStation }
functions:
  Vehicle.recharge:
    target: Vehicle
    description: 给车辆充值
    input: { amount: {type: number} }
```

```yaml
# bindings.yaml —— 实现绑定，不属于 Domain
bindings:
  Vehicle.recharge:
    executor: python
    entrypoint: functions/recharge.py
```

Function 只认识 `ctx`：

```python
def execute(ctx):
    vehicle = ctx.object()
    balance = vehicle.get("balance")
    ctx.set(vehicle.ref, "balance", balance + ctx.args["amount"])
    return {"success": True, "balance": balance + ctx.args["amount"]}
```

## 9. 代码规模（v0.1 实测）

| 模块 | 行数 | 设计预算 |
|------|-----:|---------|
| Core（schema/state/execution/refs/types/errors） | 415 | 300~500 |
| Runtime | 303 | 200~300 |
| MemoryStateStore | 128 | 100~200 |
| SQLiteStateStore | 190 | — |
| PythonFunctionExecutor | 109 | 100~200 |
| CLI / HTTP / MCP Adapter | 139 / 179 / 114 | 各 100~300 |

核心语义在 ~500 行内可完整理解。若 Core 超过 1000 行，警惕平台化。

## 10. v0.1 明确不做什么

```text
不做 Workflow / Agent Runtime / LLM / Reasoning Engine / Rule Engine
不做 RBAC / Policy Engine / Event Bus / Graph Database
不做 Data Lineage / Governance / Ontology Editor
不做 Hot Reload / Schema Migration / Distributed Transaction
不做 Query DSL / RepositoryFactory / FunctionRegistry / CapabilityManager
```

它们属于 Ontology Platform，不属于 Runtime Kernel。

## 11. 与完整平台的关系

```text
┌────────────────────────────────────────┐
│   中创本体智能平台                     │
│   Modeling / Data / Agent / Workflow  │
│   Reasoning / Governance / Policy     │
└──────────────────┬─────────────────────┘
                   │ uses
                   ▼
┌────────────────────────────────────────┐
│   Ontology Runtime Kernel (本仓库)     │
│   Schema / Object / Relation /        │
│   Function / State / Execution        │
└──────────────────┬─────────────────────┘
        ┌──────────┼──────────┐
        ▼          ▼          ▼
      Agent        MCP        CLI
       Skill       Tool      Demo/App
```

## 12. 与设计评审稿的差异记录

1. **`RuntimeError` → `KernelError`**：避免遮蔽 Python 内置异常，语义不变。
2. **Mutation 应用点**：规格 §25 让 Executor 返回 FunctionResult，§4/§20 要求
   状态修改必须经过 Runtime。落地为：Executor 调用 `context.apply_mutations()`
   （由 Runtime 注入 validate→apply 回调），保证 Store 只被 Runtime 触达，
   两个条款同时满足。
3. **`describe()` 分级**：索引级 + `describe_object/relation/function` 按需详查，
   与规格 §23 一致。

## 13. 目录

```text
src/ontology_runtime/
├── core/            schema.py state.py execution.py refs.py types.py errors.py
├── runtime/         runtime.py        # 五 API + 执行管线
├── ports/           state_store.py function_executor.py
├── infrastructure/  memory.py sqlite.py python_executor.py bindings.py filters.py
├── loader/          YAML/JSON → canonical model（Parse→Resolve→Validate→Freeze）
└── adapters/        cli.py http.py mcp.py serialize.py assembly.py
examples/
├── vehicle/         ontology.yaml bindings.yaml seed.json functions/ demo.py
├── agent/           simple_agent.py（Discover→Observe→Traverse→Act→Observe）
├── mcp/             smoke_test.py
└── html/            index.html（Ontology-driven UI）
tests/               66 个测试：类型/加载/双 Store/管线/原子性/CLI/HTTP
```
