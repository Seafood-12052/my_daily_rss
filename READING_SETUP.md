# 个人阅读 RSS

每天北京时间 08:00、13:00、20:00，从 sources/sources.json 中有 RSS 地址的来源随机挑选一篇可提取的文章，经 DeepSeek 翻译后与 sources 目录中一段随机古文合并为一条 RSS。已发布的文章链接记录在 reading_history.json；RSS 保留最近 100 条。古籍按独立一行的 `---` 分章，再从章内抽取段落。

## 启用

1. 将本目录及 sources 提交并推送到 GitHub 默认分支。
2. 在仓库 Settings → Environments 的 `DEEPSEEK_API_KEY` 环境中新增 environment secret：`DEEPSEEK_API_KEY`。也可以使用同名 repository secret。
3. 在 Settings → Pages → Build and deployment 中将 Source 设为 GitHub Actions。
4. 在 Actions 中运行“三次阅读推送”的 workflow_dispatch 测试，成功后订阅 `https://<用户名>.github.io/<仓库名>/feed.xml`。默认定时执行可能受到 GitHub 排队延迟；公开 Pages 的内容可被任何人访问。

无法抓取、无正文、正文超过 24000 字符或翻译失败的文章会跳过；若所有候选均失败，任务报错且不发布。RSS 链接指向原文，部分阅读器可能会自行抓取原网页而不显示 RSS 中的译文。API 调用会产生 DeepSeek 费用。

本地运行：安装 `reading_requirements.txt` 中的依赖，设置环境变量 `DEEPSEEK_API_KEY`，然后运行 `python reading_feed.py`，生成的文件为 `reading_site/feed.xml`。
