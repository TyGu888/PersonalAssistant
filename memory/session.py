from core.types import ChatMessage
import sqlite3
import json
import os
from datetime import datetime


class SessionStore:
    def __init__(self, db_path: str = "data/sessions.db"):
        """
        初始化 SQLite 连接
        
        表结构:
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY,          -- session_id
            messages TEXT,                -- JSON array
            created_at TIMESTAMP,
            updated_at TIMESTAMP
        )
        """
        # 确保数据目录存在
        db_dir = os.path.dirname(db_path)
        if db_dir and not os.path.exists(db_dir):
            os.makedirs(db_dir, exist_ok=True)
        
        # 创建数据库连接（线程安全设置）
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        
        # 创建表（如果不存在）
        self._create_table()
    
    def _create_table(self):
        """创建 sessions 表"""
        cursor = self.conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                messages TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        self.conn.commit()
    
    def _serialize_message(self, message: ChatMessage) -> dict:
        """序列化 ChatMessage 为字典（处理 datetime）"""
        return {
            "role": message.role,
            "content": message.content,
            "timestamp": message.timestamp.isoformat()
        }
    
    def _deserialize_message(self, data: dict) -> ChatMessage:
        """反序列化字典为 ChatMessage（处理 datetime）"""
        return ChatMessage(
            role=data["role"],
            content=data["content"],
            timestamp=datetime.fromisoformat(data["timestamp"])
        )
    
    def append(self, session_id: str, message: ChatMessage):
        """
        追加一条消息到 Session
        
        流程:
        1. 读取现有 messages (JSON)
        2. 追加新消息
        3. 更新 updated_at
        """
        cursor = self.conn.cursor()
        
        # 检查 session 是否存在
        cursor.execute("SELECT messages, created_at FROM sessions WHERE id = ?", (session_id,))
        row = cursor.fetchone()
        
        if row is None:
            # 创建新 session
            messages = [self._serialize_message(message)]
            cursor.execute("""
                INSERT INTO sessions (id, messages, created_at, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """, (session_id, json.dumps(messages)))
        else:
            # 追加到现有 session
            existing_messages = json.loads(row["messages"])
            existing_messages.append(self._serialize_message(message))
            cursor.execute("""
                UPDATE sessions
                SET messages = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (json.dumps(existing_messages), session_id))
        
        self.conn.commit()
    
    def get_recent(self, session_id: str, n: int = 20) -> list[ChatMessage]:
        """
        获取最近 n 条消息
        
        输出: [ChatMessage, ...]
        """
        cursor = self.conn.cursor()
        cursor.execute("SELECT messages FROM sessions WHERE id = ?", (session_id,))
        row = cursor.fetchone()
        
        if row is None:
            return []
        
        messages_data = json.loads(row["messages"])
        # 获取最后 n 条消息
        recent_messages_data = messages_data[-n:] if len(messages_data) > n else messages_data
        
        return [self._deserialize_message(msg) for msg in recent_messages_data]
    
    def get_all(self, session_id: str) -> list[ChatMessage]:
        """获取全部历史（用于记忆提取）"""
        cursor = self.conn.cursor()
        cursor.execute("SELECT messages FROM sessions WHERE id = ?", (session_id,))
        row = cursor.fetchone()
        
        if row is None:
            return []
        
        messages_data = json.loads(row["messages"])
        return [self._deserialize_message(msg) for msg in messages_data]
    
    def clear(self, session_id: str):
        """清空 Session"""
        cursor = self.conn.cursor()
        cursor.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        self.conn.commit()
    
    def close(self):
        """关闭数据库连接"""
        if self.conn:
            self.conn.close()
    
    def __del__(self):
        """析构函数，确保连接关闭"""
        try:
            if hasattr(self, 'conn') and self.conn:
                self.conn.close()
        except:
            pass
