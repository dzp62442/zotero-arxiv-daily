# Discord 论坛集成实施计划

> 将 zotero-arxiv-daily 的邮件推送替换为 Discord 论坛帖子，并接入 OpenClaw agent 进行论文分类汇总与深入分析。

## 1. 架构概览

```
┌─────────────────────────┐
│   GitHub Actions        │
│   (每日 22:00 UTC)      │
│                         │
│   zotero-arxiv-daily    │
│   ├─ 抓取 arxiv 论文    │
│   ├─ Zotero 匹配排序    │
│   ├─ LLM 生成 TLDR     │
│   └─ 输出论文数据       │
└───────────┬─────────────┘
            │
            │ Discord Webhook POST
            │ (创建论坛帖子 + 追加消息)
            ▼
┌─────────────────────────┐
│   Discord Forum         │
│   #arxiv-daily          │
│                         │
│   帖子: 2026-02-21      │
│   ├─ 消息1: 论文 1-5    │
│   ├─ 消息2: 论文 6-10   │
│   ├─ ...                │
│   ├─ 消息4: 论文 16-20  │
│   └─ 消息5: 触发标记    │
└───────────┬─────────────┘
            │
            │ Discord Bot Gateway
            │ (allowBots=true)
            ▼
┌─────────────────────────┐
│   OpenClaw              │
│   (本地 Ubuntu 主机)    │
│                         │
│   research agent        │
│   (Sonnet)              │
│   ├─ 识别触发标记       │
│   ├─ 读取帖子全部消息   │
│   ├─ 按领域分类汇总     │
│   ├─ 重点方向深入分析   │
│   └─ 回复到帖子中       │
└─────────────────────────┘
```

### 关键设计决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 推送方式 | Discord Webhook | GitHub Actions 无需访问本地主机 |
| Agent 触发 | allowBots + 触发标记 | 最简单，Discord 作为天然中间层 |
| 消息聚合 | 打包发送 + END 标记 | 减少 agent 触发次数，避免逐条处理 |
| 上游同步 | 新增文件为主，最小化改动 | 降低与 upstream 的合并冲突 |

## 2. 代码改动

### 2.1 新增文件：`construct_discord.py`

职责：将论文列表转换为 Discord 格式并通过 webhook 发送。

```python
# construct_discord.py 核心结构

import requests
import datetime
from paper import ArxivPaper

PAPERS_PER_MESSAGE = 5  # 每条消息包含的论文数

def render_paper_embed(paper: ArxivPaper, index: int) -> dict:
    """将单篇论文转换为 Discord embed 格式"""
    # embed 结构:
    #   title: 序号 + 论文标题
    #   description: TLDR
    #   fields: 作者、机构、相关度、链接
    #   color: 根据相关度着色
    ...

def create_forum_post(webhook_url: str, papers: list[ArxivPaper]) -> str:
    """
    通过 webhook 在论坛创建新帖子并发送所有论文。
    返回 thread_id。
    
    流程:
    1. 第一条消息（创建帖子）: 日期标题 + 前 N 篇论文 embed
    2. 后续消息（追加到帖子）: 剩余论文 embed，每条 N 篇
    3. 最后一条消息: 触发标记 ARXIV_DAILY_COMPLETE
    """
    ...

def send_to_thread(webhook_url: str, thread_id: str, content: str, embeds: list = None):
    """向已有帖子追加消息"""
    requests.post(f"{webhook_url}?thread_id={thread_id}&wait=true", json={
        "content": content,
        "embeds": embeds or []
    })
```

**实现要点：**

- Webhook POST 需加 `?wait=true` 参数以获取响应中的 thread_id（响应 JSON 的 `channel_id` 字段）
- 创建帖子时用 `thread_name` 参数，后续追加用 `?thread_id=xxx`
- 每条消息之间加 `time.sleep(1)` 避免触发 Discord 速率限制（5 请求/2 秒）
- 星级评分用纯文本 emoji（⭐）替代原 HTML 版本
- 空论文列表时发送一条"今日无新论文"的帖子，不发触发标记
```

**Discord Embed 格式设计：**

每篇论文的 embed 结构：
```json
{
  "title": "1. Paper Title Here",
  "url": "https://arxiv.org/abs/xxxx.xxxxx",
  "description": "**TLDR:** One sentence summary of the paper.",
  "color": 3447003,
  "fields": [
    { "name": "Authors", "value": "Author1, Author2, ...", "inline": false },
    { "name": "Affiliations", "value": "MIT, Stanford, ...", "inline": false },
    { "name": "Relevance", "value": "⭐⭐⭐⭐", "inline": true },
    { "name": "Links", "value": "[PDF](url) | [Code](url)", "inline": true }
  ]
}
```

每篇 embed 约 300-500 字符。5 篇/消息 ≈ 1500-2500 字符（Discord 限制 6000，余量充足）。

**触发标记消息格式：**
```
📊 ARXIV_DAILY_COMPLETE | 2026-02-21 | 共 20 篇论文
```

### 2.2 修改文件：`main.py`

改动范围：仅在文件末尾的输出逻辑处添加分支，约 15 行。

```python
# main.py 末尾改动

# 新增参数
add_argument('--output', type=str, help='Output method: email or discord', default='email')
add_argument('--discord_webhook_url', type=str, help='Discord webhook URL for forum posting', default=None)

# ... 原有逻辑不变 ...

# 替换原来的固定邮件发送逻辑
if args.output == 'discord':
    assert args.discord_webhook_url is not None, "Discord webhook URL is required"
    from construct_discord import create_forum_post
    logger.info("Posting to Discord forum...")
    thread_id = create_forum_post(args.discord_webhook_url, papers)
    logger.success(f"Posted to Discord forum, thread_id: {thread_id}")
else:
    html = render_email(papers)
    logger.info("Sending email...")
    send_email(args.sender, args.receiver, args.sender_password,
               args.smtp_server, args.smtp_port, html)
    logger.success("Email sent successfully!")
```

### 2.3 文件改动清单

| 文件 | 操作 | 改动量 | 冲突风险 |
|------|------|--------|----------|
| `construct_discord.py` | 新增 | ~120 行 | 无（新文件） |
| `main.py` | 修改 | ~15 行（末尾分支） | 低 |
| `.github/workflows/main.yml` | 修改 | ~5 行（新增环境变量） | 低 |
| `docs/` | 新增 | 文档 | 无 |

## 3. GitHub Actions 配置

### 3.1 新增 Secrets

| Key | 说明 |
|-----|------|
| `DISCORD_WEBHOOK_URL` | Discord 论坛频道的 Webhook URL |
| `OUTPUT_METHOD` | `discord` 或 `email`（默认 `email`） |

### 3.2 Workflow 改动

`.github/workflows/main.yml` 的 `env` 部分新增：

```yaml
OUTPUT: ${{ vars.OUTPUT_METHOD }}
DISCORD_WEBHOOK_URL: ${{ secrets.DISCORD_WEBHOOK_URL }}
```

运行命令不变（`uv run main.py`），参数通过环境变量传入。

## 4. OpenClaw 配置

### 4.1 openclaw.json 改动

需要兰德审批后执行。

**改动 1：开启 allowBots**

```json
{
  "channels": {
    "discord": {
      "allowBots": true
    }
  }
}
```

**改动 2：绑定 research agent 到论坛频道**

在 `bindings` 数组中新增（放在现有 Discord bindings 之前）：

```json
{
  "agentId": "research",
  "match": {
    "channel": "discord",
    "peer": {
      "id": "1474617265159803094",
      "kind": "channel"
    }
  }
}
```

> 论坛频道 ID: `1474617265159803094`（#arxiv-daily）
> 论坛内的帖子（thread）通过 parent peer 继承路由到 research agent。

### 4.2 research Agent Workspace 配置

在 research agent 的 workspace（`~/.openclaw/workspace-research/`）中更新 AGENTS.md，添加论文分析指令：

```markdown
## arxiv-daily 论文分析

当收到 Discord #arxiv-daily 论坛中的消息时：

### 判断逻辑
- 如果消息不包含 `ARXIV_DAILY_COMPLETE`，回复 NO_REPLY
- 如果消息包含 `ARXIV_DAILY_COMPLETE`，执行以下分析流程

### 分析流程
1. 读取帖子中所有历史消息，提取论文信息
2. 按研究领域分类：
   - 🚗 自动驾驶
   - 🤖 机器人
   - 👁️ 计算机视觉（非自动驾驶/机器人）
   - 🧠 其他 AI/ML
3. 发送分类汇总到帖子中
4. 对「前馈高斯重建」和「视觉占用预测」方向的论文进行深入分析：
   - 核心贡献
   - 方法概述
   - 与现有工作的关系
   - 潜在应用价值
5. 如果需要更深入的分析，spawn sonnet 子 agent 处理

### 回复格式
使用 Discord 友好的 markdown 格式，不使用表格。
```

研究方向和课题细节不写在 AGENTS.md 中，而是逐步积累在 research agent 的 MEMORY.md 中，让 agent 通过日常交互自然地了解兰德的研究重点。

## 5. 与上游同步

直接在 main 分支上开发。上游有更新时手动拉取合并：

```bash
cd ~/Libraries/zotero-arxiv-daily
git pull upstream main  # 如果配置了 upstream remote
# 或手动从 GitHub 页面 Sync fork
```

新增的 `construct_discord.py` 不会冲突；`main.py` 改动集中在末尾，冲突概率低，有冲突时手动处理即可。

不使用 `REPOSITORY` 变量（会覆盖本地改动）。

## 6. 实施步骤

### Phase 1：Discord Webhook 发帖（预计 1-2 小时）

1. [ ] 在 Discord #arxiv-daily 论坛频道创建 Webhook，获取 URL
2. [ ] 编写 `construct_discord.py`
3. [ ] 修改 `main.py` 添加 `--output discord` 分支
4. [ ] 本地测试：`uv run main.py --debug --output discord --discord_webhook_url <URL>`
5. [ ] 验证论坛帖子格式和内容
6. [ ] 更新 GitHub Actions workflow，添加新的 secrets/variables
7. [ ] 手动触发 workflow 测试

### Phase 2：OpenClaw Agent 接入（预计 1 小时）

1. [ ] 提交 openclaw.json 改动方案给兰德审批
2. [ ] 审批通过后修改 openclaw.json（allowBots + binding）
3. [ ] 更新 research agent workspace 的 AGENTS.md
4. [ ] 重启 OpenClaw gateway
5. [ ] 发送测试帖子，验证 agent 触发和分析流程

### Phase 3：联调与优化（预计 0.5 小时）

1. [ ] 端到端测试：GitHub Actions → Discord → Agent 分析
2. [ ] 调整 embed 格式和论文信息展示
3. [ ] 调整 agent 分析深度和回复格式
4. [ ] 确认 research agent (Sonnet) 分析质量和回复格式

## 7. 风险与缓解

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|----------|
| Discord embed 字符超限 | 中 | 发帖失败 | 动态计算字符数，超限时拆分 |
| allowBots 导致意外触发 | 低 | agent 误响应 | 触发标记 + agent 指令过滤 |
| 上游大改 main.py | 低 | 合并冲突 | 改动最小化，集中在末尾 |
| Haiku 4.5 分析质量不足 | — | — | 已改用 research agent（Sonnet），分析能力充足 |
| Webhook URL 泄露 | 低 | 论坛被垃圾消息 | 存为 GitHub Secret，不提交到代码 |

## 8. 后续扩展

- **论文标签系统**：在论坛帖子中使用 Discord tag 标记论文类别
- **交互式筛选**：agent 提供按钮让用户选择感兴趣的论文深入分析
- **周报汇总**：每周自动生成论文周报
- **多人协作**：其他研究者也可以在帖子中讨论论文，agent 参与回答
