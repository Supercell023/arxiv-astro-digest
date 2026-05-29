# Daily arXiv Digest

每天从 arXiv 抓取你关注领域的新论文，结合关键词和 Zotero 兴趣偏好做个性化推荐，用 LLM 生成中文总览和单篇摘要，并通过 GitHub Actions 自动发送邮件。

## 已完成的主要改进

- **arXiv 抓取**：支持按多个 arXiv category 抓取论文，例如 `astro-ph.GA,astro-ph.CO`。
- **抓取数量控制**：用 `ARXIV_FETCH_MAX_RESULTS` 控制候选池大小，避免一次读取太多论文。
- **北京时间自然日**：用 `DIGEST_TIMEZONE=Asia/Shanghai` 定义“昨天”，不再只是最近 24 小时。
- **推荐排序**：结合关键词、arXiv 分类和 Zotero 标签偏好计算推荐分数。
- **星级评价**：把推荐分数映射为 1-5 星，并在邮件中展示。
- **中文摘要**：LLM 一次生成每日总览和每篇论文的中文单篇摘要。
- **LLM 输入保护**：用 `MAX_LLM_PAPERS` 和 `MAX_ABSTRACT_CHARS` 限制模型输入，降低成本和出错概率。
- **最低星级过滤**：用 `MIN_RATING` 过滤低匹配论文。
- **Zotero 降级**：Zotero 不可用时，系统仍会用关键词和分类排序。
- **LLM 降级**：OpenAI/兼容接口不可用时，邮件仍可发送，摘要退回到基础文本。
- **失败告警**：主流程失败且 SMTP 可用时，会发送失败告警邮件。
- **arXiv 429 处理**：当 arXiv 返回 `HTTP 429 Too Many Requests` 时，使用指数退避重试。
- **429 graceful 模式**：若 arXiv 持续限流，默认发送告警邮件并让 GitHub Actions 正常结束，避免每天红灯。
- **兼容 OpenAI-like API**：支持 OpenAI、DeepSeek、通义千问、Moonshot、Ollama 等兼容 OpenAI API 的模型。
- **HTML 邮件模板**：使用 Jinja2 模板渲染更清晰的邮件内容。

## 本地运行

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

编辑 `.env` 后，先预览结果：

```powershell
python -m scripts.preview_digest
```

确认配置无误后发送邮件：

```powershell
python -m arxiv_digest.main
```

## GitHub Actions 配置

把项目推送到 GitHub 后，进入仓库：

```text
Settings -> Secrets and variables -> Actions
```

你需要分别配置 `Secrets` 和 `Variables`。

### Secrets

这些是敏感信息，必须放在 `Secrets` 中：

| Name | 示例/说明 |
| --- | --- |
| `OPENAI_API_KEY` | OpenAI 或兼容接口的 API key |
| `SMTP_HOST` | 例如 `smtp.gmail.com` |
| `SMTP_PORT` | 例如 `587`，SSL 可用 `465` |
| `SMTP_USERNAME` | 发件邮箱账号 |
| `SMTP_PASSWORD` | 邮箱授权码或 SMTP 密码 |
| `EMAIL_FROM` | 发件地址 |
| `EMAIL_TO` | 收件地址 |
| `ZOTERO_API_KEY` | 可选，Zotero API key |
| `ZOTERO_USER_ID` | 可选，Zotero user ID |

### Variables

这些是普通配置，放在 `Variables` 中。推荐先使用下表的值跑通项目，再逐步调整。

| Name | 推荐值 | 说明 |
| --- | --- | --- |
| `ARXIV_CATEGORIES` | `astro-ph.GA,astro-ph.CO` | 关注的 arXiv 分类，逗号分隔 |
| `ARXIV_KEYWORDS` | `galaxy,cosmology,black hole,star formation` | 兴趣关键词，逗号分隔 |
| `DIGEST_TIMEZONE` | `Asia/Shanghai` | 用于定义“昨天”的时区 |
| `ARXIV_FETCH_MAX_RESULTS` | `50` | arXiv 候选池大小，越大越容易触发 429 |
| `ARXIV_REQUEST_TIMEOUT` | `60` | arXiv 单次请求超时秒数 |
| `ARXIV_REQUEST_RETRIES` | `5` | arXiv 请求失败后的最大尝试次数 |
| `ARXIV_RATE_LIMIT_BACKOFF_BASE` | `60` | 429 指数退避基础等待秒数 |
| `ARXIV_RATE_LIMIT_BACKOFF_MAX` | `600` | 429 指数退避最大等待秒数 |
| `ARXIV_RATE_LIMIT_BACKOFF_JITTER` | `30` | 每次等待额外加入的随机秒数上限 |
| `ARXIV_RATE_LIMIT_GRACEFUL` | `true` | 持续 429 时发送告警并让 workflow 正常结束 |
| `MAX_PAPERS` | `12` | 邮件最多展示论文数 |
| `MAX_LLM_PAPERS` | `12` | 最多让 LLM 生成摘要的论文数 |
| `MAX_ABSTRACT_CHARS` | `1800` | 每篇英文摘要送入 LLM 前的最大字符数 |
| `MIN_RATING` | `1` | 最低推送星级，建议初期保留 `1` |
| `LOOKBACK_DAYS` | `1` | 回顾天数，通常保持 `1` |
| `OPENAI_MODEL` | `gpt-4o-mini` | 使用的模型名称 |
| `OPENAI_BASE_URL` | 留空 | 使用 OpenAI 官方时留空；兼容接口时填写 base URL |
| `ZOTERO_LIBRARY_TYPE` | `user` | Zotero 类型，可为 `user` 或 `group` |
| `ZOTERO_COLLECTION_ID` | 留空 | 可选，仅参考某个 Zotero collection |

## arXiv 429 指数退避

当 arXiv 返回 `HTTP 429 Too Many Requests` 且没有提供 `Retry-After` 头时，程序按下面的公式等待：

```text
delay = min(base * 2^attempt + random(0, jitter), max)
```

默认配置下，前几次等待大约是：

```text
60-90s, 120-150s, 240-270s, 480-510s, 最高 600s
```

如果 arXiv 返回了 `Retry-After`，程序会优先尊重 arXiv 指定的等待时间。

## LLM 模型配置示例

OpenAI：

```ini
OPENAI_API_KEY=sk-xxxxxxxx
OPENAI_MODEL=gpt-4o-mini
OPENAI_BASE_URL=
```

DeepSeek：

```ini
OPENAI_API_KEY=你的 DeepSeek key
OPENAI_BASE_URL=https://api.deepseek.com
OPENAI_MODEL=deepseek-chat
```

通义千问：

```ini
OPENAI_API_KEY=你的阿里云 key
OPENAI_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
OPENAI_MODEL=qwen-plus
```

Ollama：

```ini
OPENAI_API_KEY=ollama
OPENAI_BASE_URL=http://localhost:11434/v1
OPENAI_MODEL=qwen2.5:7b
```

## Zotero 标签

正向标签会提高相似论文的推荐分数：

- `favorite`
- `important`
- `interested`
- `high-interest`
- `must-read`
- `relevant`

负向标签会降低相似论文的推荐分数：

- `not-interesting`
- `irrelevant`
- `low-interest`
- `skip`

## 星级评分

内部推荐分数由关键词命中、Zotero 相似度和分类加成组成，然后映射为星级：

| 分数范围 | 星级 | 含义 |
| --- | --- | --- |
| `>= 5.0` | 5 星 | 高度符合兴趣 |
| `>= 3.0` | 4 星 | 明显相关 |
| `>= 1.2` | 3 星 | 中等相关 |
| `>= 0.4` | 2 星 | 领域相关但个性化信号较弱 |
| `< 0.4` | 1 星 | 保底收录 |

## GitHub Actions

工作流文件位于：

```text
.github/workflows/daily-arxiv-digest.yml
```

默认每天 `UTC 03:37` 运行，也就是北京时间 `11:37`。这个时间刻意避开整点，降低和其他批量任务一起撞上 arXiv 限流的概率。

你也可以在 GitHub 页面中手动触发：

```text
Actions -> Daily arXiv Digest -> Run workflow
```

## 降级策略

| 情况 | 行为 |
| --- | --- |
| 未配置 OpenAI key | 中文摘要退回到基础文本 |
| LLM API 调用失败 | 使用 fallback 摘要，不阻塞邮件 |
| Zotero 不可用 | 只使用关键词和分类排序 |
| arXiv 持续返回 429 | 发送限流告警邮件，默认不把 Actions 标红 |
| 主流程异常且 SMTP 正常 | 发送失败告警邮件 |

## 项目结构

```text
arxiv_digest/
  arxiv_client.py   arXiv API 抓取、日期窗口、429 重试
  config.py         环境变量配置
  emailer.py        HTML 邮件渲染和 SMTP 发送
  llm.py            LLM 摘要生成和降级
  main.py           命令入口和失败告警
  models.py         数据模型
  pipeline.py       主数据流程编排
  recommender.py    推荐分数和星级
  zotero_client.py  Zotero 偏好读取
  templates/        邮件模板

scripts/
  preview_digest.py 本地预览，不发送邮件
```

## 推送到 GitHub

```powershell
git init
git add .
git commit -m "Initial commit: Daily arXiv Digest"
git remote add origin https://github.com/你的用户名/你的仓库名.git
git branch -M main
git push -u origin main
```

推送后先手动运行一次 workflow，确认 Secrets 和 Variables 都配置正确。
