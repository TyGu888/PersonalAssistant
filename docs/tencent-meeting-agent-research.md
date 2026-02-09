# 腾讯会议 Agent 接入调研

> 结论：**官方没有提供「Bot 作为独立参会者入会」的开放 API**。可通过「会议管理 API + 客户端 SDK 用账号入会」或「扩展应用内机器人」做有限集成；若需要「一个程序以机器人身份进会听/说」，需用 SDK 以真人账号身份入会或考虑其他平台。

## 1. 官方开放能力概览

### 1.1 服务端 REST API（腾讯云文档）

- **会前**：创建/修改/取消会议、查询会议、获取入会链接、绑定扩展应用等。
- **会中**：投票、签到、直播、语音转写、会议控制等。
- **无「创建机器人参会者」或「Bot 入会」接口**：不能通过 REST API 让一个「机器人」作为独立参会者加入会议。

文档入口：[腾讯会议开放平台 - 服务端 API](https://cloud.tencent.com/document/product/1095/83678)

### 1.2 扩展应用与「拉取机器人」

- 绑定扩展应用：`POST https://api.meeting.qq.com/v1/app/toolkit`
- 在绑定应用的参数里有 **`enable_add_robot`**（应用是否可以拉取机器人），说明会议内扩展应用可以「拉取机器人」。
- 该能力是**扩展应用内**的机器人（在应用侧实现逻辑），而不是在会议参会者列表里多一个音视频参会者；扩展应用是否可拿到会中音视频流需单独查扩展应用开发文档。
- 会议开始后不能再绑定应用（错误码 190452）。

### 1.3 客户端 SDK（TencentMeetingSDK）

- 支持平台：Mac、Windows、iOS、Android、Linux、Electron、QT 等。
- 用途：把腾讯会议能力嵌到自有 App 里（类似 Zoom 的 in-app meeting），需要**客户端**（有或可无 UI）调用入会接口。
- 文档提到「Server 端参与的账号接入」需看鉴权与登录说明，一般指**服务端生成登录/鉴权信息，客户端用该账号入会**，而不是服务端自己作为一个无客户端的参会端。
- 若用 SDK 做「agent 开会」，可行做法是：用 SDK 集成一个客户端（如 Electron/无头或最小 UI），用**一个真人/企业账号**登录并调用「加入会议」，相当于 agent 占用该账号入会；需要企业账号体系和集成工作量。

### 1.4 腾讯会议自带「AI 能力」

- **AI 托管（元宝）**：用户授权后，元宝可替用户听会、多会议、生成纪要等。这是产品内置能力，**不开放给第三方 agent 接入**。
- **AI 小助手**：会前/会中/会后摘要等，同样为产品功能，非开放 API。

## 2. Agent 想「接入腾讯会议开会」的可行路径

| 方式 | 说明 | 限制 |
|------|------|------|
| **REST API 仅做会议管理** | 用开放平台 API 创建会议、拿入会链接、取消/修改会议等；agent 通过其他渠道把链接发给人，或配合日程。 | 不能以「机器人」身份入会，只能管会议。 |
| **客户端 SDK + 一个账号入会** | 用 TencentMeetingSDK（如 Electron/Linux）写一个客户端，用企业/个人账号登录并加入会议，agent 控制该客户端或与该客户端通信。 | 需要账号、集成 SDK、维护客户端；agent 实质是「用这个账号」入会，不是官方意义上的 Bot 参会者。 |
| **扩展应用 + enable_add_robot** | 开发腾讯会议扩展应用，在会议中绑定应用并开启「拉取机器人」，在应用内实现 bot 逻辑。 | 机器人是应用内能力，不是独立参会者；音视频能力需查扩展应用文档。 |
| **使用腾讯会议 AI 托管** | 用户自己在腾讯会议里开「AI 托管」让元宝听会。 | 无法接入自建 agent，只能用官方元宝。 |

## 3. 与「Bot 入会」能力对比（供参考）

- **Zoom**：有 Meeting SDK、Bot 等能力，部分场景支持程序化 bot 入会（需查当前 Zoom 文档）。
- **Meeting BaaS 等第三方**：提供跨平台（如 Zoom / Google Meet / Teams）的 Bot API，通过 meeting_url + bot_name 等让 bot 加入会议，**不包含腾讯会议**。
- **腾讯会议**：当前开放能力中**没有**类似「Bot Join API」或「Create bot participant」的接口，无法直接让自建 agent 以「一个会议机器人」身份进会。

## 4. 建议

- 若目标只是**用 agent 创建/管理腾讯会议（预约、改期、发链接）**：用腾讯会议开放平台 **REST API** 即可，可配合 [wemeet-openapi-sdk-python](https://github.com/tencentcloud/wemeet-openapi-sdk-python) 等。
- 若目标是**agent 作为「一个参会者」进腾讯会议听会/发言**：
  - **方案 A**：用 **TencentMeetingSDK** 做一个小客户端，以**一个账号**入会，agent 通过控制该客户端或与其通信来「开会」；需企业/账号与 SDK 集成。
  - **方案 B**：若必须「机器人以独立身份入会」且可接受换平台，可考虑支持 Bot 入会的会议产品（如 Zoom、或带 Bot API 的第三方服务）。
- 若目标是**在会中提供辅助能力（如纪要、问答）**：可评估**扩展应用 + enable_add_robot** 是否满足；具体音视频与数据能力需看腾讯会议扩展应用开发文档。

## 5. 参考链接

- [腾讯会议开放平台文档](https://cloud.tencent.com/document/product/1095/42406)
- [服务端 REST API 参考](https://cloud.tencent.com/document/product/1095/42414)
- [绑定扩展应用](https://cloud.tencent.com/document/product/1095/84398)
- [TencentMeetingSDK GitHub](https://github.com/Tencent-Meeting/TencentMeetingSDK)
- [腾讯会议 API 快速接入（云+社区）](https://cloud.tencent.com/developer/article/1604166)
