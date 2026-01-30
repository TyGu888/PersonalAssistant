"""
HTTP API Channel - 提供 RESTful 接口

端点:
- POST /chat - 发送消息并获取回复
- GET /health - 健康检查
- GET /agents - 列出可用 agents
- GET /tools - 列出可用 tools
- GET /sessions/{session_id} - 获取会话历史
- DELETE /sessions/{session_id} - 清空会话
"""

import logging
import uuid
from typing import Optional

from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import uvicorn

from channels.base import BaseChannel, MessageHandler
from core.types import IncomingMessage, OutgoingMessage

logger = logging.getLogger(__name__)


# ===== 请求/响应模型 =====

class ChatRequest(BaseModel):
    """聊天请求"""
    text: str = Field(..., description="消息文本")
    user_id: str = Field(default="api_user", description="用户 ID")
    session_id: Optional[str] = Field(default=None, description="可选：指定 session ID")
    images: list[str] = Field(default_factory=list, description="图片列表（base64 或 URL）")


class ChatResponse(BaseModel):
    """聊天响应"""
    text: str = Field(..., description="回复文本")
    session_id: str = Field(..., description="会话 ID")
    attachments: list[str] = Field(default_factory=list, description="附件列表")


class HealthResponse(BaseModel):
    """健康检查响应"""
    status: str = "ok"


class AgentInfo(BaseModel):
    """Agent 信息"""
    id: str
    description: Optional[str] = None


class AgentsResponse(BaseModel):
    """Agents 列表响应"""
    agents: list[AgentInfo]


class ToolInfo(BaseModel):
    """Tool 信息"""
    name: str
    description: str


class ToolsResponse(BaseModel):
    """Tools 列表响应"""
    tools: list[ToolInfo]


class MessageInfo(BaseModel):
    """消息信息"""
    role: str
    content: str
    timestamp: str


class SessionHistoryResponse(BaseModel):
    """会话历史响应"""
    session_id: str
    messages: list[MessageInfo]


class DeleteSessionResponse(BaseModel):
    """删除会话响应"""
    success: bool
    message: str


# ===== HTTP Channel =====

class HTTPChannel(BaseChannel):
    """
    HTTP API Channel - 提供 RESTful 接口
    
    特点:
    - 同步请求/响应模式（非 WebSocket）
    - 支持 API Key 认证
    - 支持 CORS
    """
    
    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 8080,
        api_key: Optional[str] = None,
        on_message: MessageHandler = None,
        engine_ref: Optional[object] = None,  # Engine 引用，用于获取 agents/tools 列表
    ):
        """
        初始化 HTTP Channel
        
        参数:
        - host: 绑定地址
        - port: 端口
        - api_key: API Key（可选，为 None 时不验证）
        - on_message: 消息处理回调
        - engine_ref: Engine 引用（用于获取 agents/tools 列表）
        """
        super().__init__(on_message)
        self.host = host
        self.port = port
        self.api_key = api_key
        self.engine_ref = engine_ref
        
        # 创建 FastAPI 应用
        self.app = FastAPI(
            title="Personal Agent Hub API",
            description="个人助手 Hub 的 HTTP API 接口",
            version="1.0.0"
        )
        
        self._setup_middleware()
        self._setup_routes()
        self.server = None
    
    def _verify_api_key(self, x_api_key: str = Header(None, alias="X-API-Key")):
        """
        验证 API Key
        
        如果配置了 api_key，则必须提供有效的 X-API-Key header
        """
        if self.api_key:
            if not x_api_key:
                raise HTTPException(
                    status_code=401,
                    detail="Missing API Key. Please provide X-API-Key header."
                )
            if x_api_key != self.api_key:
                raise HTTPException(
                    status_code=401,
                    detail="Invalid API Key"
                )
    
    def _setup_middleware(self):
        """配置中间件"""
        # CORS 中间件
        self.app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    
    def _setup_routes(self):
        """配置路由"""
        
        @self.app.post("/chat", response_model=ChatResponse, tags=["Chat"])
        async def chat(
            request: ChatRequest,
            _=Depends(self._verify_api_key)
        ):
            """
            发送消息并获取回复
            
            - text: 消息文本（必填）
            - user_id: 用户 ID（可选，默认 api_user）
            - session_id: 会话 ID（可选，不提供则自动生成）
            - images: 图片列表（可选）
            """
            if not self.on_message:
                raise HTTPException(
                    status_code=500,
                    detail="Message handler not configured"
                )
            
            # 生成或使用提供的 session_id
            session_id = request.session_id or f"http:dm:{request.user_id}:{uuid.uuid4().hex[:8]}"
            
            # 构造 IncomingMessage
            incoming = IncomingMessage(
                channel="http",
                user_id=request.user_id,
                text=request.text,
                is_group=False,
                images=request.images,
                raw={"session_id": session_id}
            )
            
            # 覆盖 get_session_id 方法以使用指定的 session_id
            # 这样可以让用户在多次请求中保持同一个 session
            if request.session_id:
                incoming._custom_session_id = request.session_id
                original_get_session_id = incoming.get_session_id
                def custom_get_session_id():
                    return incoming._custom_session_id
                incoming.get_session_id = custom_get_session_id
            
            try:
                # 调用消息处理器
                response = await self.on_message(incoming)
                
                return ChatResponse(
                    text=response.text,
                    session_id=incoming.get_session_id(),
                    attachments=response.attachments
                )
            except Exception as e:
                logger.error(f"Error processing chat request: {e}", exc_info=True)
                raise HTTPException(
                    status_code=500,
                    detail=f"Error processing request: {str(e)}"
                )
        
        @self.app.get("/health", response_model=HealthResponse, tags=["System"])
        async def health():
            """健康检查端点"""
            return HealthResponse(status="ok")
        
        @self.app.get("/agents", response_model=AgentsResponse, tags=["Info"])
        async def list_agents(_=Depends(self._verify_api_key)):
            """
            列出可用的 agents
            """
            if not self.engine_ref:
                return AgentsResponse(agents=[])
            
            agents = []
            for agent_id, agent in self.engine_ref.agents.items():
                agents.append(AgentInfo(
                    id=agent_id,
                    description=getattr(agent, 'description', None) or f"Agent: {agent_id}"
                ))
            
            return AgentsResponse(agents=agents)
        
        @self.app.get("/tools", response_model=ToolsResponse, tags=["Info"])
        async def list_tools(_=Depends(self._verify_api_key)):
            """
            列出可用的 tools
            """
            from tools.registry import registry
            
            tools = []
            for name in registry.list_tools():
                tool_info = registry._tools.get(name, {})
                tools.append(ToolInfo(
                    name=name,
                    description=tool_info.get("description", "")
                ))
            
            return ToolsResponse(tools=tools)
        
        @self.app.get("/sessions/{session_id}", response_model=SessionHistoryResponse, tags=["Sessions"])
        async def get_session_history(
            session_id: str,
            limit: int = 50,
            _=Depends(self._verify_api_key)
        ):
            """
            获取会话历史
            
            - session_id: 会话 ID
            - limit: 返回的最大消息数（默认 50）
            """
            if not self.engine_ref:
                raise HTTPException(
                    status_code=500,
                    detail="Engine not configured"
                )
            
            try:
                # 从 memory manager 获取历史
                history = self.engine_ref.memory.get_history(session_id, limit=limit)
                
                messages = [
                    MessageInfo(
                        role=msg["role"],
                        content=msg["content"],
                        timestamp=msg.get("timestamp", "")
                    )
                    for msg in history
                ]
                
                return SessionHistoryResponse(
                    session_id=session_id,
                    messages=messages
                )
            except Exception as e:
                logger.error(f"Error getting session history: {e}", exc_info=True)
                raise HTTPException(
                    status_code=500,
                    detail=f"Error getting session history: {str(e)}"
                )
        
        @self.app.delete("/sessions/{session_id}", response_model=DeleteSessionResponse, tags=["Sessions"])
        async def delete_session(
            session_id: str,
            _=Depends(self._verify_api_key)
        ):
            """
            清空会话历史
            
            - session_id: 会话 ID
            """
            if not self.engine_ref:
                raise HTTPException(
                    status_code=500,
                    detail="Engine not configured"
                )
            
            try:
                # 从 memory manager 清空历史
                self.engine_ref.memory.clear_session(session_id)
                
                return DeleteSessionResponse(
                    success=True,
                    message=f"Session {session_id} cleared successfully"
                )
            except Exception as e:
                logger.error(f"Error deleting session: {e}", exc_info=True)
                raise HTTPException(
                    status_code=500,
                    detail=f"Error deleting session: {str(e)}"
                )
    
    async def start(self):
        """启动 HTTP 服务器"""
        logger.info(f"Starting HTTP API server on {self.host}:{self.port}")
        
        config = uvicorn.Config(
            self.app,
            host=self.host,
            port=self.port,
            log_level="info",
            access_log=True
        )
        self.server = uvicorn.Server(config)
        
        try:
            await self.server.serve()
        except Exception as e:
            logger.error(f"HTTP server error: {e}", exc_info=True)
            raise
    
    async def send(self, user_id: str, message: OutgoingMessage):
        """
        HTTP 模式不支持主动推送
        
        HTTP 是请求/响应模式，无法主动向客户端推送消息。
        如需推送功能，请使用 WebSocket 或其他实时通信协议。
        """
        logger.warning(
            f"HTTP Channel does not support push messages. "
            f"Message to {user_id} was not sent: {message.text[:50]}..."
        )
    
    async def stop(self):
        """停止 HTTP 服务器"""
        logger.info("Stopping HTTP API server")
        if self.server:
            self.server.should_exit = True
