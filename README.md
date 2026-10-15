# xhs-notes-archiver

> 小红书笔记归档器 —— **用 curl_cffi 直接抓笔记正文与图片，不需要 XHS-Downloader、不需要 fastmcp、不需要 Playwright**。
>
> 起因：现成工具很好用，但装它的依赖（`fastmcp` → `mcp` → `jsonschema` → …）能卡十几分钟，
> 而它真正干活的只有「请求页面 → 解析内嵌 JSON」这一步。这个仓库把那一步单独拎出来，150 行、一个依赖。

## 特性

- **单依赖**：只要 `curl_cffi`（它负责模拟真实 Chrome 的 TLS 指纹，这是能不能拿到数据的关键）
- **抓正文**：标题、正文、话题标签、发布时间、作者、图片清单
- **抓图片**：原图直链直接落盘
- **反风控**：内置慢速模式（随机间隔 + 失败退避 + 断点续传）
- **链接规范化**：把缺 `xsec_token` 的链接补全，并与已有的 note_id 清单交叉校验

## 为什么需要它：三个必须知道的坑

### 坑 1：普通请求只能拿到"空壳页面"

```
urllib / requests        → 36 KB HTML，只有登录墙，没有数据
curl_cffi (impersonate)  → 65 KB HTML，含 window.__INITIAL_STATE__ 完整数据
```

区别在 **TLS 指纹**。小红书的网关会识别客户端指纹，普通 Python 请求被判为爬虫后只回空壳。

```python
from curl_cffi import requests as creq
r = creq.get(url, impersonate="chrome146")   # ← 这一行是成败关键
```

### 坑 2：数据在 `d["note"]["noteDetailMap"]`，不是 `d["noteDetailMap"]`

少一层就会读到全 `None`（表现为"标题、正文、图片全空，但请求成功"）：

```python
state = json.loads(html.split("window.__INITIAL_STATE__=", 1)[1].split("</script>", 1)[0]
                   .strip().rstrip(";").replace("undefined", "null"))
note = state["note"]["noteDetailMap"][note_id]["note"]   # ← 三层
```

### 坑 3：风控是"先给 30 条再拦"

用 1.2 秒/条的节奏跑到第 30 条左右，之后全部返回空壳页面（`无 __INITIAL_STATE__`），
而且**冷却 5 分钟不够**。本仓库的 `--slow` 模式用 **6-14 秒随机间隔 + 失败退避 45-75 秒**。

## 安装

```bash
pip install curl_cffi
```

## 使用

### 1. 准备链接清单

从浏览器里把笔记链接抓出来（推荐用官方的 [XHS-Downloader 用户脚本](https://github.com/JoeanAmier/XHS-Downloader) 的「提取账号发布作品链接」功能），
或直接用本仓库的链接工具规范化：

```bash
python src/xhs_links.py 聊天记录.txt --out links.txt --csv links.csv
```

链接格式（**必须带 `xsec_token`，否则打不开**）：

```
https://www.xiaohongshu.com/discovery/item/<24位note_id>?xsec_token=<token>&xsec_source=pc_share
```

### 2. 抓取正文与图片

```bash
# 常规模式（每条间隔 2~5 秒）
python src/xhs_fetch_notes.py --links links.txt --out ./notes

# 慢速模式（被风控后使用；支持断点续传，中断直接重跑）
python src/xhs_fetch_notes.py --links links.txt --out ./notes \
    --min-delay 8 --max-delay 20 --backoff 900

# 只要正文、不要图片
python src/xhs_fetch_notes.py --links links.txt --out ./notes --skip-images
```

### 完整流程

```
┌──────────────────────────────────────────────────────────────┐
│ 1. 收集链接                                                   │
│    浏览器插件 / 油猴脚本「提取账号发布作品链接」                │
│    或本仓库的 xhs_links.py 从任意文本里提取                   │
└───────────────────────────┬──────────────────────────────────┘
                            ↓
┌──────────────────────────────────────────────────────────────┐
│ 2. 规范化（关键：必须带 xsec_token）                          │
│    python src/xhs_links.py 输入.txt --out links.txt --check 收藏.json │
└───────────────────────────┬──────────────────────────────────┘
                            ↓
┌──────────────────────────────────────────────────────────────┐
│ 3. 抓取（curl_cffi 模拟浏览器指纹 → 解析 __INITIAL_STATE__）  │
│    python src/xhs_fetch_notes.py --links links.txt --out notes │
└───────────────────────────┬──────────────────────────────────┘
                            ↓
   notes/<note_id>_<标题>.md + notes/图片/*.jpg + 笔记正文索引.csv
```

### 遇到风控怎么办

| 症状 | 原因 | 处理 |
|---|---|---|
| 部分条目"空壳页面（风控）" | 请求太快 | 用 `--min-delay 8 --max-delay 20`，**并让失败的条目隔 15~30 分钟再跑**（脚本支持断点续传） |
| 全部条目都空壳 | 触发 IP/会话级限流 | 停 30~60 分钟；仍不行就换网络环境（如手机热点） |
| 想彻底避开 | — | 改用浏览器内跑的油猴脚本（真实会话 + 真实 IP） |

实测节奏参考：**1.2 秒/条跑到第 30 条左右被拦；6~14 秒/条可稳定跑完 50+ 条。**

输出结构：

```
notes/
├── <note_id>_<标题>.md        # 正文 Markdown（含元信息、正文、图片引用）
├── 图片/<note_id>_01.jpg      # 原图
└── 笔记正文索引.csv            # note_id / 标题 / 字数 / 图片数 / 发布时间 / 作者
```

Markdown 长这样：

```markdown
# 你家哈基米炸毛了吗？

- **note_id**：`0123456789abcdef01234567`
- **类型**：video ｜ **发布**：2026-03-05
- **作者**：某某（`aaaaaaaaaaaaaaaaaaaaaaaa`）
- **标签**：#铲屎官的日常 #惊蛰 #节气海报
- **原链接**：https://...

## 正文

春雷始动，万物生长，就是对猫猫不太友好~

## 图片

![](图片/69b11509000000002303bd5e_01.jpg)
```

## 已知边界

| 项 | 说明 |
|---|---|
| **互动数据** | `赞/藏/评` 需登录态，未登录时为空（正文和图片不受影响） |
| **视频笔记** | 正文能抓；视频文件建议用 [yt-dlp](https://github.com/yt-dlp/yt-dlp)（它支持小红书，但**只支持视频笔记**，图文会报 `No video formats found!`） |
| **风控** | 见坑 3。设成几十毫秒一条会被限频甚至封号，请自重 |
| **接口变动** | 依赖页面内嵌 JSON 结构，小红书改版后可能失效 |

## 只用于导出你自己的内容

本工具设计用途是**归档自己账号的笔记**。批量抓取他人内容涉及隐私与平台规则，请勿为之。

## 相关工具（都比我这个完善，也都有各自的坑）

| 工具 | 说明 | 注意 |
|---|---|---|
| [JoeanAmier/XHS-Downloader](https://github.com/JoeanAmier/XHS-Downloader) | 全能型，GUI/TUI/API 都有 | `fastmcp` 依赖很重；**核心下载其实不需要它** |
| [PCPrincipal67/xhs-favorites-exporter](https://github.com/PCPrincipal67/xhs-favorites-exporter) | 一键导出收藏列表 | **只适配"收藏"页**；在自己主页跑会只拿到 note_id（标题/token 全空） |
| [yt-dlp](https://github.com/yt-dlp/yt-dlp) | 视频下载最稳 | 只吃视频笔记 |

## License

MIT
