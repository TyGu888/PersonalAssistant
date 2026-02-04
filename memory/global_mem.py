from core.types import MemoryItem
import chromadb
from datetime import datetime
import uuid
from typing import List


class GlobalMemory:
    def __init__(self, db_path: str = "data/chroma", openai_api_key: str = None):
        """
        初始化 ChromaDB
        
        ChromaDB Collection:
        - name: "memories"
        - metadata: {"person_id", "type", "source_session", "created_at", "active", "scope"}
        
        注意: ChromaDB 有内置 embedding，可以不使用 OpenAI
        """
        self.client = chromadb.PersistentClient(path=db_path)
        self.collection = self.client.get_or_create_collection("memories")
    
    async def add(
        self, 
        person_id: str, 
        content: str, 
        memory_type: str, 
        source_session: str,
        scope: str = "personal"
    ) -> str:
        """
        添加一条记忆
        
        流程:
        1. 生成唯一 ID (使用 uuid)
        2. ChromaDB 自动计算 embedding
        3. 存入 collection
        
        参数:
        - person_id: 统一身份标识
        - content: 记忆内容
        - memory_type: 记忆类型
        - source_session: 来源会话
        - scope: 记忆范围 ("global" | "personal")
        
        返回: 记忆 ID
        """
        memory_id = str(uuid.uuid4())
        created_at = datetime.utcnow().isoformat()
        
        metadata = {
            "person_id": person_id,
            "type": memory_type,
            "source_session": source_session,
            "created_at": created_at,
            "active": "true",  # ChromaDB metadata 需要字符串
            "scope": scope
        }
        
        self.collection.add(
            documents=[content],
            metadatas=[metadata],
            ids=[memory_id]
        )
        
        return memory_id
    
    async def search(
        self, 
        person_id: str, 
        query: str, 
        top_k: int = 5,
        include_global: bool = True
    ) -> List[MemoryItem]:
        """
        向量相似度搜索
        
        输入:
        - person_id: 统一身份标识
        - query: 查询文本
        - top_k: 返回数量
        - include_global: 是否包含全局记忆
        
        查询逻辑:
        - include_global=True: 查询 scope="global" 或 (scope="personal" 且 person_id 匹配)
        - include_global=False: 只查询 scope="personal" 且 person_id 匹配
        
        输出: [MemoryItem, ...] 按相似度排序
        """
        # 构建查询条件
        if include_global:
            # 包含全局记忆：scope="global" 或 (scope="personal" 且 person_id 匹配)
            where_filter = {
                "$and": [
                    {"active": {"$eq": "true"}},
                    {
                        "$or": [
                            {"scope": {"$eq": "global"}},
                            {
                                "$and": [
                                    {"scope": {"$eq": "personal"}},
                                    {"person_id": {"$eq": person_id}}
                                ]
                            }
                        ]
                    }
                ]
            }
        else:
            # 只查询个人记忆
            where_filter = {
                "$and": [
                    {"person_id": {"$eq": person_id}},
                    {"scope": {"$eq": "personal"}},
                    {"active": {"$eq": "true"}}
                ]
            }
        
        results = self.collection.query(
            query_texts=[query],
            n_results=top_k,
            where=where_filter,
            include=["documents", "metadatas", "embeddings", "distances"]
        )
        
        memory_items = []
        
        # ChromaDB 返回格式: {"ids": [[...]], "documents": [[...]], "metadatas": [[...]], "embeddings": [[...]], "distances": [[...]]}
        if results["ids"] and len(results["ids"][0]) > 0:
            for i in range(len(results["ids"][0])):
                memory_id = results["ids"][0][i]
                content = results["documents"][0][i]
                metadata = results["metadatas"][0][i]
                # ChromaDB 可能不返回 embeddings（如果使用默认 embedding），使用空列表作为默认值
                embedding = results["embeddings"][0][i] if (results.get("embeddings") and 
                                                             results["embeddings"] and 
                                                             len(results["embeddings"][0]) > i) else []
                
                memory_item = MemoryItem(
                    id=memory_id,
                    person_id=metadata.get("person_id", metadata.get("user_id", "")),  # 兼容旧数据
                    type=metadata["type"],
                    content=content,
                    embedding=embedding,
                    source_session=metadata["source_session"],
                    created_at=datetime.fromisoformat(metadata["created_at"]),
                    active=metadata.get("active", "true") == "true",
                    scope=metadata.get("scope", "personal")  # 兼容旧数据默认 personal
                )
                memory_items.append(memory_item)
        
        return memory_items
    
    def deactivate(self, memory_id: str):
        """标记记忆为过期（软删除）- 更新 metadata 中的 active 字段"""
        # 先获取当前 metadata 和 document
        result = self.collection.get(ids=[memory_id])
        
        if result["ids"]:
            current_metadata = result["metadatas"][0]
            current_document = result["documents"][0] if result.get("documents") else None
            updated_metadata = {**current_metadata, "active": "false"}
            
            # ChromaDB 的 update 方法需要同时提供 documents
            if current_document:
                self.collection.update(
                    ids=[memory_id],
                    documents=[current_document],
                    metadatas=[updated_metadata]
                )
            else:
                # 如果没有 document，只更新 metadata（某些 ChromaDB 版本支持）
                self.collection.update(
                    ids=[memory_id],
                    metadatas=[updated_metadata]
                )
