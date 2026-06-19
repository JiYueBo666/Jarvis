# Agent-mini

一个本地终端编程智能体，通过自然语言与 LLM 交互，自动完成代码的阅读、搜索、编辑和执行。

## 快速开始

### 1. 配置

创建 `.env` 文件（参考 `.env.example`）：

```ini
API_KEY=sk-your_api_key_here
BASE_URL=https://api.deepseek.com/v1
SPEED_MODEL=deepseek-chat
HIGH_MODEL=deepseek-reasoner
```

支持任何 OpenAI 兼容 API：

| 服务商 | BASE_URL |
|-------|----------|
| **DeepSeek** | `https://api.deepseek.com/v1` |
| **OpenAI** | `https://api.openai.com/v1` |

### 2. 安装依赖

```bash
python -m venv .venv
source .venv/bin/activate    # Linux/macOS
# .venv\Scripts\activate     # Windows

pip install -e .
```

### 3. 启动

```bash
python main.py              # 新建会话，ask 审批
python main.py auto         # 自动审批所有危险操作
python main.py never        # 拒绝所有危险操作
```

### 4. 使用示例

```
╭─────────────────────────────────────────
> 列出当前目录下有多少个 Python 文件

╰── Enter · Alt+Enter · /help ────────────
  ▶ 运行 shell 命令
  ⚙ run_shell(command=find . -name "*.py" | wc -l)
  → 42
  ├─ 输入 1438 tok | 输出 281 tok | 缓存 1408 tok | 费用 $0.0064
```

## 内置工具

| 工具 | 说明 | 安全等级 |
|---|---|---|
| `read_file` | 读取文件，支持行号范围 | ✅ 安全 |
| `write_file` | 创建或覆盖文件 | ⚠️ 危险 |
| `patch_file` | 精确替换第一个匹配文本（非正则） | ⚠️ 危险 |
| `delete_file` | 删除文件或递归删除目录 | ⚠️ 危险 |
| `run_shell` | 在工作目录执行 Shell 命令 | ⚠️ 危险 |
| `glob` | 根据 glob 模式查找文件 | ✅ 安全 |
| `grep` | 在文件内容中搜索正则表达式 | ✅ 安全 |
| `list_dir` | 列出目录内容（大小、类型、嵌套层级） | ✅ 安全 |

危险工具在 `ask` 审批模式下向用户确认。审批时展示带 🛡️ 标识的交互式确认框，支持 Y（允许本次）/ a（允许本轮）/ n（拒绝）。

## 内置命令

| 命令 | 说明 |
|---|---|
| `/exit` / `/quit` | 退出 |
| `/help` | 查看帮助 |
| `/sessions` | 列出历史会话 |

## 核心数据流

```
┌──────────────────────────────────────────────────────────────┐
│                        用户输入                              │
│  bordered_prompt() → query string                            │
└─────────────────────┬────────────────────────────────────────┘
                      │
                      ▼
┌──────────────────────────────────────────────────────────────┐
│  AgentSession.prompt(query)                                  │
│   • 构建系统提示（Git 分支、工具列表、规则）                  │
│   • 创建 UserMessage 加入消息列表                             │
│   • 调用 Agent.prompt()                                      │
└─────────────────────┬────────────────────────────────────────┘
                      │
                      ▼
┌──────────────────────────────────────────────────────────────┐
│  Agent.prompt(messages)                                      │
│   • 设置 isStreaming = true                                  │
│   • 调用 Engine.run_stream()                                 │
└─────────────────────┬────────────────────────────────────────┘
                      │
                      ▼
┌──────────────────────────────────────────────────────────────┐
│  Engine.run_stream()           ◄──── 循环直到无工具调用       │
│   ┌─────────────────────┐                                    │
│   │ 1. TurnStart        │  ← 发射事件                        │
│   │ 2. 消息格式转换     │  ← internal → LLM API 格式         │
│   │ 3. ModelClient      │  ← 流式调用 LLM                    │
│   │    .stream_complete │                                    │
│   │    ├─ ThinkingContent → 🧠 思考面板                      │
│   │    ├─ TextContent    → 文本面板                          │
│   │    ├─ ToolCallContent → 工具调用                         │
│   │    └─ Usage          → Token 统计                        │
│   │ 4. MessageEnd        │  ← 发射事件                       │
│   └─────────────────────┘                                    │
                      │                                         │
                      ▼                                         │
              有工具调用？                                      │
                      │                                         │
               ┌──────┴──────┐                                  │
               ▼              ▼                                 │
          有工具调用       无工具调用                            │
               │              │                                 │
               ▼              ▼                                 │
        AgentSession       AgentEnd                             │
        ._before_tool_     ← 发射事件                           │
         call()                                                 │
               │              │                                 │
               ▼              │                                 │
        审批决策               │                                 │
        ask/auto/never        │                                 │
               │              │                                 │
         ┌─────┴─────┐       │                                 │
         ▼           ▼       │                                 │
       approved    denied    │                                 │
         │           │       │                                 │
         ▼           ▼       │                                 │
      Engine.run()  返回拒绝   │                                 │
      → ToolExecutionStart    │                                 │
      → 工具执行               │                                 │
      → ToolExecutionEnd      │                                 │
      → 结果加入消息列表       │                                 │
         │                    │                                 │
         └──── 回到循环 ──────┘                                 │
                      │                                         │
                      ▼                                         │
┌──────────────────────────────────────────────────────────────┐
│  AgentSession._handle_after()                                │
│   • 检查可重试错误 → 指数退避重试                            │
│   • 检查上下文超限 → 自动压缩后继续                          │
└─────────────────────┬────────────────────────────────────────┘
                      │
                      ▼
┌──────────────────────────────────────────────────────────────┐
│  渲染 & 统计                                                │
│   • 显示 Token 用量（输入/输出/缓存）                        │
│   • 估算费用                                                │
│   • 自动保存会话到 .jarvis/sessions/                         │
└──────────────────────────────────────────────────────────────┘
```

## 核心功能

### 双模型架构
- **SPEED_MODEL** — 快速模型，用于日常对话和简单工具调用
- **HIGH_MODEL** — 高能力模型，预留用于复杂推理和规划

### 流式交互
- **思考过程** — 🧠 灰色面板实时显示模型推理过程（支持 DeepSeek reasoning_content）
- **文本输出** — 模型回答流式逐步渲染
- **工具调用** — `⚙ tool_name(args)` 带颜色高亮显示，附操作意图说明
- **动态加载** — 模型推理中、工具执行中的实时 Spinner 动画

### 智能容错
- **自动重试** — 限速（429）、超时、5xx 等可恢复错误自动指数退避重试（最多 3 次）
- **上下文压缩** — 超出上下文窗口时自动将早期对话压缩为结构化摘要，保留关键事实、文件路径和代码引用
- **会话持久化** — 每轮对话自动保存到 `.jarvis/sessions/`，支持 `/sessions` 列出历史会话

### 工具安全
- **三层审批策略** — `ask`（逐次询问）/ `auto`（自动放行）/ `never`（全部拒绝）
- **只读自动放行** — `read_file`、`glob`、`grep`、`list_dir` 等只读工具免审批
- **意图记录** — 每次工具调用自动记录操作意图和执行结果

### 任务追踪
- 自动记录用户任务与工具调用步骤
- 追踪受影响文件列表
- 任务状态持久化到 `.jarvis/tasks/`

## 事件驱动架构

Agent-mini 基于事件驱动设计，核心生命周期事件通过订阅机制分发。`cli.py` 订阅事件流并实时渲染到终端。

| 事件 | 触发时机 |
|---|---|
| `AgentStart` / `AgentEnd` | Agent 运行开始/结束 |
| `TurnStart` / `TurnEnd` | 一轮 LLM 调用 + N 次工具调用 |
| `MessageStart` / `MessageUpdate` / `MessageEnd` | 消息流式生命周期 |
| `ToolExecutionStart` / `ToolExecutionEnd` | 工具执行前后 |
| `ApprovalRequired` | 危险工具等待用户审批 |
| `CompactionStart` / `CompactionEnd` | 上下文压缩前后 |
| `RetryStart` / `RetryEnd` | 自动重试前后 |

## 项目结构

```
main.py                        # 入口：启动 CLI
src/
├── cli.py                     # 带边框的 prompt_toolkit REPL，事件渲染，审批交互
├── config.py                  # pydantic-settings 配置管理（.env → Settings）
├── Agent/
│   └── agent.py               # Agent 状态管理，事件处理，before/after 工具回调
├── AgentSession/
│   └── agent_session.py       # 会话管理层：审批、重试、压缩、持久化
├── engine/
│   ├── loop.py                # 编排循环：LLM 调用 → 工具执行 → 事件发射
│   ├── model.py               # ModelClient：OpenAI 兼容流式客户端
│   └── tool.py                # Tool 基类：参数定义、OpenAI schema 生成
├── context/
│   ├── session_manager.py     # 会话持久化：JSON 序列化/反序列化
│   └── convert_to_llm.py      # 消息格式转换（内部格式 → LLM API 格式）
├── data/
│   ├── event.py               # 事件类型：Agent/Message/Tool/Compaction/Retry 生命周期
│   └── messages.py            # 消息模型：User/Assistant/ToolResult/CompactionSummary
├── tools/                     # 工具自动发现（基于 pkgutil）
│   ├── read_file.py
│   ├── write_file.py
│   ├── patch_file.py
│   ├── delete_file.py
│   ├── run_shell.py
│   ├── glob.py
│   ├── grep.py
│   ├── list_dir.py
│   └── base.py                # 工具结果模型
├── TaskManager/
│   └── task_manager.py        # 任务追踪：记录用户任务、工具步骤、受影响文件
└── providers/
    └── errors.py              # Provider 错误类型
```
