# Agent-mini

一个本地终端编程智能体，通过自然语言与 LLM 交互，自动完成代码的阅读、搜索、编辑和执行。

## 快速开始

### 1. 配置

创建 `.env` 文件（参考 `.env.example`）：

```ini
API_KEY=sk-your_key_here
BASE_URL=https://api.deepseek.com/v1
SPEED_MODEL=deepseek-v4-flash       # 快速模型，用于简单对话和工具调用
HIGH_MODEL=deepseek-v4-pro     # 高能力模型，用于复杂推理和规划
```

支持任何 OpenAI 兼容 API，可替换为其他服务商：

| 服务商 | BASE_URL | SPEED_MODEL / HIGH_MODEL |
|-------|----------|-------------------------|
| **DeepSeek** | `https://api.deepseek.com/v1` | `deepseek-chat` / `deepseek-v4` |


### 2. 安装依赖

推荐使用虚拟环境安装：

```bash
# 创建虚拟环境（推荐）
python -m venv .venv
source .venv/bin/activate    # Linux/macOS
# 或 .venv\Scripts\activate  # Windows

# 安装依赖（含项目自身）
pip install -e .
```

或者直接使用 pip 安装（全局或当前环境）：

```bash
pip install -e .
```

### 3. 启动

```bash
python main.py
```

进入 `jarvis>` 交互式终端，直接输入需求即可。

### 4. 使用示例

```
jarvis> 列出当前目录下有多少个 Python 文件

  ── 模型调用 1 ──
  🧠 先看看目录结构...
  run_shell(command=find . -name "*.py" | wc -l)
  → 2339
  ├─ 输入 1438 tok | 输出  281 tok | 缓存 1408 tok (98%) | 费用 $0.0064

jarvis> 读取 src/engine/loop.py 的前 10 行

  ── 模型调用 1 ──
  read_file(path=src/engine/loop.py, start=1, end=10)
  → import time
    from src.context.manager import ContextManager
    ...
```

### 内置命令

| 命令 | 说明 |
|---|---|
| `/exit` / `/quit` | 退出 |
| `/session` | 查看当前会话信息 |
| `/sessions` | 列出所有本地会话 |
| `/resume` | 恢复上一个会话 |
| `/help` | 查看帮助 |

### 启动选项

```bash
python main.py              # 新建会话，ask 审批
python main.py auto         # 自动审批所有危险操作
python main.py never        # 拒绝所有危险操作
python main.py --resume     # 启动后自动恢复上一个会话
```

## 工具

| 工具 | 说明 | 安全等级 |
|---|---|---|
| `read_file` | 读取文件，支持行号范围 | 安全 |
| `write_file` | 创建或覆盖文件 | 危险 |
| `patch_file` | 精确替换第一个匹配文本（非正则） | 危险 |
| `run_shell` | 在工作目录执行 Shell 命令 | 危险 |

危险工具在 `ask` 审批模式下会向用户确认。

## 特性

- **工具调用** — 基于 OpenAI function calling 的原生工具执行
- **实时进度** — 模型思考 `🧠`、工具调用、结果输出逐步显示
- **Token 统计** — 每轮结束后显示输入/输出/cache 用量和估算费用
- **提示词缓存** — 自动标记稳定部分（系统提示、工具定义）为 cache_control
- **会话持久化** — 事件流水、运行记录自动写入 `.jarvis/sessions/`
- **历史压缩** — 跨轮对话超预算时自动压缩为结构化摘要
- **重复检测** — 相同工具+参数连续调用 / 写失败后未读文件即重试，自动拦截
- **模型重试** — 可恢复错误（限速、超时、5xx）自动回退重试
- **会话恢复** — 进程崩溃后通过 `/resume` 恢复上下文继续工作
- **审批控制** — 危险工具支持 ask/auto/never 三种策略

## 项目状态

项目处于可用状态。核心路径（模型调用 → 工具执行 → 结果返回 → 持久化）已打通。

### 已实现

`engine/` — 编排循环、模型客户端、工具执行器、重复检测、审批  
`context/` — 消息管理、跨轮历史、预算压缩、缓存标记  
`tools/` — 读文件、写文件、补丁、Shell  
`trace/` — 事件总线、JSONL 持久化、TaskState 记录  
`guard/` — 重复调用检测  
`data/` — TaskState、TouchedFile  
`cli/` — prompt_toolkit REPL、流式渲染、Token 统计、Spinner

### 空目录（规划中）

| 目录 | 预期内容 |
|---|---|
| `memory/` | 工作记忆、文件摘要、episodic notes、持久记忆 |
| `guard/permissions.py` | 权限校验、策略检查 |
| `guard/sandbox/` | Shell 沙箱隔离 |
| `life/` | 检查点、Plan 模式 |
| `skills/` | 技能系统 |
| `workers/` | 子智能体管理 |

## 依赖

- Python >= 3.11
- openai >= 2.38.0
- pydantic >= 2.13.4
- pydantic-settings >= 2.14.1
- prompt-toolkit >= 3.0.52
- rich >= 15.0.0
- typer >= 0.26.4

## 架构

```
                    cli.py
                       │
                    agent.py    ← 组合根
                   /    |   \
            engine/  context/  trace/  tools/
               │        │        │       │
               ├ loop.py  manager.py  bus.py  read_file.py
               ├ model.py compact.py  store.py write_file.py
               ├ executor.py          │    patch_file.py
               └ tool.py              │    run_shell.py
                                      └ data/task.py
```

详见 [note.md](note.md) 中的完整架构设计说明。
