from openai import AsyncOpenAI
from core.types import ChatMessage, ToolResult
from tools.registry import registry
from utils.token_counter import TokenCounter
import asyncio
import base64
import json
import logging
import mimetypes
import os
import time
from typing import Optional, Union

logger = logging.getLogger(__name__)


class BaseAgent:
    def __init__(self, agent_id: str, system_prompt: str, llm_config: dict, skill_summaries: list[dict] = None):
        """
        初始化 Agent
        
        参数:
        - agent_id: Agent 标识 (如 "study_coach", "default")
        - system_prompt: 系统提示词
        - llm_config: {
            "api_key": "...", 
            "base_url": "...", 
            "model": "...", 
            "max_context_tokens": 8000,
            "extra_params": {...},  # 直接传给 API 的额外参数
            "features": {...}       # 需要代码处理的特性
          }
        - skill_summaries: Skill 摘要列表，格式 [{"name": "xxx", "description": "xxx", "path": "xxx"}, ...]
        """
        self.agent_id = agent_id
        self.system_prompt = system_prompt
        self.skill_summaries = skill_summaries
        self.model = llm_config.get("model", "gpt-4o")
        
        # Token 计数器和上下文限制
        self.token_counter = TokenCounter(self.model)
        self.max_context_tokens = llm_config.get("max_context_tokens", 8000)
        self.max_response_tokens = llm_config.get("max_response_tokens")
        self.max_iterations = llm_config.get("max_iterations", 20)
        self.llm_call_timeout = llm_config.get("llm_call_timeout", 120)
        self.llm_http_timeout = llm_config.get("llm_http_timeout") or self.llm_call_timeout
        self.llm_max_retries = llm_config.get("llm_max_retries", 2)
        
        # Provider 特定配置
        self.extra_params = llm_config.get("extra_params", {})
        self.features = llm_config.get("features", {})
        
        # 媒体格式：决定 content block 的构建方式
        # "openai" (默认): image_url + input_audio, 不支持 video
        # "volcengine": image_url + video_url, 不支持 audio
        self.media_format = llm_config.get("media_format", "openai")
        
        # 是否支持 Vision（image_url content block）
        # DeepSeek 等纯文本模型不支持，需设为 false
        self.supports_vision = llm_config.get("supports_vision", True)
        
        # 支持火山引擎等自定义 base_url
        client_kwargs = {"api_key": llm_config.get("api_key")}
        if llm_config.get("base_url"):
            client_kwargs["base_url"] = llm_config.get("base_url")
        
        # 超时与重试：来自 config（agent 或 profile 覆盖）
        client_kwargs["timeout"] = float(self.llm_http_timeout)
        client_kwargs["max_retries"] = self.llm_max_retries
        
        self.client = AsyncOpenAI(**client_kwargs)
    
    async def run(
        self, 
        user_text: str, 
        context: dict, 
        tools: list[dict], 
        tool_context: dict = None,
        images: list[str] = None,
        msg_context: dict = None,
        system_prompt_override: Optional[str] = None,
    ) -> str:
        """
        运行 Agent 处理用户消息
        
        输入:
        - user_text: 用户消息
        - context: {
            "history": list[ChatMessage],  # 最近对话历史
            "memories": list[str]          # 相关长期记忆
          }
        - tools: Tool schema 列表（OpenAI function calling 格式）
        - tool_context: Tool 执行时注入的上下文 {"engine": ..., "scheduler": ..., "memory": ...}
        - images: 图片列表（路径或 data URL）
        - msg_context: 消息上下文（世界信息）{
            "user_id": str,           # 发消息的用户 ID
            "channel": str,           # 消息来源渠道
            "timestamp": datetime,    # 消息时间
            "is_group": bool,         # 是否群聊
            "group_id": str,          # 群 ID（群聊时）
            "is_owner": bool,         # 是否 owner
            "raw": dict               # 渠道特有信息
          }
        
        输出: str（回复文本）
        
        流程:
        1. 构建 messages:
           - system: prompt + memories + 世界信息
           - history: 最近对话
           - user: 当前消息（可能包含图片）
        2. 检查 token 数，必要时压缩上下文
        3. 调用 LLM
        4. 如果有 tool_calls:
           a. 执行 tools（通过 registry.execute）
           b. 将结果追加到 messages
           c. 重新调用 LLM
           d. 循环直到无 tool_calls 或达到最大循环次数
        5. 返回最终回复
        """
        # 构建系统消息（子 Agent 可传入 system_prompt_override 使用 skill 全文）
        memories = context.get("memories", [])
        if system_prompt_override is not None:
            system_content = system_prompt_override
            if memories:
                system_content += "\n\n## 关于用户的记忆\n" + "\n".join(f"- {m}" for m in memories)
        else:
            system_content = self._build_system_message(memories, msg_context)
        
        # 构建消息列表
        history = context.get("history", [])
        messages = self._build_messages(system_content, history, user_text, images)
        
        # 检查并压缩上下文
        total_tokens = self.token_counter.count_messages(messages)
        if total_tokens > self.max_context_tokens:
            logger.info(
                f"上下文 token 数 ({total_tokens}) 超过限制 ({self.max_context_tokens})，开始压缩"
            )
            messages = self._compress_context(messages, self.max_context_tokens)
            compressed_tokens = self.token_counter.count_messages(messages)
            logger.info(f"上下文压缩完成: {total_tokens} -> {compressed_tokens} tokens")
        else:
            logger.debug(f"上下文 token 数: {total_tokens}/{self.max_context_tokens}")
        
        # 最大 tool_calls 循环次数
        # msg_context 可以 override（用于 system/wake 消息限制 iteration 数）
        max_iterations = (msg_context or {}).get("max_iterations") or self.max_iterations
        iteration = 0
        
        # 构建 LLM 调用参数
        llm_kwargs = {
            "model": self.model,
            "messages": messages,
            "tools": tools if tools else None,
            "tool_choice": "auto" if tools else None
        }
        if self.max_response_tokens:
            llm_kwargs["max_tokens"] = self.max_response_tokens
        
        # 合并 extra_params（如 reasoning_effort）
        if self.extra_params:
            llm_kwargs.update(self.extra_params)
        
        # 是否需要保留 reasoning_content（DeepSeek Reasoner）
        preserve_reasoning = self.features.get("preserve_reasoning_content", False)
        
        while iteration < max_iterations:
            # 调用 LLM（带总超时保护，超时时间来自 config.agent.llm_call_timeout）
            logger.info(f"LLM 调用开始 (iteration={iteration})")
            t0 = time.monotonic()
            try:
                response = await asyncio.wait_for(
                    self.client.chat.completions.create(**llm_kwargs),
                    timeout=self.llm_call_timeout
                )
            except asyncio.TimeoutError:
                elapsed = time.monotonic() - t0
                logger.error(f"LLM 调用总超时 ({elapsed:.1f}s > {self.llm_call_timeout}s)")
                return "抱歉，AI 响应超时，请稍后重试。"
            except Exception as e:
                elapsed = time.monotonic() - t0
                logger.error(f"LLM 调用异常 ({elapsed:.1f}s): {type(e).__name__}: {e}")
                return f"抱歉，AI 服务出错: {type(e).__name__}"
            
            elapsed = time.monotonic() - t0
            logger.info(f"LLM 调用完成 ({elapsed:.1f}s)")
            
            # 检查响应是否有效
            if response is None or not response.choices:
                logger.error(f"LLM 返回无效响应: response={response}")
                return "抱歉，AI 服务暂时无法响应，请稍后重试。"
            
            # 获取 assistant 消息
            assistant_message = response.choices[0].message
            
            # 直接 append 消息对象（官方推荐方式）
            # DeepSeek Reasoner 要求 tool call 循环中回传 reasoning_content，
            # 直接用消息对象可确保所有字段（content、reasoning_content、tool_calls）正确序列化
            # 见 https://api-docs.deepseek.com/zh-cn/guides/thinking_mode
            messages.append(assistant_message)
            
            # 如果没有 tool_calls，返回最终回复
            if not assistant_message.tool_calls:
                return assistant_message.content or ""
            
            # 执行 tool calls
            tool_messages = await self._execute_tool_calls(
                assistant_message.tool_calls,
                tool_context
            )
            
            # 将 tool 结果追加到 messages
            messages.extend(tool_messages)
            
            # 自动检测 tool result 中的媒体文件路径，塞给 LLM 查看
            media = self._extract_media_paths(tool_messages)
            # Vision 不支持时跳过图片注入（避免发送 image_url block 到纯文本模型）
            inject_images = media["images"] if self.supports_vision else None
            if inject_images or media["audio"] or media["video"]:
                messages.append(self._build_user_message(
                    "[系统: 以下是工具产生的媒体文件，请查看]",
                    images=inject_images or None,
                    audio=media["audio"] or None,
                    video=media["video"] or None,
                ))
            
            iteration += 1
        
        # 如果达到最大循环次数，返回最后一次的回复
        return assistant_message.content or "已达到最大 tool_calls 循环次数，请重试。"
    
    def _build_system_message(self, memories: list[str], msg_context: dict = None) -> str:
        """
        构建系统消息（合并 prompt、世界信息和记忆）
        
        格式:
        {system_prompt}
        
        ## 可用 Skills（按需加载）
        ...
        
        ## 当前消息上下文
        - 来源渠道: {channel}
        - 发送者: {user_id}
        - 时间: {timestamp}
        ...
        
        ## 关于用户的记忆
        - {memory1}
        - {memory2}
        ...
        
        ## 回复指南
        - 如果无需回复，输出 <NO_REPLY>
        """
        result = self.system_prompt
        
        # 添加 skill 清单
        if self.skill_summaries:
            result += "\n\n## 可用 Skills（按需加载）"
            result += "\n\n以下是你可以使用的 Skills。当任务需要某个 Skill 的专业指导时，使用 read_file 工具读取对应的 SKILL.md 文件获取详细说明。"
            result += "\n\n| Skill | 说明 | 文件路径 |"
            result += "\n|-------|------|----------|"
            for skill in self.skill_summaries:
                name = skill.get("name", "")
                description = skill.get("description", "")
                path = skill.get("path", "")
                result += f"\n| {name} | {description} | {path} |"
            result += "\n\n使用示例：read_file(\"skills/coding_assistant/SKILL.md\")"
        
        # 工作区与 state 目录说明（避免被误删）
        result += "\n\n## 工作区说明"
        result += "\n- 工作区路径为 data/workspace/，你通过 read_file/create_file 等传入的路径会默认落在此目录下。"
        result += "\n- **state/ 目录**：用于存储各任务/Agent 的持久化状态（如 state/pm.json 等），不是临时文件。"
        result += "\n- 当用户要求「清理乱七八糟的文件」「删除临时文件」等时，**不要删除 state/ 下的任何文件**，以免破坏 Skill/任务状态。"
        
        # 添加世界信息（消息上下文）
        if msg_context:
            result += "\n\n## 当前消息上下文"
            result += f"\n- 来源渠道: {msg_context.get('channel', 'unknown')}"
            result += f"\n- 发送者 ID: {msg_context.get('user_id', 'unknown')}"
            if msg_context.get('person_id') is not None:
                result += f"\n- 发送者 person_id: {msg_context.get('person_id')}"
            
            timestamp = msg_context.get('timestamp')
            if timestamp:
                result += f"\n- 发送时间: {timestamp.strftime('%Y-%m-%d %H:%M:%S UTC')}"
            
            is_group = msg_context.get('is_group', False)
            result += f"\n- 是否群聊: {'是' if is_group else '否'}"
            
            if is_group and msg_context.get('group_id'):
                result += f"\n- 群 ID: {msg_context.get('group_id')}"
            
            # Owner 标识
            is_owner = msg_context.get('is_owner', False)
            if is_owner:
                result += "\n- **此消息来自 owner（主人），请认真对待每一条消息**"
            else:
                result += "\n- 此消息来自非 owner 用户，可酌情区分对待"
            
            # 渠道特有信息 (Raw Context)
            # 直接展示所有 raw 字段，供 LLM 使用（如 channel_id, message_id 等）
            raw = msg_context.get('raw', {})
            if raw:
                channel = msg_context.get('channel', 'unknown')
                result += f"\n\n### {channel.capitalize()} Context (Raw)"
                for k, v in raw.items():
                    result += f"\n- {k}: {v}"

            # 用户上传的文件（非图片）
            attachments = msg_context.get("attachments", [])
            if attachments:
                result += "\n\n## 用户上传的文件"
                result += "\n以下文件已保存在工作区，可以用 read_file 读取或 run_command 在沙箱中处理："
                for path in attachments:
                    result += f"\n- {path}"
        
        # 添加记忆
        if memories:
            result += "\n\n## 关于用户的记忆"
            for memory in memories:
                result += f"\n- {memory}"
        
        # 添加可用渠道信息
        available_channels = msg_context.get("available_channels", []) if msg_context else []
        if available_channels:
            result += "\n\n## 可用渠道"
            result += f"\n你可以通过 send_message 工具向以下渠道发送消息: {', '.join(available_channels)}"
            result += "\n- 如果不指定 channel 和 user_id，默认回复当前对话"
            result += "\n- 指定 channel 和 user_id 可以向其他渠道/用户主动发消息"

        # 通讯录信息
        # - 普通消息：精简概要（渠道名 + 状态 + guild/chat 数量），节省 token
        # - 系统唤醒消息：完整详情（具体 channel_id、user_id 等），供主动发消息
        is_system_wake = msg_context.get("channel") == "system" if msg_context else False
        contacts = msg_context.get("contacts", {}) if msg_context else {}
        if contacts:
            if is_system_wake:
                # 完整通讯录
                result += "\n\n## 通讯录（可联系的渠道和目标）"
                for ch_name, ch_info in contacts.items():
                    status = ch_info.get("status", "unknown")
                    result += f"\n\n### {ch_name} ({status})"
                    for guild_id, guild in ch_info.get("guilds", {}).items():
                        guild_name = guild.get("name", guild_id)
                        result += f"\n- Guild: {guild_name} (id: {guild_id})"
                        for ch_id, ch_data in guild.get("channels", {}).items():
                            ch_n = ch_data.get("name", ch_id)
                            result += f"\n  - #{ch_n} (channel_id: {ch_id})"
                    for chat_id, chat in ch_info.get("chats", {}).items():
                        chat_name = chat.get("name", chat_id)
                        chat_type = chat.get("type", "")
                        result += f"\n- Chat: {chat_name} ({chat_type}, chat_id: {chat_id})"
                    for ch_id, ch_data in ch_info.get("channels", {}).items():
                        ch_n = ch_data.get("name", ch_id)
                        ch_type = ch_data.get("type", "")
                        result += f"\n- #{ch_n} ({ch_type}, channel_id: {ch_id})"
                    for g_id, g_data in ch_info.get("groups", {}).items():
                        g_name = g_data.get("name", g_id)
                        result += f"\n- Group: {g_name} (group_openid: {g_id})"
                    for uid, u_data in ch_info.get("dm_users", {}).items():
                        u_name = u_data.get("name", uid)
                        result += f"\n- DM: {u_name} (user_id: {uid})"
            else:
                # 精简概要（节省 token，但让 Agent 知道有哪些渠道可用）
                result += "\n\n## 已连接渠道概要"
                for ch_name, ch_info in contacts.items():
                    status = ch_info.get("status", "unknown")
                    parts = [f"{ch_name} ({status})"]
                    guilds = ch_info.get("guilds", {})
                    chats = ch_info.get("chats", {})
                    channels = ch_info.get("channels", {})
                    groups = ch_info.get("groups", {})
                    dm_users = ch_info.get("dm_users", {})
                    details = []
                    if guilds:
                        total_ch = sum(len(g.get("channels", {})) for g in guilds.values())
                        details.append(f"{len(guilds)} guild(s)/{total_ch} channel(s)")
                    if chats:
                        details.append(f"{len(chats)} chat(s)")
                    if channels:
                        details.append(f"{len(channels)} channel(s)")
                    if groups:
                        details.append(f"{len(groups)} group(s)")
                    if dm_users:
                        details.append(f"{len(dm_users)} DM user(s)")
                    if details:
                        parts.append(", ".join(details))
                    result += f"\n- {': '.join(parts)}"
                result += "\n- 使用 send_message 工具可向这些渠道发消息（需要 channel_id/user_id）"
        
        # 添加 NO_REPLY 机制说明
        result += "\n\n## 回复指南"
        result += "当对方用中文提问时，用中文回答，用英文提问时，用英语回答。"
        result += "\n- 当你认为这条消息无需回复时（例如用户只是闲聊的一部分、感谢语、或消息不是针对你的），直接输出 `<NO_REPLY>` 作为完整回复"
        result += "\n- 如果你已经通过工具发送了消息（如 send_message），则不需要再生成文本回复，直接输出 `<NO_REPLY>`"
        result += "\n- 如果需要正常回复，直接输出回复内容即可（不要包含 <NO_REPLY>）"
        
        return result
    
    def _build_messages(
        self, 
        system_content: str, 
        history: list[ChatMessage], 
        user_text: str,
        images: list[str] = None
    ) -> list[dict]:
        """
        构建 OpenAI messages 格式（支持多模态）
        
        输出: [
            {"role": "system", "content": "..."},
            {"role": "user", "content": "..."},
            {"role": "assistant", "content": "..."},
            ...
            {"role": "user", "content": [...]}  # 可能包含图片
        ]
        
        图片消息格式 (OpenAI Vision):
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "描述这张图片"},
                {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,..."}}
            ]
        }
        """
        messages = [
            {"role": "system", "content": system_content}
        ]
        
        # 添加历史消息（支持多模态）
        for msg in history:
            if msg.images:
                # Reconstruct multimodal message
                messages.append(self._build_user_message(msg.content, msg.images))
            else:
                messages.append({
                    "role": msg.role,
                    "content": msg.content
                })
        
        # 添加当前用户消息（支持图片）
        user_message = self._build_user_message(user_text, images)
        messages.append(user_message)
        
        return messages
    
    def _build_user_message(self, user_text: str, images: list[str] = None,
                            audio: list[str] = None, video: list[str] = None) -> dict:
        """
        构建用户消息（支持多模态：图片/音频/视频）
        
        参数:
        - user_text: 用户文本
        - images: 图片列表（路径或 data URL）
        - audio: 音频文件路径列表
        - video: 视频文件路径列表
        
        返回: OpenAI 消息格式（content 为 str 或 list[dict]）
        """
        has_media = images or audio or video
        if not has_media:
            return {"role": "user", "content": user_text}
        
        content = []
        if user_text:
            content.append({"type": "text", "text": user_text})
        
        has_non_text = False
        
        # 图片 — 所有 provider 都用 image_url
        for img in (images or []):
            block = self._build_image_block(img)
            if block:
                content.append(block)
                has_non_text = True
        
        # Vision 不支持时，为跳过的图片添加文本提示
        if images and not self.supports_vision:
            content.append({"type": "text", "text": f"[此消息包含{len(images)}张图片，当前模型不支持图片输入]"})
        
        # 音频
        for aud in (audio or []):
            block = self._build_audio_block(aud)
            if block:
                content.append(block)
                has_non_text = True
            else:
                content.append({"type": "text", "text": f"[音频文件: {aud}，当前模型不支持原生音频输入，可用 run_command 处理]"})
        
        # 视频
        for vid in (video or []):
            block = self._build_video_block(vid)
            if block:
                content.append(block)
                has_non_text = True
            else:
                content.append({"type": "text", "text": f"[视频文件: {vid}，当前模型不支持原生视频输入，可用 run_command 处理]"})
        
        if not content:
            return {"role": "user", "content": user_text or ""}
        
        # 仅剩文本 block 时，折叠为纯字符串（兼容不支持 content list 的 provider）
        if not has_non_text:
            text_parts = [b["text"] for b in content if b.get("type") == "text"]
            return {"role": "user", "content": "\n".join(text_parts)}
        
        return {"role": "user", "content": content}
    
    # ===== Media content block builders (provider-aware) =====
    
    def _build_image_block(self, image: str) -> Optional[dict]:
        """构建图片 content block（所有 provider 通用 image_url 格式）"""
        if not self.supports_vision:
            return None
        url = self._process_image_for_message(image)
        if url:
            return {"type": "image_url", "image_url": {"url": url}}
        return None
    
    def _build_audio_block(self, audio_path: str) -> Optional[dict]:
        """
        构建音频 content block。
        
        OpenAI: {"type": "input_audio", "input_audio": {"data": base64, "format": "wav"}}
        其他 provider 不支持时返回 None（调用方会插入文本提示）。
        """
        if self.media_format == "openai":
            data = self._file_to_base64(audio_path)
            if data is None:
                return None
            ext = os.path.splitext(audio_path)[1].lower().lstrip(".")
            # OpenAI input_audio 支持 wav / mp3
            fmt = ext if ext in ("wav", "mp3") else "wav"
            return {"type": "input_audio", "input_audio": {"data": data, "format": fmt}}
        return None
    
    def _build_video_block(self, video_path: str) -> Optional[dict]:
        """
        构建视频 content block。
        
        volcengine: {"type": "video_url", "video_url": {"url": data_url}}
        其他 provider 不支持时返回 None。
        """
        if self.media_format == "volcengine":
            url = self._file_to_data_url(video_path)
            if url:
                return {"type": "video_url", "video_url": {"url": url}}
        return None
    
    def _process_image_for_message(self, image: str) -> Optional[str]:
        """处理图片，返回用于 LLM API 的 URL（data URL / HTTP URL）"""
        if image.startswith("data:"):
            return image
        if image.startswith("http://") or image.startswith("https://"):
            return image
        try:
            from tools.image import process_image_for_llm
            result = process_image_for_llm(image)
            return result["data_url"]
        except Exception as e:
            logger.error(f"处理图片失败 {image}: {str(e)}")
            return None
    
    # ===== Media file helpers =====
    
    _MEDIA_MAX_SIZE = 20 * 1024 * 1024  # 20MB inline 上限
    
    def _file_to_base64(self, path: str) -> Optional[str]:
        """读取文件并返回 base64 字符串。超过大小限制或失败时返回 None。"""
        try:
            size = os.path.getsize(path)
            if size > self._MEDIA_MAX_SIZE:
                logger.warning(f"媒体文件过大，跳过 inline: {path} ({size/(1024*1024):.1f}MB > {self._MEDIA_MAX_SIZE/(1024*1024):.0f}MB)")
                return None
            with open(path, "rb") as f:
                return base64.b64encode(f.read()).decode("utf-8")
        except Exception as e:
            logger.error(f"读取媒体文件失败 {path}: {e}")
            return None
    
    def _file_to_data_url(self, path: str) -> Optional[str]:
        """读取文件并返回 data URL（含 mime type）。"""
        data = self._file_to_base64(path)
        if data is None:
            return None
        mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
        return f"data:{mime};base64,{data}"
    
    def _compress_context(self, messages: list[dict], max_tokens: int) -> list[dict]:
        """
        压缩上下文到指定 token 数
        
        策略:
        1. 保留 system 消息
        2. 保留最后一条 user 消息（当前问题）
        3. 从最早的 user/assistant 消息开始删除
        4. 直到 token 数符合要求
        
        参数:
        - messages: 消息列表
        - max_tokens: 最大 token 数
        
        返回: 压缩后的消息列表
        """
        if not messages:
            return []
        
        # 分离消息
        system_messages = []
        history_messages = []
        last_user_message = None
        
        for i, msg in enumerate(messages):
            role = msg.get("role")
            if role == "system":
                system_messages.append(msg)
            elif i == len(messages) - 1 and role == "user":
                # 最后一条 user 消息（当前问题）
                last_user_message = msg
            else:
                history_messages.append(msg)
        
        # 计算必须保留的 token 数
        must_keep = system_messages + ([last_user_message] if last_user_message else [])
        must_keep_tokens = self.token_counter.count_messages(must_keep)
        
        # 可用于历史消息的 token 数
        available_tokens = max_tokens - must_keep_tokens
        
        if available_tokens <= 0:
            logger.warning(
                f"System + 当前消息已超过 max_tokens ({must_keep_tokens} > {max_tokens})，"
                f"移除所有历史"
            )
            return must_keep
        
        # 从后往前累计历史消息，直到超过可用 token 数
        kept_history = []
        current_tokens = 3  # 基础开销
        
        for msg in reversed(history_messages):
            msg_tokens = self.token_counter.count_messages([msg]) - 3  # 减去基础开销
            if current_tokens + msg_tokens <= available_tokens:
                kept_history.insert(0, msg)
                current_tokens += msg_tokens
            else:
                # 超过限制，停止添加
                break
        
        # 组合最终结果
        result = system_messages + kept_history
        if last_user_message:
            result.append(last_user_message)
        
        # 记录压缩信息
        removed_count = len(history_messages) - len(kept_history)
        if removed_count > 0:
            logger.info(f"上下文压缩: 删除了 {removed_count} 条历史消息")
        
        return result
    
    async def _execute_tool_calls(self, tool_calls, tool_context: dict) -> list[dict]:
        """
        执行 tool calls 并返回 tool messages
        
        输入: OpenAI response 中的 tool_calls
        输出: [{"role": "tool", "tool_call_id": "...", "content": "..."}]
        """
        tool_messages = []
        
        for tool_call in tool_calls:
            tool_call_id = tool_call.id
            tool_name = tool_call.function.name
            tool_args_str = tool_call.function.arguments
            
            # 解析 JSON 参数
            try:
                tool_args_dict = json.loads(tool_args_str)
            except json.JSONDecodeError as e:
                tool_messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "content": json.dumps({
                        "success": False,
                        "error": f"JSON 解析错误: {str(e)}"
                    })
                })
                continue
            
            # 执行 tool
            logger.info(f"执行 tool: {tool_name}, 参数: {tool_args_dict}")
            result = await registry.execute(tool_name, tool_args_dict, tool_context)
            logger.info(f"tool {tool_name} 执行完成: {str(result)[:200]}")
            
            # 格式化结果
            if result.success:
                content = result.output
            else:
                content = json.dumps({
                    "success": False,
                    "error": result.error or "未知错误"
                })
            
            tool_messages.append({
                "role": "tool",
                "tool_call_id": tool_call_id,
                "content": content
            })
        
        return tool_messages
    
    # ===== Media path auto-detection from tool results =====
    
    _IMAGE_EXTS = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp'}
    _AUDIO_EXTS = {'.wav', '.mp3', '.flac', '.ogg', '.m4a', '.aac', '.wma'}
    _VIDEO_EXTS = {'.mp4', '.webm', '.mov', '.avi', '.mkv', '.flv', '.wmv'}
    
    def _extract_media_paths(self, tool_messages: list[dict]) -> dict:
        """
        从 tool result text 中自动提取存在的媒体文件路径。
        
        扫描每条 tool message 的 content，找出以 / 开头的绝对路径 token，
        按扩展名分类为 images / audio / video。
        
        返回: {"images": [...], "audio": [...], "video": [...]}
        """
        media = {"images": [], "audio": [], "video": []}
        for msg in tool_messages:
            content = msg.get("content", "")
            if not content:
                continue
            for token in content.replace("\n", " ").split():
                if not token.startswith("/"):
                    continue
                ext = os.path.splitext(token)[1].lower()
                if ext in self._IMAGE_EXTS and os.path.isfile(token):
                    media["images"].append(token)
                elif ext in self._AUDIO_EXTS and os.path.isfile(token):
                    media["audio"].append(token)
                elif ext in self._VIDEO_EXTS and os.path.isfile(token):
                    media["video"].append(token)
        return media
