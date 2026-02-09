"""
Gateway Server - FastAPI + WebSocket

提供：
1. POST /chat - 同步聊天接口（backward compatible）
2. WS /ws - WebSocket 实时通信（CLI Client 和 WebUI 使用）
3. GET /health - 健康检查
4. GET /agents - 列出 agents
5. GET /tools - 列出 tools
6. GET/DELETE /sessions/{id} - 会话管理
"""

import asyncio
import json
import logging
import uuid
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Depends, Header, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
import uvicorn

from gateway.bus import MessageBus
from gateway.dispatcher import Dispatcher
from core.types import IncomingMessage

logger = logging.getLogger(__name__)


# ===== Request/Response Models =====

class ChatRequest(BaseModel):
    text: str = Field(..., description="消息文本")
    user_id: str = Field(default="api_user", description="用户 ID")
    session_id: Optional[str] = Field(default=None, description="可选：指定 session ID")
    images: list[str] = Field(default_factory=list, description="图片列表")


class ChatResponse(BaseModel):
    text: str = Field(..., description="回复文本")
    session_id: str = Field(..., description="会话 ID")
    attachments: list[str] = Field(default_factory=list, description="附件列表")


class HealthResponse(BaseModel):
    status: str = "ok"


class AgentInfo(BaseModel):
    id: str
    description: Optional[str] = None


class AgentsResponse(BaseModel):
    agents: list[AgentInfo]


class ToolInfo(BaseModel):
    name: str
    description: str


class ToolsResponse(BaseModel):
    tools: list[ToolInfo]


class MessageInfo(BaseModel):
    role: str
    content: str
    timestamp: str


class SessionHistoryResponse(BaseModel):
    session_id: str
    messages: list[MessageInfo]


class DeleteSessionResponse(BaseModel):
    success: bool
    message: str


# ===== Gateway Server =====

class GatewayServer:
    """
    Gateway FastAPI 服务
    
    提供 HTTP API 和 WebSocket 连接。
    消息通过 MessageBus 转发给 AgentLoop。
    """
    
    def __init__(
        self,
        bus: MessageBus,
        dispatcher: Dispatcher,
        host: str = "0.0.0.0",
        port: int = 8080,
        api_key: Optional[str] = None,
        gateway_ref: Optional[object] = None,  # Gateway 引用，用于获取 agents/tools/memory
    ):
        self.bus = bus
        self.dispatcher = dispatcher
        self.host = host
        self.port = port
        self.api_key = api_key
        self.gateway_ref = gateway_ref
        
        self.app = FastAPI(
            title="Personal Agent Hub Gateway",
            description="Agent-Centric Gateway API",
            version="2.0.0"
        )
        
        self._setup_middleware()
        self._setup_routes()
        self.server = None
    
    def _verify_api_key(self, x_api_key: str = Header(None, alias="X-API-Key")):
        if self.api_key:
            if not x_api_key:
                raise HTTPException(status_code=401, detail="Missing API Key")
            if x_api_key != self.api_key:
                raise HTTPException(status_code=401, detail="Invalid API Key")
    
    def _setup_middleware(self):
        self.app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    
    def _setup_routes(self):
        
        # 工作区静态文件预览（只读，供浏览器打开 PPT 预览图等）
        _workspace_dir = Path(__file__).resolve().parent.parent / "data" / "workspace"

        @self.app.get("/workspace/{path:path}", tags=["Preview"])
        async def serve_workspace_file(path: str):
            """
            以只读方式提供 data/workspace 下的文件，用于预览。
            例如 Agent 生成 preview/slide-1.png 后，可打开: http://localhost:8080/workspace/preview/slide-1.png
            """
            if not path or ".." in path or path.startswith("/"):
                raise HTTPException(status_code=400, detail="Invalid path")
            full = (_workspace_dir / path).resolve()
            if not full.is_relative_to(_workspace_dir) or not full.exists() or not full.is_file():
                raise HTTPException(status_code=404, detail="Not found")
            return FileResponse(full)

        @self.app.post("/chat", response_model=ChatResponse, tags=["Chat"])
        async def chat(request: ChatRequest, _=Depends(self._verify_api_key)):
            """发送消息并获取回复（同步）"""
            session_id = request.session_id or f"http:dm:{request.user_id}"
            
            incoming = IncomingMessage(
                channel="http",
                user_id=request.user_id,
                text=request.text,
                is_group=False,
                images=request.images,
                raw={"session_id": session_id}
            )
            
            if request.session_id:
                incoming._custom_session_id = request.session_id
                original_get_session_id = incoming.get_session_id
                def custom_get_session_id():
                    return incoming._custom_session_id
                incoming.get_session_id = custom_get_session_id
            
            try:
                # 通过 MessageBus 发送，等待回复
                response = await self.bus.publish(incoming, wait_reply=True)
                
                return ChatResponse(
                    text=response.text if response else "",
                    session_id=session_id,
                    attachments=response.attachments if response else []
                )
            except Exception as e:
                logger.error(f"Error processing chat: {e}", exc_info=True)
                raise HTTPException(status_code=500, detail=str(e))
        
        @self.app.websocket("/ws")
        async def websocket_endpoint(websocket: WebSocket):
            """
            WebSocket 连接端点
            
            协议：
            - Client → Server:
              {"type": "auth", "api_key": "xxx"}                    认证
              {"type": "message", "text": "...", "user_id": "..."}  发送消息
              {"type": "register_tools", "tools": [...]}            注册客户端工具
              {"type": "tool_result", "call_id": "...", "result": "...", "error": "..."} 工具执行结果
            
            - Server → Client:
              {"type": "auth_ok", "connection_id": "..."}           认证成功
              {"type": "reply", "text": "...", "session_id": "..."} 消息回复
              {"type": "push", "text": "..."}                       主动推送
              {"type": "tool_request", "call_id": "...", "tool_name": "...", "arguments": {...}} 工具调用请求
            """
            await websocket.accept()
            connection_id = str(uuid.uuid4())
            authenticated = not self.api_key  # 无 api_key 配置时默认已认证
            
            try:
                # ws_send 发送任意 dict (由 Dispatcher 调用)
                async def ws_send(data: dict):
                    await websocket.send_json(data)
                
                while True:
                    data = await websocket.receive_json()
                    msg_type = data.get("type", "")
                    
                    # 认证
                    if msg_type == "auth":
                        if self.api_key and data.get("api_key") != self.api_key:
                            await websocket.send_json({"type": "error", "message": "Invalid API key"})
                            await websocket.close(code=4001)
                            return
                        authenticated = True
                        self.dispatcher.register_ws(connection_id, ws_send)
                        await websocket.send_json({"type": "auth_ok", "connection_id": connection_id})
                        continue
                    
                    if not authenticated:
                        await websocket.send_json({"type": "error", "message": "Not authenticated"})
                        continue
                    
                    # 注册远程工具 (客户端提供工具给 Agent 使用)
                    if msg_type == "register_tools":
                        tools = data.get("tools", [])
                        self.dispatcher.register_remote_tools(connection_id, tools)
                        await websocket.send_json({
                            "type": "tools_registered",
                            "count": len(tools),
                            "names": [t["name"] for t in tools]
                        })
                        continue
                    
                    # 工具执行结果 (客户端完成了 Agent 请求的工具调用)
                    if msg_type == "tool_result":
                        call_id = data.get("call_id", "")
                        result = data.get("result", "")
                        error = data.get("error")
                        self.dispatcher.resolve_rpc_result(call_id, result, error)
                        continue
                    
                    # 聊天消息
                    if msg_type == "message":
                        user_id = data.get("user_id", f"ws_{connection_id[:8]}")
                        text = data.get("text", "")
                        session_id = data.get("session_id")
                        images = data.get("images", [])
                        
                        if not text:
                            await websocket.send_json({"type": "error", "message": "Empty text"})
                            continue
                        
                        incoming = IncomingMessage(
                            channel="websocket",
                            user_id=user_id,
                            text=text,
                            is_group=False,
                            images=images,
                            raw={"connection_id": connection_id, "session_id": session_id}
                        )
                        
                        if session_id:
                            incoming._custom_session_id = session_id
                            def make_custom_getter(sid):
                                def getter():
                                    return sid
                                return getter
                            incoming.get_session_id = make_custom_getter(session_id)
                        
                        try:
                            response = await self.bus.publish(incoming, wait_reply=True)
                            await websocket.send_json({
                                "type": "reply",
                                "text": response.text if response else "",
                                "session_id": incoming.get_session_id(),
                                "attachments": response.attachments if response else []
                            })
                        except Exception as e:
                            logger.error(f"WS message error: {e}", exc_info=True)
                            await websocket.send_json({"type": "error", "message": str(e)})
                    
            except WebSocketDisconnect:
                logger.info(f"WebSocket {connection_id} disconnected")
            except Exception as e:
                logger.error(f"WebSocket error: {e}", exc_info=True)
            finally:
                self.dispatcher.unregister_ws(connection_id)
        
        @self.app.get("/health", response_model=HealthResponse, tags=["System"])
        async def health():
            return HealthResponse(status="ok")
        
        @self.app.get("/agents", response_model=AgentsResponse, tags=["Info"])
        async def list_agents(_=Depends(self._verify_api_key)):
            if not self.gateway_ref or not hasattr(self.gateway_ref, 'agent_loop'):
                return AgentsResponse(agents=[])
            agents = []
            for agent_id, agent in self.gateway_ref.agent_loop.agents.items():
                agents.append(AgentInfo(
                    id=agent_id,
                    description=getattr(agent, 'description', None) or f"Agent: {agent_id}"
                ))
            return AgentsResponse(agents=agents)
        
        @self.app.get("/tools", response_model=ToolsResponse, tags=["Info"])
        async def list_tools(_=Depends(self._verify_api_key)):
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
        async def get_session_history(session_id: str, limit: int = 50, _=Depends(self._verify_api_key)):
            if not self.gateway_ref or not hasattr(self.gateway_ref, 'agent_loop'):
                raise HTTPException(status_code=500, detail="Gateway not ready")
            try:
                history = self.gateway_ref.agent_loop.runtime.memory.get_history(session_id, limit=limit)
                messages = [
                    MessageInfo(role=msg["role"], content=msg["content"], timestamp=msg.get("timestamp", ""))
                    for msg in history
                ]
                return SessionHistoryResponse(session_id=session_id, messages=messages)
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))
        
        @self.app.delete("/sessions/{session_id}", response_model=DeleteSessionResponse, tags=["Sessions"])
        async def delete_session(session_id: str, _=Depends(self._verify_api_key)):
            if not self.gateway_ref or not hasattr(self.gateway_ref, 'agent_loop'):
                raise HTTPException(status_code=500, detail="Gateway not ready")
            try:
                self.gateway_ref.agent_loop.runtime.memory.clear_session(session_id)
                return DeleteSessionResponse(success=True, message=f"Session {session_id} cleared")
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))
    
    async def start(self):
        """启动 HTTP 服务器"""
        logger.info(f"Starting Gateway server on {self.host}:{self.port}")
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
            logger.error(f"Gateway server error: {e}", exc_info=True)
            raise
    
    async def stop(self):
        """停止服务器"""
        logger.info("Stopping Gateway server")
        if self.server:
            self.server.should_exit = True
