# Discord 论坛集成开发文档

> 将 zotero-arxiv-daily 的论文推送扩展到 Discord 论坛频道，并接入 OpenClaw research agent 进行自动分类汇总与深入分析。

## 1. 系统架构

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
│   ├─ 主楼: 概要信息     │
│   ├─ 消息1: 论文 1-5    │
│   ├─ 消息2: 论文 6-10   │
│   ├─ ...                │
│   └─ 末尾: 触发标记     │
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
│   (Claude Sonnet)       │
│   ├─ 识别触发标记       │
│   ├─ 读取帖子全部消息   │
│   ├─ 按领域分类汇总     │
│   ├─ 重点方向深入分析   │
│   └─ 回复到帖子中       │
└─────────────────────────┘
```

### 设计决策

- **Discord 作为中间层**：GitHub Actions 在云端运行，OpenClaw 在本地主机运行且无公网 IP，两者无法直接通信。Discord 天然充当消息中间件。
- **Webhook 发帖**：GitHub Actions 通过 Discord Webhook API 创建论坛帖子，无需 bot token 或额外服务。
- **allowBots 触发**：OpenClaw 开启 `allowBots=true` 接收 webhook 发送的 bot 消息，通过触发标记 `ARXIV_DAILY_COMPLETE` 控制 agent 何时开始分析。
- **每条消息 5 篇论文**：Discord embed 限制 6000 字符/消息，每篇 embed 约 300-500 字符，5 篇约 1500-2500 字符，余量充足。
- **最小化代码改动**：新增独立文件 `construct_discord.py`，`main.py` 仅在末尾添加条件分支，降低与上游的合并冲突风险。

## 2. 代码结构

### 2.1 新增文件

#### `construct_discord.py`

核心模块，负责将论文列表转换为 Discord embed 格式并通过 webhook 发送。

**主要函数：**

| 函数 | 说明 |
|------|------|
| `get_stars_text(score)` | 将相关度分数转换为纯文本星级（⭐），逻辑与 `construct_email.get_stars` 一致 |
| `render_paper_embed(paper, index)` | 将单篇 `ArxivPaper` 转换为 Discord embed 字典 |
| `create_forum_post(webhook_url, papers)` | 主入口：创建论坛帖子、分批发送论文、发送触发标记 |
| `_post_webhook(webhook_url, payload)` | 底层 HTTP 请求，带重试和速率限制处理 |

**Embed 格式：**

```json
{
  "title": "1. Paper Title",
  "url": "https://arxiv.org/abs/xxxx.xxxxx",
  "description": "**TLDR:** Summary text.",
  "color": 3447003,
  "fields": [
    { "name": "Authors", "value": "Author1, Author2, ...", "inline": false },
    { "name": "Affiliations", "value": "MIT, Stanford, ...", "inline": false },
    { "name": "Relevance", "value": "⭐⭐⭐⭐", "inline": true },
    { "name": "Links", "value": "[PDF](url) | [Code](url)", "inline": true }
  ]
}
```

**颜色编码：**
- 🔴 红色 (`0xE74C3C`)：score ≥ 7.5（高相关度）
- 🟠 橙色 (`0xF39C12`)：score ≥ 6.5（中相关度）
- 🔵 蓝色 (`0x3498DB`)：score < 6.5（低相关度）

**发帖流程：**

1. 第一条 POST 请求（主楼）：携带 `thread_name` 参数创建论坛帖子，仅包含概要信息（论文总数、各相关度分布），不含论文 embed
2. 后续 POST 请求：使用 `?thread_id=xxx` 追加到同一帖子，每条包含 5 篇论文 embed
3. 最后一条：发送触发标记 `📊 ARXIV_DAILY_COMPLETE | 日期 | 共 N 篇论文`
4. 每条消息间隔 `time.sleep(1)` 避免触发 Discord 速率限制
5. 所有请求带 `?wait=true` 以获取响应中的 `channel_id`（即 thread_id）

主楼与后续消息分离的原因：Discord 论坛帖子的主楼（opener）和后续回复之间有视觉分隔，主楼适合放概要信息，论文详情从第二条消息开始更清晰。

**边界情况：**
- 空论文列表：发送"今日无新论文"帖子，不发触发标记
- 速率限制（429）：自动等待 `retry_after` 秒后重试，最多 3 次
- Cloudflare 拦截：所有请求携带 `User-Agent: ZoteroArxivDaily/1.0` header

### 2.2 修改文件

#### `main.py`

新增两个命令行参数（同时支持环境变量）：

```python
add_argument('--output', type=str, help='Output method: email or discord', default='email')
add_argument('--discord_webhook_url', type=str, help='Discord webhook URL', default=None)
```

末尾输出逻辑改为条件分支：

```python
if args.output == 'discord':
    from construct_discord import create_forum_post
    thread_id = create_forum_post(args.discord_webhook_url, papers)
else:
    html = render_email(papers)
    send_email(...)
```

#### `.github/workflows/main.yml` 和 `test.yml`

`env` 部分新增：

```yaml
OUTPUT: ${{ vars.OUTPUT_METHOD }}
DISCORD_WEBHOOK_URL: ${{ secrets.DISCORD_WEBHOOK_URL }}
```

### 2.3 文件改动清单

| 文件 | 操作 | 改动量 | 上游冲突风险 |
|------|------|--------|-------------|
| `construct_discord.py` | 新增 | ~140 行 | 无（新文件） |
| `main.py` | 修改 | ~15 行（末尾） | 低 |
| `.github/workflows/main.yml` | 修改 | +2 行 | 低 |
| `.github/workflows/test.yml` | 修改 | +2 行 | 低 |
| `README.md` | 修改 | +27 行（开头） | 低 |

## 3. GitHub Actions 配置

### Secrets

| Key | 说明 |
|-----|------|
| `DISCORD_WEBHOOK_URL` | Discord 论坛频道的 Webhook URL |

### Variables

| Key | 说明 |
|-----|------|
| `OUTPUT_METHOD` | `discord` 启用论坛输出；不设置或 `email` 保持邮件输出 |

Webhook URL 的获取方式：Discord 频道设置 → 集成 → Webhooks → 新建 Webhook → 复制 URL。

## 4. OpenClaw 配置

### 4.1 openclaw.json

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

在 `bindings` 数组中新增（放在现有 Discord bindings 之前以确保优先匹配）：

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

论坛频道 ID `1474617265159803094` 对应 #arxiv-daily。论坛内的帖子（thread）通过 parent peer 继承路由到 research agent。

### 4.2 research agent workspace

**AGENTS.md** 添加论文分析指令：

- 收到不含 `ARXIV_DAILY_COMPLETE` 的消息 → 回复 `NO_REPLY`
- 收到含 `ARXIV_DAILY_COMPLETE` 的消息 → 执行分析流程：
  1. 读取帖子历史消息，提取论文信息
  2. 按领域分类（🚗 自动驾驶、🤖 机器人、👁️ 计算机视觉、🧠 其他 AI/ML）
  3. 发送分类汇总
  4. 对 MEMORY.md 中记录的重点研究方向相关论文进行深入分析
  5. 使用 Discord 友好的 markdown 格式回复

**MEMORY.md** 记录重点研究方向（随时间积累更新）：

- 前馈高斯重建 (Feed-forward Gaussian Reconstruction)
- 视觉占用预测 (Visual Occupancy Prediction)

## 5. 与上游同步

直接在 main 分支开发。上游更新时：

```bash
# 方式 1：GitHub 页面 Sync fork
# 方式 2：命令行
git pull upstream main
```

不使用 `REPOSITORY` 变量（会覆盖本地改动）。新增的 `construct_discord.py` 不会冲突；`main.py` 改动集中在末尾，冲突概率低。

## 6. 风险与缓解

| 风险 | 概率 | 缓解措施 |
|------|------|----------|
| Discord embed 字符超限 | 中 | 每条消息仅 5 篇，远低于 6000 字符限制 |
| allowBots 导致意外触发 | 低 | 触发标记 + agent 指令过滤；私有服务器无其他 bot |
| 上游大改 main.py | 低 | 改动最小化，集中在末尾 |
| Webhook URL 泄露 | 低 | 存为 GitHub Secret，不提交到代码；.env 在 .gitignore 中 |
| Cloudflare 拦截 webhook 请求 | 中 | 所有请求携带 User-Agent header |
| Discord 速率限制 | 低 | 消息间 1 秒延迟 + 429 自动重试 |
