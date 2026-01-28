from tools.registry import registry
from typing import Optional
import warnings

# 忽略包改名警告
warnings.filterwarnings("ignore", message=".*duckduckgo_search.*renamed.*")

import httpx
from bs4 import BeautifulSoup


@registry.register(
    name="web_search",
    description="搜索互联网获取信息",
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "搜索关键词"},
            "num_results": {"type": "integer", "description": "返回结果数量", "default": 5}
        },
        "required": ["query"]
    }
)
async def web_search(query: str, num_results: int = 5, context=None) -> str:
    """
    搜索网页并返回结果摘要
    
    流程:
    1. 使用 DuckDuckGo 搜索（尝试多个后端）
    2. 格式化返回结果（标题、链接、摘要）
    3. 处理网络错误
    
    返回: 格式化的搜索结果或错误信息
    """
    try:
        # 尝试导入新包名，如果失败则用旧包名
        try:
            from ddgs import DDGS
        except ImportError:
            from duckduckgo_search import DDGS
        
        if not query or not query.strip():
            return "错误: 搜索关键词不能为空"
        
        if num_results < 1:
            num_results = 1
        elif num_results > 20:
            num_results = 20  # 限制最大结果数
        
        # 使用 DuckDuckGo 搜索
        # 新版 ddgs 可用后端: brave, duckduckgo, google, grokipedia, mojeek, wikipedia, yahoo, yandex
        results = []
        with DDGS() as ddgs:
            # 依次尝试不同后端，优先使用支持中文的后端
            backends_to_try = ["duckduckgo", "google", "yahoo", "brave", None]
            for backend in backends_to_try:
                try:
                    if backend:
                        # 设置区域为中国，以获取更好的中文结果
                        results = list(ddgs.text(
                            query, 
                            max_results=num_results, 
                            backend=backend,
                            region="cn-zh"  # 中国区域，中文结果
                        ))
                    else:
                        results = list(ddgs.text(query, max_results=num_results, region="cn-zh"))
                    if results:
                        break
                except Exception:
                    continue
        
        if not results:
            return f"未找到与 '{query}' 相关的搜索结果"
        
        # 格式化结果
        formatted_results = []
        formatted_results.append(f"搜索关键词: {query}\n")
        formatted_results.append(f"找到 {len(results)} 个结果:\n")
        formatted_results.append("-" * 60)
        
        for i, result in enumerate(results, 1):
            title = result.get("title", "无标题")
            href = result.get("href", "")
            body = result.get("body", "无摘要")
            
            formatted_results.append(f"\n[{i}] {title}")
            formatted_results.append(f"链接: {href}")
            formatted_results.append(f"摘要: {body}")
            formatted_results.append("-" * 60)
        
        return "\n".join(formatted_results)
        
    except ImportError:
        return "错误: 缺少 duckduckgo_search 库，请运行 pip install duckduckgo-search"
    except Exception as e:
        # 处理网络错误和其他异常
        error_msg = str(e)
        if "network" in error_msg.lower() or "connection" in error_msg.lower():
            return f"错误: 网络连接失败，请检查网络设置后重试"
        elif "timeout" in error_msg.lower():
            return f"错误: 请求超时，请稍后重试"
        else:
            return f"错误: 搜索失败 - {error_msg}"


@registry.register(
    name="fetch_url",
    description="访问网页并提取正文内容",
    parameters={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "要访问的网页 URL"},
            "max_length": {"type": "integer", "description": "返回内容最大长度", "default": 5000}
        },
        "required": ["url"]
    }
)
async def fetch_url(url: str, max_length: int = 5000, context=None) -> str:
    """
    访问指定 URL，提取网页正文内容
    
    流程:
    1. 使用 httpx 发送 HTTP 请求（10秒超时）
    2. 使用 BeautifulSoup 解析 HTML
    3. 移除 script、style、nav、footer、header 等标签
    4. 提取正文文本并截断到指定长度
    
    返回: 格式化的网页内容（URL、标题、正文）
    """
    try:
        if not url or not url.strip():
            return "错误: URL 不能为空"
        
        # 验证 URL 格式
        url = url.strip()
        if not url.startswith(('http://', 'https://')):
            url = 'https://' + url
        
        # 设置请求头，模拟浏览器
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        
        # 发送 HTTP 请求，设置 10 秒超时
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()  # 检查 HTTP 错误状态码
        
        # 解析 HTML
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # 获取标题
        title_tag = soup.find('title')
        title = title_tag.get_text(strip=True) if title_tag else "无标题"
        
        # 移除不需要的标签
        for tag in soup(['script', 'style', 'nav', 'footer', 'header', 'aside', 'iframe', 'noscript']):
            tag.decompose()
        
        # 提取正文文本
        text = soup.get_text(separator='\n', strip=True)
        
        # 清理多余的空行
        lines = [line.strip() for line in text.split('\n') if line.strip()]
        text = '\n'.join(lines)
        
        # 截断内容
        if len(text) > max_length:
            text = text[:max_length] + '...\n[内容已截断]'
        
        # 格式化返回
        result = f"URL: {url}\n"
        result += f"标题: {title}\n"
        result += f"内容:\n{text}"
        
        return result
        
    except httpx.TimeoutException:
        return f"错误: 请求超时（超过 10 秒），无法访问 {url}"
    except httpx.HTTPStatusError as e:
        return f"错误: HTTP {e.response.status_code} - 无法访问 {url}"
    except httpx.RequestError as e:
        return f"错误: 网络请求失败 - {str(e)}"
    except Exception as e:
        error_msg = str(e)
        if "network" in error_msg.lower() or "connection" in error_msg.lower():
            return f"错误: 网络连接失败，请检查网络设置后重试"
        else:
            return f"错误: 解析网页失败 - {error_msg}"
