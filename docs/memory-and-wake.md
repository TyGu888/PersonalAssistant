# 记忆模块与唤醒机制 — 梳理

> 基于当前代码从头到尾梳理：记忆存什么、谁写入、RAG 怎么用、唤醒带什么记忆、问题与改进方向。

---

## 1. 记忆模块整体结构

```
┌─────────────────────────────────────────────────────────────────┐
│  SessionStore (SQLite: data/sessions.db)                        │
│  - 按 session_id 存「原始对话」：每条 user/assistant 消息       │
│  - 不按内容检索，只按 session 取最近 N 条                       │
│  - 谁写：AgentLoop 里 save_message(session_id, role, content)   │
└─────────────────────────────────────────────────────────────────┘
                                    │
                                    │ 仅 get_recent(session_id, n) 用于 load_context 的 history
                                    │ extract_memories() 会读 session 但当前未被调用
                                    ▼
┌─────────────────────────────────────────────────────────────────┐
│  GlobalMemory (ChromaDB: data/chroma, collection "memories")    │
│  - 存的是「一条条记忆」：content + embedding + metadata         │
│  - metadata: person_id, type, source_session, scope, active     │
│  - scope: "global" | "personal"                                 │
│  - 谁写：仅 memory_add 工具（Agent 主动加）；extract_memories 未接入 │
└─────────────────────────────────────────────────────────────────┘
                                    │
                    ┌───────────────┴───────────────┐
                    ▼                               ▼
         load_context() 里 RAG              memory_search 工具
         global_mem.search(person_id,       Agent 主动查同一 store
          query=当前消息, top_k, include_global)
```

- **Session**：所有 session 的「原始对话」都在 SQLite 里，但**按 session_id 分条带**，不是「所有内容混在一个池子里」。
- **Global memory（ChromaDB）**：是**所有已写入的记忆条目的集合**，但每条带 `person_id` 和 `scope`；检索时用 query 做向量相似度，再按 scope/person_id 过滤。不是「所有 session 的原始内容都塞进一个池子」，而是「离散记忆 + 向量检索」。

---

## 2. memory_add / memory_search 和 RAG 的 global 是什么关系？

- **同一存储**：`memory_add`、`memory_search` 和 `load_context` 里用的 RAG，操作的都是 **MemoryManager.global_mem**，即 ChromaDB 的 `memories` collection。
- **memory_add**：往这个 collection 里插入一条记忆（content、person_id、scope、type、source_session）；之后 RAG 和 memory_search 都能查到。
- **memory_search**：对同一 collection 做向量检索，可指定 scope=all/global/personal，等价于「Agent 主动再查一遍」。
- **RAG（get_context）**：每次加载上下文时自动调用 `global_mem.search(person_id, query=当前用户消息, top_k, include_global)`，把结果放进 `context["memories"]`，再注入 system prompt。

所以：**memory 的 read/add 工具和 RAG 用的是同一套 Global memory（ChromaDB）；RAG 是「自动按当前消息查一次」，memory_search 是「Agent 再按需查一次」。**

---

## 3. 「Global memory」里到底有什么？全在一起吗？

- **不是**「所有 session 的原始对话都混在一起」。
- ChromaDB 里只有**离散记忆条目**，来源目前只有：
  - **memory_add**：Agent 在对话里主动调用写入。
  - **extract_memories**：设计上会从 Session 历史里用 LLM 抽关键信息写入 GlobalMemory，但**当前代码里没有任何地方调用**，所以没有自动「session → global」的流水线。
- 每条记忆有：
  - **scope**：`global`（所有人可见）或 `personal`（仅该 person_id 可见）。
  - **person_id**：single_owner 下多为 `"owner"`。
- RAG 时：`include_global=True` 会查「scope=global 或 (scope=personal 且 person_id 匹配)」；所以「global」= 存在 ChromaDB 里且 scope=global 的那部分 + 当前用户的 personal，**不是**「所有 session 全文」在一起。

---

## 4. 唤醒的文案是硬编码的吗？

- **是。**
  - **周期性唤醒**：`agent/loop.py` 里 `_on_wake()` 写死一段英文 prompt（"[Periodic Wake] You are waking up for a routine check..."）。
  - **定时提醒唤醒**：`tools/scheduler.py` 里 `run_scheduled_reminder` 写死模板：`"[Scheduled Reminder] Please remind user {user_id} on {channel}: {content}. Use send_message..."`，只有 `content` 是用户设提醒时填的「要干什么」。

---

## 5. 唤醒时带什么记忆？RAG 每次会一样吗？

- **唤醒时**：`load_context(session_id="system:dm:system", query=msg.text, person_id="owner", history_limit=0)`  
  - **history**：0 条（不读任何 Session 历史）。  
  - **memories**：`global_mem.search(person_id="owner", query=msg.text, top_k=memory_limit, include_global=True)` 的向量检索结果。

- **周期性唤醒**：`msg.text` 每次都是同一段固定英文，**query 几乎不变** → 向量检索结果会**高度相似甚至几乎一样**，所以「RAG 每次都会差不多」是对的。

- **定时提醒唤醒**：`msg.text` 里包含「提醒内容」和 channel/user，query 会随提醒变化，RAG 会稍微不同，但若多条提醒文案类似，检索结果仍可能重复。

---

## 6. 唤醒的记忆管理有什么问题？

- **周期性唤醒**：
  - 依赖的「要干什么」主要来自**硬编码的 wake 文案** + **system prompt 里的角色与职责**，而不是来自记忆或工具。
  - 但 Agent 仍会拿到**同一套 RAG 记忆**（因为 query 固定），且可以调用很多工具（web_search、send_message 等），容易变成「每次醒来都类似地调一堆工具找事做」，行为重复、不可控。
  - 更合理的设计：**唤醒「要干什么」应主要由 system prompt 规定**（例如：每日检查项、汇报规则），记忆只做补充；RAG 对周期性唤醒可以弱化或换 query，避免每次都拉同一批记忆。

- **定时提醒唤醒**：
  - 「要干什么」已经在消息里的 `content` 中，记忆管理相对合理；RAG 只是辅助上下文。

- **共同点**：
  - 唤醒不写 Session（不污染对话历史）。
  - 唤醒不自动调用 `extract_memories`（当前本来也没人调）。

---

## 7. 记忆模块数据流小结

| 数据 | 存哪 | 谁写入 | 谁读取 |
|------|------|--------|--------|
| 原始对话 | SessionStore (SQLite) | AgentLoop.save_message | get_context → history；extract_memories（未接入） |
| 长期记忆条 | GlobalMemory (ChromaDB) | memory_add 工具 | get_context → RAG；memory_search 工具 |
| Session → 记忆 | 设计有 extract_memories | 无调用方 | - |

| 场景 | history | memories (RAG) | query 来源 |
|------|---------|-----------------|------------|
| 普通消息 | 该 session 最近 N 条 | person_id + 当前用户消息 | 当前用户消息 |
| 周期性唤醒 | 0 条 | owner + 固定唤醒文案 | 固定英文 → 每次几乎相同 |
| 定时提醒唤醒 | 0 条 | owner + 提醒文案 | 含 content → 随提醒变化 |

---

## 8. 改进方向（建议）

1. **唤醒应强烈依赖 system prompt**  
   - 在 default/Skill 的 system 里明确写「醒来时该做什么、不该做什么」；  
   - 可选：对周期性唤醒减少或不用 RAG，或改用固定「唤醒专用 query」避免每次都拉同一批记忆。

2. **周期性唤醒的 RAG**  
   - 若保留 RAG：可单独传一个短句（如「例行检查、今日待办」）作为 query，与固定长文案解耦，减少重复感；  
   - 或 wake 时 `memory_limit=0` / 不注入 memories，完全靠 prompt 驱动。

3. **extract_memories 是否接入**  
   - 若希望「对话自动沉淀为长期记忆」，可在某处（例如对话结束或 N 条消息后）调用 `extract_memories(session_id, person_id)`，把 Session 内容抽成条写入 GlobalMemory；  
   - 当前未接入，所以长期记忆**只来自 memory_add**。

4. **定时提醒**  
   - 保持现状即可：要干什么在消息里，RAG 仅作补充；记忆管理问题主要在周期性唤醒。

---

以上是当前记忆模块与两种唤醒方式的完整梳理和问题归纳。
