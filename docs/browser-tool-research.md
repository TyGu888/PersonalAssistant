# Browser 工具健壮性与健全性研究

## 当前能力概览

| 能力 | 状态 | 说明 |
|------|------|------|
| **打开/关闭** | ✅ | `browser_open` / `browser_close`，单例无头 Chromium |
| **导航** | ✅ | `browser_goto`，`wait_until=domcontentloaded`，30s 超时 |
| **点击** | ✅ | `browser_click`，支持 CSS / `text=...` / `[aria-label=...]`，自动等待可点击、滚动到视区 |
| **填表** | ✅ | `browser_fill`，selector + value |
| **文本快照** | ✅ | `browser_snapshot`：URL、标题、`body.innerText`，过长截断 |
| **截图（“看”）** | ✅ 已补 | `browser_screenshot`：整页或元素截图，保存到 `data/screenshots/` |

## 能否“看”（截图）？

- **之前**：不能。`browser_snapshot` 只返回**文本快照**（innerText），不是视觉截图，Agent 无法“看到”页面布局、图片、按钮位置等。
- **现在**：已增加 `browser_screenshot`，使用 Playwright `page.screenshot()`，可截整页或指定元素，保存为 PNG，返回路径。  
- **与 Vision 的衔接**：当前工具返回仅为字符串，Agent 不会自动把截图路径当作下一轮输入的图片。若要让模型“看图”，需在框架侧扩展（例如 ToolResult 支持 `images` 列表，或在下一轮将截图路径加入 user message 的 images）。

## 点击是否健全？

- **有** `browser_click(selector)`，行为健全：
  - Playwright 会等待元素可见、可点击、稳定（无动画），超时 30s。
  - 自动 scroll into view。
  - 支持 CSS、`text=按钮文字`、`[aria-label=...]` 等。
- **注意**：若页面动态加载，Agent 应先 `browser_snapshot` 或 `browser_screenshot` 确认 DOM/视觉再选 selector；必要时可增加 `browser_wait_selector` 等待某元素出现。

## 其他健壮性

- **前置检查**：所有工具统一通过 `_ensure_page()` 检查浏览器是否已打开，未打开时返回明确错误，不抛异常。
- **异常**：全部 `try/except` 后返回字符串说明，便于 Agent 理解。
- **超时**：`_context.set_default_timeout(DEFAULT_TIMEOUT_MS)`（30s），避免无限等待。
- **单例单页**：当前仅一个 page，无多 tab；若浏览器进程异常退出，`_page` 可能仍非 None，后续操作会失败，可考虑在 `_ensure_page` 中增加“页面是否已关闭”的轻量检测并自动清理（可选优化）。
 
## 系统与平台（并非“所有系统都无问题”）

Browser 工具依赖 Playwright + Chromium，**不同系统需要不同准备**：

| 系统 | 说明 |
|------|------|
| **macOS** | 一般可直接用。Intel / Apple Silicon (M1/M2) 均有 Chromium 构建。`pip install playwright && playwright install chromium` 即可。 |
| **Windows** | 一般可直接用。Windows 10 (1809+)/11、x64/ARM64 支持。部分环境杀毒或策略可能拦截浏览器自动化。 |
| **Linux** | **常需额外系统依赖**。仅 `playwright install chromium` 可能不够，需安装 Chromium 依赖库，否则启动会报错（如缺少 libgbm、libnss3 等）。<br>• Ubuntu/Debian: `sudo apt-get install -y libnss3 libatk-bridge2.0-0 libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 libxrandr2 libgbm1 libxss1 libasound2`<br>• 或使用 Playwright 自动装依赖: `playwright install-deps chromium`（需系统允许）<br>• **Docker 就是 Linux**：容器多为精简镜像，往往缺上述依赖，需在 Dockerfile 里装依赖或基于 Playwright 官方镜像；`headless=True` 无需显示器。 |
| **ARM64（如树莓派、部分云主机）** | Playwright 提供 Chromium ARM 构建，但并非所有版本/环境都测试充分，若遇启动失败需查 Playwright 文档或 issue。 |

**结论**：在 macOS/Windows 上通常“没问题”；在 **Linux**（含 Docker 容器）上需要按上面安装系统依赖或使用带依赖的镜像，否则 `browser_open` 可能失败。

## 依赖与配置

- 需安装：`pip install playwright && playwright install chromium`（Linux 上视情况加 `playwright install-deps chromium` 或手动装系统依赖）
- 在 `config.yaml` 的 `routing.tools` 中已包含 `browser_*`；Gateway 通过 `gateway/app.py` 的 `import tools.browser` 完成注册。
