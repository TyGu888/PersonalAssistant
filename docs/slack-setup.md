# Slack Channel 配置说明

本项目的 Slack 集成使用 **Socket Mode**（WebSocket），无需公网 HTTP 入口。

**必须完成三件事**：① App-Level Token（`connections:write`）② Bot OAuth Scopes ③ **Event Subscriptions**（Enable Events + 订阅 `message.channels` / `message.groups` / `message.im` / `app_mention`）。缺一不可，否则连得上也收不到消息。

## 需要的两个 Token

| 类型 | 环境变量 | 格式 | 用途 |
|------|----------|------|------|
| **Bot User OAuth Token** | `SLACK_BOT_TOKEN` | `xoxb-...` | 调用 Slack API（发消息、列频道、上传文件等） |
| **App-Level Token** | `SLACK_APP_TOKEN` | `xapp-...` | Socket Mode 连接（WebSocket） |

---

## 一、App-Level Token（xapp-...）

用于 Socket Mode，**必须先创建**。

1. 打开 [Slack API 应用列表](https://api.slack.com/apps) → 选择你的 App（或新建）。
2. 左侧 **Settings** → **Basic Information**。
3. 下滚到 **App-Level Tokens** → **Generate Token and Scopes**。
4. 名字随意（如 `socket-mode`），Scope 只勾选：
   - **`connections:write`**
5. 生成后复制以 `xapp-` 开头的 Token，即 **App Token**，用于 `SLACK_APP_TOKEN`。

---

## 二、Bot User OAuth Token（xoxb-...）及权限

Bot Token 需要在 OAuth & Permissions 里为 Bot 配置以下 **Bot Token Scopes**（根据 `channels/slack.py` 实际调用的 API 整理）：

### 必选 Scopes

| Scope | 用途（代码中的调用） |
|-------|----------------------|
| **`channels:read`** | `conversations_list` 列出公开频道 |
| **`channels:history`** | 接收公开频道中的 `message` / `app_mention` 事件 |
| **`groups:read`** | `conversations_list` 列出私有频道 |
| **`groups:history`** | 接收私有频道中的消息事件 |
| **`im:read`** | 列出/识别 DM |
| **`im:history`** | 接收 DM 中的 `message` 事件 |
| **`im:write`** | `conversations_open` 打开 DM、向用户发 DM |
| **`mpim:read`** | 多人群聊（如用到） |
| **`mpim:history`** | 多人群聊消息（如用到） |
| **`chat:write`** | `chat_postMessage` 在频道/线程/DM 发消息；工具 `slack_reply_in_thread` |
| **`files:write`** | `files_upload_v2` 上传附件 |
| **`reactions:write`** | 工具 `slack_add_reaction`（`reactions_add`） |
| **`pins:write`** | 工具 `slack_pin_message`（`pins_add`） |
| **`app_mentions:read`** | 接收 `app_mention`（在频道里 @Bot） |

若不需要多人群聊，可不勾选 `mpim:read` / `mpim:history`。

### 配置步骤

1. [Slack API](https://api.slack.com/apps) → 你的 App → **OAuth & Permissions**。
2. 下滚到 **Scopes** → **Bot Token Scopes**。
3. 按上表添加上述 Scope。
4. 保存后，回到 **Install App**（或 **OAuth & Permissions** 顶部）→ **Reinstall to Workspace**（因改了权限）。
5. 安装完成后，在 **OAuth & Permissions** 页复制 **Bot User OAuth Token**（`xoxb-...`），即 **Bot Token**，用于 `SLACK_BOT_TOKEN`。

### Event 订阅（必做，否则收不到消息）

使用 Socket Mode 时不需要填 Request URL（事件走 WebSocket），但**必须**在 **Event Subscriptions** 里启用并订阅事件：

- 打开 **Enable Events**。
- **Subscribe to bot events** 至少添加：
  - **`message.channels`** — 公开频道消息
  - **`message.groups`** — 私有频道消息
  - **`message.im`** — DM 消息
  - **`app_mention`** — 在频道中被 @

保存后如提示权限不足，按提示在 **OAuth & Permissions** 补上对应 Scope 再 Reinstall。

---

## 三、本地配置

1. **环境变量**（二选一或同时用）：
   ```bash
   export SLACK_BOT_TOKEN="xoxb-你的Bot Token"
   export SLACK_APP_TOKEN="xapp-你的App Token"
   ```

2. **config.yaml** 中已通过变量引用，无需改 key：
   ```yaml
   channels:
     slack:
       enabled: true   # 改为 true 启用
       bot_token: ${SLACK_BOT_TOKEN}
       app_token: ${SLACK_APP_TOKEN}
       allowed_users: []   # 留空表示不限制；填 Slack 用户 ID 如 ["U01234ABC"] 则仅这些用户可触发
   ```

3. 启用 Slack：将 `slack.enabled` 设为 `true`，然后重启 `python main.py start`。

---

## 四、小结

| 项目 | 内容 |
|------|------|
| **App-Level Token** | 仅需 **`connections:write`**，得到 `xapp-...` → `SLACK_APP_TOKEN` |
| **Bot Token Scopes** | `channels:read`, `channels:history`, `groups:read`, `groups:history`, `im:read`, `im:history`, `im:write`, `chat:write`, `files:write`, `reactions:write`, `pins:write`, `app_mentions:read`（可选 `mpim:read` / `mpim:history`） |
| **Bot Events** | `message.channels`, `message.groups`, `message.im`, `app_mention` |
| **配置** | 设好 `SLACK_BOT_TOKEN`、`SLACK_APP_TOKEN`，在 config 里 `enabled: true` 即可 |
