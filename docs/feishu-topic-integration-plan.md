# 飞书话题推送集成方案与专家评审

## 1. 背景与目标

### 1.1 背景
- 现有仓库已支持 `email` 与 `discord` 两种输出。
- 本次目标是新增 `feishu` 输出，与 `discord` 并列。
- 需求关键点：在飞书群里实现“主贴 + 跟帖”的话题式推送体验。

### 1.2 目标
- 在 `executor.output=feishu` 时，支持：
  - 发送“每日摘要主消息”（主贴）
  - 按批次发送论文详情为“跟帖消息”
  - 发送结束标记消息（可选）
- 与现有架构保持低耦合，减少未来与上游同步冲突。

### 1.3 非目标
- 本阶段不做事件驱动机器人（不接收用户消息）。
- 本阶段不做复杂交互卡片模板管理（先用文本/富文本稳定通路）。
- 不依赖 Feishu MCP 作为生产路径（MCP 当前能力集不覆盖消息发送链路）。

---

## 2. 路线选择结论

### 2.1 候选路线
- 路线 A：群自定义机器人 Webhook（`/bot/v2/hook/...`）
- 路线 B：飞书自建应用机器人 API（`im/v1/messages` + `.../reply`）

### 2.2 结论
- 采用路线 B（自建应用 API）。
- 理由：
  - 跟帖能力依赖 `POST /open-apis/im/v1/messages/{message_id}/reply` 且使用 `reply_in_thread=true`。
  - 需要 `message_id` 与 `tenant_access_token`。
  - 仅 Webhook 路线无法完整满足“主贴后跟帖到同话题”的控制需求。

---

## 3. 权限与授权设计

## 3.1 应用侧配置（一次性）
- 在飞书开放平台创建自建应用。
- 开启机器人能力。
- 申请消息发送相关权限（按官方文档要求至少开启一组可发送权限）。
- 发布应用版本，并确保应用可用范围覆盖实际操作者与目标群。
- 将机器人拉入目标群，并确认具备发言权限。

## 3.2 仓库侧配置（GitHub）
- Secrets（新增）：
  - `FEISHU_APP_ID`
  - `FEISHU_APP_SECRET`
  - `FEISHU_CHAT_ID`
- Variables（可选）：
  - `OUTPUT_METHOD=feishu`
- `CUSTOM_CONFIG` 中新增：
  - `executor.output: feishu`
  - `executor.feishu.reply_in_thread: true`（建议默认）

## 3.3 是否需要“事件与回调”
- 不需要（本需求是主动推送，不是被动接收消息触发）。
- 仅当未来要做“用户 @ 机器人后触发检索”才需要事件订阅与回调地址。

---

## 4. 技术方案（分阶段）

## 4.1 阶段 P0：最小验证（PoC）

目标：证明“API 鉴权 -> 主贴发送 -> 跟帖发送”在目标群可用。

### 实施步骤
1. 使用 `app_id + app_secret` 获取 `tenant_access_token`。
2. 调 `POST /open-apis/im/v1/messages?receive_id_type=chat_id` 发主贴，拿到 `message_id`。
3. 调 `POST /open-apis/im/v1/messages/{message_id}/reply` 发一条跟帖，设置 `reply_in_thread=true`。
4. 记录响应字段与日志（`message_id/thread_id/code/msg`）。

### 通过标准
- 主贴可见。
- 跟帖挂在目标话题下（不是新消息漂移到主时间线）。
- 接口无鉴权/权限类报错（如 230027/230035）。

### 失败判定与分流
- `230071`：群不支持话题回复，降级为 `reply_in_thread=false`，记录告警。
- `230072`：消息类型不支持话题回复，调整消息类型或降级。
- `230019`：话题不存在，检查 `message_id` 关联关系。

## 4.2 阶段 P1：仓库最小集成（MVP）

目标：接入主流程，跑通“每日摘要 + 分批跟帖”。

### 改动范围
- 新增：`src/zotero_arxiv_daily/construct_feishu.py`
- 修改：`src/zotero_arxiv_daily/executor.py`
- 修改：`config/base.yaml`
- 修改：`config/custom.yaml`（示例）
- 修改：`.github/workflows/main.yml`
- 修改：`.github/workflows/test.yml`

### MVP 能力
- `executor.output=feishu` 路由分发
- 主贴消息（summary）
- 分批跟帖（每批 N 篇，默认 5）
- 基础重试（429 / 5xx）、超时（15~20s）、退避
- 结束标记消息

### 验收标准
- `test.yml` 手动触发成功。
- 一次完整运行能稳定发出主贴+至少一条跟帖。
- 不影响 `email/discord` 既有路径。

## 4.3 阶段 P2：完整集成（Production）

目标：增强可维护性与容错，进入常规日跑。

### 增强项
- 错误码分类告警（230071/230072/230035/230027/429）
- 去重 `uuid`（reply 请求，1 小时窗口）
- 配置项完备化：
  - `executor.feishu.chat_id`
  - `executor.feishu.reply_in_thread`
  - `executor.feishu.batch_size`
  - `executor.feishu.send_complete_marker`
  - `executor.feishu.mode`（`text`/`post`）
- README 增加飞书配置章节与排障指南。

### 上线门槛
- 连续 3 次 workflow 手动触发成功。
- 1 次定时任务成功。
- 错误日志可定位，失败可重试或降级。

---

## 5. 实现设计细节

## 5.1 模块职责
- `construct_feishu.py`：
  - `get_tenant_access_token()`
  - `send_message(chat_id, content)`
  - `reply_message(message_id, content, reply_in_thread, uuid)`
  - `create_topic_and_replies(chat_id, papers)`

## 5.2 与现有 Executor 对齐
- 延续当前 `email/discord` 分支模式，新增 `feishu` 分支。
- `reranked_papers` 生成逻辑不改。
- 空结果策略：
  - 保持与 discord 一致：可配置是否发送“今日无新论文”。

## 5.3 限流与超时策略
- 单请求超时：`20s`
- 重试次数：`3`
- 退避：`1s -> 2s -> 4s`
- 每批间隔：`1s`（避免触发群机器人共享限频）

## 5.4 回滚策略
- 仅需切回 `OUTPUT_METHOD=email` 或 `discord`。
- Feishu 分支完全旁路，不阻塞其他输出通路。

---

## 6. 开发工作量评估

- P0 PoC：0.5 天
- P1 MVP：0.5~1 天
- P2 完整集成与文档：0.5~1 天
- 总计：1.5~2.5 天（含联调和排障）

---

## 7. 专家评审会（两轮）

> 采用角色：RiskGuardian（风控）、Skeptic（质疑者）、Pragmatist（务实派）、Innovator（激进派）

## 7.1 第一轮独立评审

### RiskGuardian
- 主要风险不是开发复杂度，而是群配置和权限边界不透明（可用范围、群发言权限、话题支持）。
- 建议将 `230071` 作为强提示错误并自动降级，避免任务整批失败。
- 建议上线前增加“dry-run only send opener+one reply”开关。

### Skeptic
- 质疑点：如果目标群不是话题能力群，业务价值会打折。
- 质疑点：若仍依赖富文本复杂结构，可能频繁踩消息体限制和审核规则。
- 建议 MVP 只用纯文本，稳定后再上富文本/卡片。

### Pragmatist
- 赞同分阶段：先验证 API 通路，再改主流程，避免一次性大改。
- 建议配置最小化，先用 3 个 secrets + 2 个 executor 项，不要早期过度参数化。
- 建议保留一键回滚到 `email/discord` 的路径。

### Innovator
- 建议后续可引入飞书卡片以提升可读性，但应放在 P2 之后。
- 建议保留“completion marker”便于后续自动化 agent 消费消息流。

## 7.2 作者回应与方案修订

### 接受
- 接受“先文本后卡片”的策略，MVP 不做复杂卡片。
- 接受 `230071/230072` 降级策略并固化日志分类。
- 接受 dry-run 验证步骤，作为 P0 出口条件之一。

### 反驳
- 对“先不上 completion marker”的建议不采纳。保留该标记有利于后续自动处理链路，成本低。

### 待验证
- 待验证目标群是否稳定支持 thread reply（以 P0 实测结论为准）。

## 7.3 第二轮交叉审阅

### RiskGuardian（复审）
- 认可修订，条件是将“降级行为”写入明确日志，避免静默失败。

### Skeptic（复审）
- 认可先文本 MVP，前提是将“群不支持话题”定义为可观测风险而非技术债。

### Pragmatist（复审）
- 通过。方案可在 1~2 天内交付并可回滚。

### Innovator（复审）
- 通过。建议在 P2 再加卡片，不阻塞上线。

## 7.4 评审结论

- 审议结果：**通过（有条件）**
- 通过条件：
  - 先完成 P0 最小验证并保留日志证据
  - MVP 默认文本消息
  - 明确 `230071/230072` 降级处理
  - 保留快速回滚开关（`OUTPUT_METHOD`）

### 风险评级变化

| 维度 | 第一轮 | 最终 | 说明 |
|---|---|---|---|
| 鉴权与权限 | 中 | 中 | 需严格按应用发布与可用范围配置 |
| 话题能力兼容 | 高 | 中 | 增加降级策略后可控 |
| 交付复杂度 | 中 | 低 | 分阶段实施降低失败面 |
| 回滚难度 | 低 | 低 | 输出路由可快速切换 |

---

## 8. 下一步执行建议

1. 先执行 P0：在目标群完成“主贴+1条跟帖”实测并截图/日志留档。
2. P0 通过后进入 P1：提交 `feishu` MVP 代码改动。
3. P1 通过后进入 P2：完善配置项、文档与错误码监控。

