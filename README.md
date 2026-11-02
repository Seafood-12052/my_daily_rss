# 我的每日随机阅读

在云端定时生成中文 RSS，供手机和墨水屏阅读器订阅。每天计划选三篇新文章（5、10、10 分钟），并在三个时段各从古籍库随机选出一组阅读材料。英文文章使用你配置的 OpenAI 兼容翻译接口。

**目前这是需要配置后才能运行的项目，不是已经上线的订阅地址。** 由于发布的是完整正文或译文，示例来源均默认关闭；先核实每个来源允许抓取、翻译并公开再发布，并在 config.json 对应来源上将 allow_full_republish 设为 true。来源级开关不代表所有文章都有许可，还需核实各篇内容与转载的第三方材料。来源不够、正文提取失败、时长不符或翻译不可用时，系统会少推，不会用摘要冒充完整文章。

## 部署

1. 把这个目录上传到你的 GitHub **公开仓库**，确保 .github/workflows/daily.yml 也在仓库中。
2. 仓库 Settings → Pages → Build and deployment → Source 选择 **GitHub Actions**。Settings → Actions → General 中允许工作流读写仓库。
3. 在 Settings → Secrets and variables → Actions 添加 secret：TRANSLATION_API_KEY；添加 variables：TRANSLATION_BASE_URL（兼容 Chat Completions 的 API 基础地址，例如服务商的 /v1 地址）、TRANSLATION_MODEL（该服务的模型名）。可选添加 FEED_SITE_URL，填 Pages 站点 URL。不要把密钥写进仓库。
4. 编辑 config.json，添加足够多授权允许公开全文及译文的 RSS 来源、对应语言和 allow_full_republish。示例来源只是待审核候选，并未断言其授权许可或保证链接可用。每个来源须设置 name、url、language（en 或 zh）、allow_full_republish。
5. 在 Actions 页面选择“每日阅读”，点击 Run workflow。成功后订阅 `https://你的用户名.github.io/仓库名/feed.xml`。用户名、仓库名及具体页面地址以 GitHub Pages 设置显示为准。此 URL 及内容是公开的。

北京时间 06:17、12:17 和 18:17 各运行一次，每次生成当时段的一组古籍；文章的后两次运行用于补上前面未成功的条目。同一时段重复运行不会重复生成古籍。实际运行可能延迟或被丢弃，阅读器也有自己的更新频率；手动运行按当时北京时间所属时段生成。公开仓库 60 天无活动可能禁用定时工作流。失败可在 Actions 查看日志并手动重试。自动保存的 state.json 是公开的，包含推送记录，勿放入敏感信息。

## 调整兴趣和古籍

直接在 GitHub 网页编辑 config.json 的 topics；兴趣抽签先随机选一类，再依次尝试该类和其他有候选的兴趣领域。其他 30% 从全部来源抽取，也可能属于兴趣领域。如果所有兴趣文章都不满足正文、翻译或长度要求，会尝试非兴趣文章，并明确标注“兴趣候选不足”；此时实际比例不保证达到 70%。候选按最近 7、30、90 天分层，层内偏向较新文章；目前只按标题和摘要中的关键词粗筛，可能误判。阅读时长按中文字符数估算，每分钟约 150–360 字；严格长度门槛可能导致部分日子不足三篇。来源网页拒绝访问或翻译 API 出错时应查看 Actions 生成日志。

编辑 classics.json，添加对象：book、title、text、notes、summary、source、enabled、tags。每条 text 是连续原文；系统按句末标点分段，每次推送一段，目标约 330 个汉字，实际长度取决于原文和句子边界。附上适用于该段的词语解释、原选段大意和出处。notes 是“词语、解释”二元数组，summary 是一段简短大意。enabled 设为 false，或者 tags 中包含“暂不阅读”或“已读”，即跳过。同一天的选段不重复，全部读完后会循环；请继续添加选段以减少重复。请自行补充可靠版本和出处，医学内容仅供历史阅读。

每次运行都记录状态并上传 Pages：state.json 是历史与当天抽签计划，public/feed.xml 是公开输出。不要手工删除 state.json，否则会丢失去重记录。RSS 保留最近 30 天；翻译失败不会发布未翻译的英文文章。仓库公开，请勿上传付费内容或私密资料。

## 本地试运行

```sh
python -m pip install -r requirements.txt
python generate.py
```

生成 public/feed.xml。若没有启用具备授权的来源或未配置翻译，仅会看到可用古籍选段。模型接口请求可能产生费用；请在服务商处设置预算上限。
