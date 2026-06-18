# Agent-mini

一个本地终端编程智能体，通过自然语言与 LLM 交互，自动完成代码的阅读、搜索、编辑和执行。

## 快速开始

### 1. 配置

创建 `.env` 文件（参考 `.env.example`）：

```ini
API_KEY=sk-your_key_here
BASE_URL=https://api.deepseek.com/v1
SPEED_MODEL=deepseek-v4-flash       # 快速模型，用于简单对话和工具调用
HIGH_MODEL=deepseek-v4-pro          # 高能力模型，用于复杂推理和规划
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
python main.py
```

### 4. 使用示例

```
╭─────────────────────────────────────────
> 列出当前目录下有多少个 Python 文件

╰── Enter · Alt+Enter · /help ────────────
  ⚙ run_shell(command=find . -name "*.py" | wc -l)
  → 42
  ├─ 输入 1438 tok | 输出 281 tok | 缓存 1408 tok | 费用 $0.0064
```

### 内置命令

| 命令 | 说明 |
|---|---|
| `/exit` / `/quit` | 退出 |
| `/help` | 查看帮助 |

### 启动选项

```bash
python main.py              # 新建会话，ask 审批
python main.py auto         # 自动审批所有危险操作
python main.py never        # 拒绝所有危险操作
```

## 工具

| 工具 | 说明 | 安全等级 |
|---|---|---|
| `read_file` | 读取文件，支持行号范围 | 安全 |
| `write_file` | 创建或覆盖文件 | 危险 |
| `patch_file` | 精确替换第一个匹配文本（非正则） | 危险 |
| `run_shell` | 在工作目录执行 Shell 命令 | 危险 |

危险工具在 `ask` 审批模式下向用户确认。审批时展示带 🛡️ 标识的交互式确认框。

## 特性

### 架构特性
- **事件驱动架构** — Agent → Engine → ModelClient，事件通过订阅机制分发
- **异步兼容同步** — asyncio 事件循环运行在后台线程，queue.Queue 桥接同步 UI
- **工具自动发现** — `discover_tools()` 自动扫描 `src.tools` 包注册工具

### LLM 交互
- **工具调用** — 基于 OpenAI function calling 的原生工具执行，支持 DeepSeek reasoning_content
- **实时流式输出** — 思考过程（🧠 灰色）、文本、工具调用逐步渲染
- **Token 统计** — 每轮结束后显示输入/输出/cache 用量和估算费用

### 用户界面
- **带边框的输入框** — 仿 cc-mini 风格的完整终端宽度输入框，顶部 `╭─` 底部 `╰─` 边框
- **工具执行预览** — `⚙ tool_name(args)` 带颜色高亮的工具调用显示
- **审批确认框** — 🛡️ 标识的交互式审批流程，支持 Y（允许本次）/ a（允许本轮）/ n（拒绝）
- **实时 Spinner** — 模型推理中、工具执行中的动态加载动画
- **多行输入** — Alt+Enter 换行，Enter 提交

### 工程化
- **提示词缓存** — 自动标记稳定部分（系统提示、工具定义）为 cache_control
- **历史压缩** — 跨轮对话超预算时自动压缩为结构化摘要
- **重复检测** — 相同工具+参数连续调用 / 写失败后未读文件即重试，自动拦截
- **模型重试** — 可恢复错误（限速、超时、5xx）自动回退重试
- **会话持久化** — 事件流水、运行记录自动写入 `.jarvis/sessions/`
- **审批控制** — 危险工具支持 ask/auto/never 三种策略

## 架构

```
cli.py  ← 带边框 prompt_toolkit UI
   │
agent.py  ← 组合根：Agent + AgentSession
   │
engine/     context/     tools/       data/
loop.py     manager.py   __init__.py  event.py
model.py    compact.py   read_file.py messages.py
executor.py              write_file.py
tool.py                  patch_file.py
                         run_shell.py
```

### 核心数据流

```
用户输入 → bordered_prompt() → Agent.prompt()
  → Engine.run_stream() → ModelClient.stream_complete()
  → 流式事件 → cli.py 渲染
  → 工具调用 → before_tool_call 审批 → 执行 → 继续流
  → AgentEnd → Token 统计
```

### 项目结构

| 模块 | 职责 |
|---|---|
| `src/cli.py` | 带边框的 prompt_toolkit REPL，事件渲染，审批交互 |
| `src/AgentSession/` | 会话管理，事件桥接，审批钩子 |
| `src/Agent/` | Agent 状态管理，before_tool_call 审批回调 |
| `src/engine/` | 编排循环、模型客户端、工具执行器 |
| `src/context/` | 消息管理、跨轮历史、预算压缩、缓存标记 |
| `src/tools/` | 读文件、写文件、补丁、Shell，自动发现注册 |
| `src/data/` | 事件类型、消息块、Usage 等数据模型 |

## 依赖

- Python >= 3.11
- openai >= 2.38.0
- prompt-toolkit >= 3.0.52
- python-dotenv >= 1.0.0
