# Daily arXiv Digest

这个项目会每天从你关注的 arXiv 领域抓取新论文，结合 Zotero 中的兴趣标签和关键词偏好做推荐排序，为每篇论文标注兴趣星级、生成中文单篇摘要，并通过邮件发送每日总结。

## 功能

- 按 arXiv 分类抓取最新论文，例如 `astro-ph.GA`、`astro-ph.CO`。
- 用关键词进行基础推荐排序。
- 读取 Zotero 条目的标题、摘要和标签，根据你的兴趣记录调整推荐分数。
- 将推荐分数映射为 1-5 星兴趣评级，并在邮件中醒目展示。
- 使用 OpenAI 模型生成中文每日总览和每篇论文的中文单篇摘要。
- 通过 GitHub Actions 每天定时运行并发送邮件。

## 本地运行

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

编辑 `.env` 后，先预览抓取、评分和摘要结果：

```powershell
python -m scripts.preview_digest
```

确认邮件配置无误后运行完整发送流程：

```powershell
python -m arxiv_digest.main
```

## 稳定性配置

建议把“候选池大小、邮件数量、LLM 阅读数量”分开控制：

```text
ARXIV_FETCH_MAX_RESULTS=100
DIGEST_TIMEZONE=Asia/Shanghai
ARXIV_REQUEST_TIMEOUT=60
ARXIV_REQUEST_RETRIES=3
MAX_PAPERS=12
MAX_LLM_PAPERS=12
MAX_ABSTRACT_CHARS=1800
MIN_RATING=1
```

- `ARXIV_FETCH_MAX_RESULTS`：每次最多从 arXiv API 拉取多少条候选论文，防止单次读取过多。
- `DIGEST_TIMEZONE`：用于定义“昨天”的时区，默认 `Asia/Shanghai`，即按北京时间自然日筛选。
- `ARXIV_REQUEST_TIMEOUT`：单次 arXiv API 请求的超时时间，单位秒。
- `ARXIV_REQUEST_RETRIES`：arXiv API 请求失败或超时时的重试次数。
- `MAX_PAPERS`：排序和过滤后，邮件最多展示多少篇论文。
- `MAX_LLM_PAPERS`：最多让 LLM 精读并生成单篇中文摘要的论文数量。
- `MAX_ABSTRACT_CHARS`：每篇英文摘要送入 LLM 前最多保留多少字符，避免 prompt 过长。
- `MIN_RATING`：最低推送星级；设为 `2` 可以过滤掉低匹配文章，设为 `1` 则尽量保留。

## arXiv 配置

常用天文分类可以写在 `ARXIV_CATEGORIES`：

```text
astro-ph.GA,astro-ph.CO,astro-ph.HE,astro-ph.IM,astro-ph.SR,astro-ph.EP
```

`ARXIV_KEYWORDS` 用英文逗号分隔，例如：

```text
galaxy,cosmology,black hole,star formation,supernova,large language model
```

## 星级评分

系统先计算内部推荐分数，再映射为邮件中的兴趣星级：

- 5 星：高度符合兴趣，通常来自强关键词命中和高权重 Zotero 相似记录。
- 4 星：明显相关，值得优先阅读。
- 3 星：中等相关，可以浏览摘要后决定。
- 2 星：属于关注领域，但个性化信号较弱。
- 1 星：保底收录，与你当前兴趣匹配较低。

当前星级规则在 `arxiv_digest/recommender.py` 的 `score_to_rating()` 中，可以按你的反馈继续调校。

## Zotero 兴趣标签

当前实现会读取 Zotero 条目的标题、摘要和标签，用标签给相似的新论文加权。

推荐使用这些正向标签：

- `favorite`
- `important`
- `interested`
- `high-interest`
- `must-read`
- `relevant`

也可以用这些负向标签降低相似论文排名：

- `not-interesting`
- `irrelevant`
- `low-interest`
- `skip`

需要的 Zotero secrets：

- `ZOTERO_API_KEY`
- `ZOTERO_USER_ID`

如果只想参考某个 Zotero collection，把 collection key 填到 `ZOTERO_COLLECTION_ID`。

## GitHub Actions 配置

把项目推到 GitHub 后，在仓库设置中配置以下内容。

Secrets:

- `OPENAI_API_KEY`
- `ZOTERO_API_KEY`
- `ZOTERO_USER_ID`
- `SMTP_HOST`
- `SMTP_PORT`
- `SMTP_USERNAME`
- `SMTP_PASSWORD`
- `EMAIL_FROM`
- `EMAIL_TO`

Variables:

- `ARXIV_CATEGORIES`
- `ARXIV_KEYWORDS`
- `DIGEST_TIMEZONE`
- `ARXIV_FETCH_MAX_RESULTS`
- `ARXIV_REQUEST_TIMEOUT`
- `ARXIV_REQUEST_RETRIES`
- `MAX_PAPERS`
- `MAX_LLM_PAPERS`
- `MAX_ABSTRACT_CHARS`
- `MIN_RATING`
- `LOOKBACK_DAYS`
- `OPENAI_MODEL`
- `ZOTERO_LIBRARY_TYPE`
- `ZOTERO_COLLECTION_ID`

工作流位于 `.github/workflows/daily-arxiv-digest.yml`，默认每天 UTC 00:10 运行，也就是北京时间 08:10。

## 邮箱说明

如果使用 Gmail，`SMTP_PASSWORD` 通常需要填写 App Password，而不是登录密码。

## 降级策略

- 如果没有配置 OpenAI key，邮件仍会发送，但中文单篇摘要会退回到基于英文摘要的占位文本。
- 如果 Zotero API 暂时不可用，系统会只使用 arXiv 分类和关键词排序。
- 如果某篇论文的 LLM 摘要缺失，只会影响该篇论文，不会阻塞整封邮件。
- 如果主流程失败且 SMTP 配置完整，系统会发送一封失败告警邮件，方便你及时发现问题。

## 后续可增强

- 使用 embedding 替代当前关键词重叠相似度，让 Zotero 偏好匹配更准确。
- 把每天的推荐反馈写回 Zotero 或 GitHub artifacts，形成更长期的偏好记忆。
- 在邮件中加入“喜欢/不喜欢”反馈链接，用于自动更新兴趣模型。
