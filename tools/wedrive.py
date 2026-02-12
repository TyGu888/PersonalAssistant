"""
WeDrive (企业微信微盘) 工具集

依赖 WeCom Channel 的 access_token，需在管理后台为自建应用开启微盘 API 权限。
接口文档: https://developer.work.weixin.qq.com/document/path/93657 等
"""

import json
import logging
import os
from typing import Optional

import httpx

from tools.registry import registry

logger = logging.getLogger(__name__)

WEDRIVE_BASE = "https://qyapi.weixin.qq.com/cgi-bin/wedrive"


async def _get_wecom_token(context) -> Optional[str]:
    if context is None:
        return None
    cm = context.get("channel_manager")
    if not cm:
        return None
    ch = cm.channels.get("wecom")
    if not ch or not hasattr(ch, "get_access_token"):
        return None
    return await ch.get_access_token()


async def _wedrive_post(context, api: str, body: dict) -> dict:
    token = await _get_wecom_token(context)
    if not token:
        return {"errcode": -1, "errmsg": "WeCom channel not ready or no token"}
    url = f"{WEDRIVE_BASE}/{api}"
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, params={"access_token": token}, json=body)
        return resp.json()
    except Exception as e:
        logger.error(f"wedrive {api} error: {e}", exc_info=True)
        return {"errcode": -2, "errmsg": str(e)}


def _ok(data: dict) -> bool:
    return data.get("errcode") == 0


def _err(data: dict) -> str:
    return data.get("errmsg", "unknown error")


# ---------- 空间 ----------


@registry.register(
    name="wedrive_list_spaces",
    description="列出企业微信微盘空间列表（需微盘 API 权限）",
    parameters={
        "type": "object",
        "properties": {
            "userid": {"type": "string", "description": "操作者 userid"}
        },
        "required": ["userid"]
    }
)
async def wedrive_list_spaces(userid: str, context=None) -> str:
    data = await _wedrive_post(context, "space_list", {"userid": userid})
    if not _ok(data):
        return f"失败: {_err(data)}"
    spaces = data.get("space_list", {}).get("item", [])
    if not spaces:
        return "暂无空间"
    lines = [f"- {s.get('space_name', s.get('spaceid', ''))} (spaceid: {s.get('spaceid')})" for s in spaces]
    return "\n".join(lines)


@registry.register(
    name="wedrive_space_info",
    description="获取企业微信微盘指定空间信息",
    parameters={
        "type": "object",
        "properties": {
            "spaceid": {"type": "string", "description": "空间 ID"},
            "userid": {"type": "string", "description": "操作者 userid"}
        },
        "required": ["spaceid", "userid"]
    }
)
async def wedrive_space_info(spaceid: str, userid: str, context=None) -> str:
    data = await _wedrive_post(context, "space_info", {"spaceid": spaceid, "userid": userid})
    if not _ok(data):
        return f"失败: {_err(data)}"
    return json.dumps(data.get("space_info", data), ensure_ascii=False, indent=2)


@registry.register(
    name="wedrive_create_space",
    description="创建企业微信微盘空间",
    parameters={
        "type": "object",
        "properties": {
            "space_name": {"type": "string", "description": "空间名称"},
            "userid": {"type": "string", "description": "创建者 userid"}
        },
        "required": ["space_name", "userid"]
    }
)
async def wedrive_create_space(space_name: str, userid: str, context=None) -> str:
    data = await _wedrive_post(context, "create_space", {"space_name": space_name, "userid": userid})
    if not _ok(data):
        return f"失败: {_err(data)}"
    return f"已创建空间，spaceid: {data.get('spaceid', '')}"


@registry.register(
    name="wedrive_rename_space",
    description="重命名企业微信微盘空间",
    parameters={
        "type": "object",
        "properties": {
            "spaceid": {"type": "string", "description": "空间 ID"},
            "space_name": {"type": "string", "description": "新名称"},
            "userid": {"type": "string", "description": "操作者 userid"}
        },
        "required": ["spaceid", "space_name", "userid"]
    }
)
async def wedrive_rename_space(spaceid: str, space_name: str, userid: str, context=None) -> str:
    data = await _wedrive_post(context, "rename_space", {"spaceid": spaceid, "space_name": space_name, "userid": userid})
    if not _ok(data):
        return f"失败: {_err(data)}"
    return "已重命名"


# ---------- 文件 ----------


@registry.register(
    name="wedrive_list_files",
    description="列出企业微信微盘指定空间/目录下的文件列表；fileid 不传或传 spaceid 表示根目录",
    parameters={
        "type": "object",
        "properties": {
            "spaceid": {"type": "string", "description": "空间 ID"},
            "fileid": {"type": "string", "description": "父目录 fileid，不传则列空间根目录"},
            "userid": {"type": "string", "description": "操作者 userid"}
        },
        "required": ["spaceid", "userid"]
    }
)
async def wedrive_list_files(spaceid: str, userid: str, fileid: Optional[str] = None, context=None) -> str:
    body = {"spaceid": spaceid, "userid": userid}
    if fileid:
        body["fatherid"] = fileid
    else:
        body["fatherid"] = spaceid
    data = await _wedrive_post(context, "file_list", body)
    if not _ok(data):
        return f"失败: {_err(data)}"
    items = data.get("file_list", {}).get("item", [])
    if not items:
        return "该目录下无文件"
    lines = []
    for f in items:
        name = f.get("file_name", f.get("fileid", ""))
        fid = f.get("fileid", "")
        typ = "目录" if f.get("type", 0) == 1 else "文件"
        lines.append(f"- [{typ}] {name} (fileid: {fid})")
    return "\n".join(lines)


@registry.register(
    name="wedrive_file_info",
    description="获取企业微信微盘指定文件/目录详情",
    parameters={
        "type": "object",
        "properties": {
            "fileid": {"type": "string", "description": "文件或目录 ID"},
            "userid": {"type": "string", "description": "操作者 userid"}
        },
        "required": ["fileid", "userid"]
    }
)
async def wedrive_file_info(fileid: str, userid: str, context=None) -> str:
    data = await _wedrive_post(context, "file_info", {"fileid": fileid, "userid": userid})
    if not _ok(data):
        return f"失败: {_err(data)}"
    return json.dumps(data.get("file_info", data), ensure_ascii=False, indent=2)


@registry.register(
    name="wedrive_create_folder",
    description="在企业微信微盘中创建文件夹",
    parameters={
        "type": "object",
        "properties": {
            "spaceid": {"type": "string", "description": "空间 ID"},
            "fatherid": {"type": "string", "description": "父目录 fileid，可为 spaceid 表示根目录"},
            "file_name": {"type": "string", "description": "文件夹名称"},
            "userid": {"type": "string", "description": "操作者 userid"}
        },
        "required": ["spaceid", "fatherid", "file_name", "userid"]
    }
)
async def wedrive_create_folder(spaceid: str, fatherid: str, file_name: str, userid: str, context=None) -> str:
    data = await _wedrive_post(context, "new_space_file", {
        "spaceid": spaceid,
        "fatherid": fatherid,
        "file_type": 1,
        "file_name": file_name,
        "userid": userid,
    })
    if not _ok(data):
        return f"失败: {_err(data)}"
    return f"已创建文件夹，fileid: {data.get('fileid', '')}"


@registry.register(
    name="wedrive_upload_file",
    description="上传本地文件到企业微信微盘指定目录",
    parameters={
        "type": "object",
        "properties": {
            "spaceid": {"type": "string", "description": "空间 ID"},
            "fatherid": {"type": "string", "description": "父目录 fileid"},
            "file_path": {"type": "string", "description": "本地文件路径"},
            "userid": {"type": "string", "description": "操作者 userid"}
        },
        "required": ["spaceid", "fatherid", "file_path", "userid"]
    }
)
async def wedrive_upload_file(spaceid: str, fatherid: str, file_path: str, userid: str, context=None) -> str:
    path = os.path.abspath(file_path)
    if not os.path.exists(path) or not os.path.isfile(path):
        return f"本地文件不存在: {file_path}"
    token = await _get_wecom_token(context)
    if not token:
        return "WeCom 未就绪或无 token"
    url = f"{WEDRIVE_BASE}/file_upload"
    params = {"access_token": token}
    with open(path, "rb") as f:
        content = f.read()
    # 微盘上传多为 multipart 或 先传临时再提交
    # 部分版本接口为 file_upload 传 file + spaceid, fatherid, userid 等
    files = {"file": (os.path.basename(path), content)}
    data_json = json.dumps({"spaceid": spaceid, "fatherid": fatherid, "userid": userid})
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                url, params=params,
                files=files,
                data={"meta": data_json},
            )
        out = resp.json()
    except Exception as e:
        logger.error(f"wedrive file_upload error: {e}", exc_info=True)
        return f"上传异常: {e}"
    if out.get("errcode") != 0:
        return f"失败: {out.get('errmsg')}"
    return f"已上传，fileid: {out.get('fileid', '')}"


@registry.register(
    name="wedrive_download_file",
    description="从企业微信微盘下载文件到本地（需有下载权限）",
    parameters={
        "type": "object",
        "properties": {
            "fileid": {"type": "string", "description": "文件 ID"},
            "save_path": {"type": "string", "description": "保存到的本地路径"},
            "userid": {"type": "string", "description": "操作者 userid"}
        },
        "required": ["fileid", "save_path", "userid"]
    }
)
async def wedrive_download_file(fileid: str, save_path: str, userid: str, context=None) -> str:
    token = await _get_wecom_token(context)
    if not token:
        return "WeCom 未就绪或无 token"
    url = f"{WEDRIVE_BASE}/file_download"
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                url, params={"access_token": token},
                json={"fileid": fileid, "userid": userid},
            )
        if resp.status_code != 200:
            return f"HTTP {resp.status_code}"
        ct = resp.headers.get("content-type", "")
        if "application/json" in ct:
            data = resp.json()
            if data.get("errcode") != 0:
                return f"失败: {data.get('errmsg')}"
            # 可能返回 download_url 需再 GET
            url_download = data.get("download_url")
            if url_download:
                async with httpx.AsyncClient(timeout=60.0) as c2:
                    r2 = await c2.get(url_download)
                content = r2.content
            else:
                return "接口未返回下载内容"
        else:
            content = resp.content
        path = os.path.abspath(save_path)
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "wb") as f:
            f.write(content)
        return f"已保存到 {path}"
    except Exception as e:
        logger.error(f"wedrive_download_file error: {e}", exc_info=True)
        return f"下载失败: {e}"


@registry.register(
    name="wedrive_delete_file",
    description="删除企业微信微盘中的文件或目录",
    parameters={
        "type": "object",
        "properties": {
            "fileid": {"type": "string", "description": "文件或目录 ID"},
            "userid": {"type": "string", "description": "操作者 userid"}
        },
        "required": ["fileid", "userid"]
    }
)
async def wedrive_delete_file(fileid: str, userid: str, context=None) -> str:
    data = await _wedrive_post(context, "file_delete", {"fileid": fileid, "userid": userid})
    if not _ok(data):
        return f"失败: {_err(data)}"
    return "已删除"


@registry.register(
    name="wedrive_move_file",
    description="移动企业微信微盘中的文件/目录到另一目录",
    parameters={
        "type": "object",
        "properties": {
            "fileid": {"type": "string", "description": "要移动的文件/目录 ID"},
            "fatherid": {"type": "string", "description": "目标父目录 ID"},
            "userid": {"type": "string", "description": "操作者 userid"}
        },
        "required": ["fileid", "fatherid", "userid"]
    }
)
async def wedrive_move_file(fileid: str, fatherid: str, userid: str, context=None) -> str:
    data = await _wedrive_post(context, "file_move", {"fileid": fileid, "fatherid": fatherid, "userid": userid})
    if not _ok(data):
        return f"失败: {_err(data)}"
    return "已移动"


@registry.register(
    name="wedrive_rename_file",
    description="重命名企业微信微盘中的文件或目录",
    parameters={
        "type": "object",
        "properties": {
            "fileid": {"type": "string", "description": "文件或目录 ID"},
            "new_name": {"type": "string", "description": "新名称"},
            "userid": {"type": "string", "description": "操作者 userid"}
        },
        "required": ["fileid", "new_name", "userid"]
    }
)
async def wedrive_rename_file(fileid: str, new_name: str, userid: str, context=None) -> str:
    data = await _wedrive_post(context, "file_rename", {"fileid": fileid, "new_name": new_name, "userid": userid})
    if not _ok(data):
        return f"失败: {_err(data)}"
    return "已重命名"
