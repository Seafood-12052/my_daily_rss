# 个人阅读 RSS

每天北京时间 08:00、13:00、20:00，分别推送两条 RSS：随机文章的中文译文，以及随机古籍选段和 DeepSeek 生成的白话理解与阅读提示。已发布的文章链接记录在 reading_history.json；RSS 保留最近 100 条。古籍按独立一行的 `---` 分章，再从章内抽取段落。

## 启用

1. 将本目录及 sources 提交并推送到 GitHub 默认分支。
2. 在仓库 Settings → Environments 的 `DEEPSEEK_API_KEY` 环境中新增 environment secret：`DEEPSEEK_API_KEY`。也可以使用同名 repository secret。
3. 在 Settings → Pages → Build and deployment 中将 Source 设为 GitHub Actions。
4. 在 Actions 中运行“三次阅读推送”的 workflow_dispatch 测试，成功后订阅 `https://<用户名>.github.io/<仓库名>/feed.xml`。默认定时执行可能受到 GitHub 排队延迟；公开 Pages 的内容可被任何人访问。

先随机选择并抓取一篇可用文章，再调用一次翻译 API；另外调用一次 API 整理古籍阅读提示。任一调用失败时任务报错且不发布。无法抓取、无正文或正文超过 24000 字符的候选会跳过。两个 RSS 条目各有阅读页面，文章页面保留原文链接。AI 阅读提示仅供参考。API 调用会产生 DeepSeek 费用。

本地运行：安装 `reading_requirements.txt` 中的依赖，设置环境变量 `DEEPSEEK_API_KEY`，然后运行 `python reading_feed.py`，生成的文件为 `reading_site/feed.xml`。
