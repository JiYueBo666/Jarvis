# Agent-mini

一个本地终端编程智能体，通过自然语言与 LLM 交互，自动完成代码的阅读、搜索、编辑和执行。

## 架构概览

项目经过 v4 重构，采用清晰的分层架构，依赖方向严格向下：

```
                    cli.py / tui/
                         │
                     agent.py  ← 组合根
                    /   |   \   \
                   /    |    \   \
          engine/  context/  guard/  life/  trace/
           /  \       \       /      /      /
          /    \       \     /      /      /
    memory/  tools/  skills/  workers/  providers/
          \     \       \     /        /
           \     \       \   /        /
            └─────┴───┴───┴─────────┘
                      │
                    data/    ← 被所有层依赖
```

### 分层说明

| 层 | 职责 |
|---|------|
| **agent.py** | 极薄组合根（<100 行），组装所有子系统 |
| **engine/** | 唯一编排点，Model I/O、工具执行、turn 生命周期 |
| **guard/** | 安全门链：权限、策略、Profile、重复调用拦截、沙箱 |
| **context/** | 上下文装配与历史压缩，控制 prompt 预算 |
| **memory/** | 工作记忆与持久化长期记忆 |
| **life/** | 检查点、会话恢复、Plan 模式控制器 |
| **trace/** | 可观测基建：事件总线、Span、审计报告 |
| **data/** | 纯 dataclass，零业务逻辑，被所有层依赖 |
| **tools/** | 内置工具实现 |
| **providers/** | LLM Provider 适配层 |

## 核心特性

**6 个内置工具** — 文件读写（`read_file` / `write_file` / `patch_file`）、基于 Shell 的代码搜索与执行（`run_shell`）。

**多层安全防护** — 工具权限分级（多种 profile）、重复调用拦截（`guard/repetition.py`）、五级安全门链。

**工作记忆与持久记忆** — 跨轮次的紧凑工作记忆（任务摘要、最近文件、事件笔记）；基于主题的持久化长期记忆（`memory/` 层）。

**上下文窗口管理** — 自动将 prompt 控制在预算范围内，按优先级压缩各分区；历史对话智能压缩（`context/compact.py`）。

**会话与检查点** — 会话事件持久化为 JSONL；检查点记录运行身份与关键文件新鲜度，支持安全恢复会话（`life/` 层）。

**子智能体系统** — 可在后台线程中派生 Worker 子智能体，支持消息传递与中止控制（`workers/` 层）。

**技能系统** — 从内置定义和 `.jarvis/skills/` 目录自动发现技能（`skills/` 层）。

**Plan 模式** — 进入后智能体仅可读取文件和撰写计划文档，适合先规划再执行的工作流（`life/plan.py`）。

**可观测性** — 完整的 trace 事件流（`trace/` 层），支持审计、Span 追踪、运行报告生成。

## 快速开始

### 1. 配置环境变量

创建 `.env` 文件（参考 `.env.example`）：

```ini
API_KEY=your_api_key_here
BASE_URL=https://api.openai.com/v1
SPEED_MODEL=gpt-4o-mini
HIGH_MODEL=gpt-4o
```

### 2. 安装依赖

```bash
pip install -e .
```

### 3. 运行

```bash
python main.py
```

进入 `jarvis>` 交互式终端，输入自然语言指令即可。

### 内置命令

| 命令 | 说明 |
|------|------|
| `/exit` / `/quit` | 退出 |
| `/session` | 查看当前会话信息 |
| `/help` | 查看帮助 |

## 开发

### 项目结构

```
src/
├── agent.py              # 组合根（依赖注入）
├── cli.py                # REPL 入口（prompt_toolkit）
├── config.py             # 配置（pydantic-settings）
├── testing.py            # 测试工具
├── data/                 # 纯数据结构（dataclass）
│   ├── session.py
│   ├── memory.py
│   ├── task.py
│   ├── checkpoint.py
│   ├── trace.py
│   ├── identity.py
│   └── events.py
├── engine/               # 引擎层
│   ├── loop.py           # 主循环（唯一编排点）
│   ├── model.py          # 模型调用与输出解析
│   ├── tool.py           # 工具执行（纯函数）
│   └── executor.py       # ToolExecutor
├── guard/                # 安全约束
│   ├── permissions.py
│   ├── policy.py
│   ├── profiles.py
│   ├── repetition.py
│   └── sandbox/
├── context/              # 上下文治理
│   ├── manager.py
│   ├── compact.py
│   └── render.py
├── memory/               # 记忆系统
│   ├── store.py
│   ├── fresh.py
│   ├── recall.py
│   └── durable.py
├── life/                 # 生命周期
│   ├── checkpoint.py
│   ├── resume.py
│   └── plan.py
├── trace/                # 可观测基建
│   ├── bus.py
│   ├── span.py
│   ├── store.py
│   ├── consumers.py
│   └── report.py
├── tools/                # 内置工具
│   ├── base.py
│   ├── read_file.py
│   ├── write_file.py
│   ├── patch_file.py
│   └── run_shell.py
├── providers/            # LLM Provider
│   ├── base.py
│   ├── clients.py
│   └── errors.py
├── skills/               # 技能系统
│   ├── bundled.py
│   ├── discovery.py
│   └── runtime.py
├── workers/              # 子智能体
│   ├── executor.py
│   ├── manager.py
│   └── runtime.py
└── tui/                  # TUI 界面
```

### 依赖

- Python >= 3.11
- openai >= 2.38.0
- pydantic >= 2.13.4
- pydantic-settings >= 2.14.1
- prompt-toolkit >= 3.0.52
- rich >= 15.0.0
- typer >= 0.26.4
