# Daily arXiv Digest

每天从 arXiv 抓取你关注领域的最新论文，结合关键词和 Zotero 兴趣偏好做个性化推荐，用 LLM 生成中文摘要，通过邮件发送每日总结。

## 功能

- 按 arXiv 分类抓取最新论文（astro-ph、cs、hep 等任意分类均可）
- 关键词 + 单词边界匹配，避免误匹配（"star" 不会命中 "start"）
- 可选接入 Zotero 文献库，根据你的阅读记录和标签调整推荐分数
- 1-5 星兴趣评级，邮件中醒目展示
- LLM 生成中文每日总览 + 每篇论文的中文单篇摘要
- 支持 OpenAI / DeepSeek / 通义千问 / Ollama 等所有兼容 OpenAI API 的模型
- GitHub Actions 每天定时运行，全自动无需手动操作
- 完善的降级策略：LLM 不可用、Zotero 不可用均不会阻塞邮件发送

## 本地运行

```powershell
# 1. 创建虚拟环境并安装依赖
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# 2. 复制配置文件
Copy-Item .env.example .env

# 3. 编辑 .env，填入必需的配置（见下方配置说明）

# 4. 先预览，确认效果满意（不发邮件）
python -m scripts.preview_digest

# 5. 发送完整邮件
python -m arxiv_digest.main
```

## 配置说明

编辑 `.env` 文件，以下是所有可用配置项。

### 必填

| 变量 | 说明 | 示例 |
|------|------|------|
| `ARXIV_CATEGORIES` | arXiv 分类，逗号分隔 | `astro-ph.GA,astro-ph.CO,astro-ph.HE` |
| `ARXIV_KEYWORDS` | 关注的关键词，逗号分隔 | `galaxy,cosmology,black hole,star formation` |
| `OPENAI_API_KEY` | LLM API 密钥 | `sk-xxxxxxxx` |
| `SMTP_HOST` | 邮件服务器 | `smtp.gmail.com` |
| `SMTP_PORT` | 邮件端口 | `587` |
| `SMTP_USERNAME` | 邮箱账号 | `your@gmail.com` |
| `SMTP_PASSWORD` | 邮箱密码（Gmail 需用 App Password） | `xxxx` |
| `EMAIL_FROM` | 发件地址 | `your@gmail.com` |
| `EMAIL_TO` | 收件地址 | `your@gmail.com` |

### LLM 模型（可选，默认 OpenAI gpt-4o-mini）

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `OPENAI_MODEL` | 模型名称 | `gpt-4o-mini` |
| `OPENAI_BASE_URL` | API 地址，不填则用 OpenAI 官方 | 留空 |

**切换模型示例：**

```ini
# OpenAI（默认，不填 BASE_URL 即可）
OPENAI_API_KEY=sk-xxxxxxxx
OPENAI_MODEL=gpt-4o-mini

# DeepSeek
OPENAI_API_KEY=你的DeepSeek-key
OPENAI_BASE_URL=https://api.deepseek.com
OPENAI_MODEL=deepseek-chat

# 阿里通义千问
OPENAI_API_KEY=你的阿里云-key
OPENAI_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
OPENAI_MODEL=qwen-plus

# 本地 Ollama
OPENAI_API_KEY=ollama
OPENAI_BASE_URL=http://localhost:11434/v1
OPENAI_MODEL=qwen2.5:7b

# 月之暗面 Moonshot
OPENAI_API_KEY=你的Moonshot-key
OPENAI_BASE_URL=https://api.moonshot.cn/v1
OPENAI_MODEL=moonshot-v1-8k

# 智谱 ChatGLM
OPENAI_API_KEY=你的智谱-key
OPENAI_BASE_URL=https://open.bigmodel.cn/api/paas/v4
OPENAI_MODEL=glm-4-flash
```

### arXiv 抓取（可选）

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `ARXIV_FETCH_MAX_RESULTS` | 候选池大小 | `100` |
| `ARXIV_REQUEST_TIMEOUT` | 请求超时（秒） | `60` |
| `ARXIV_REQUEST_RETRIES` | 失败重试次数 | `3` |

### 邮件控制（可选）

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `MAX_PAPERS` | 邮件最多展示论文数 | `12` |
| `MAX_LLM_PAPERS` | 生成中文摘要的论文数 | `12` |
| `MAX_ABSTRACT_CHARS` | 每篇摘要截断长度 | `1800` |
| `MIN_RATING` | 最低星级（1-5），低于此不推送 | `1` |
| `DIGEST_TIMEZONE` | 时区，用于定义"昨天" | `Asia/Shanghai` |
| `LOOKBACK_DAYS` | 回顾天数 | `1` |

### Zotero 偏好（可选，不填则只用关键词排序）

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `ZOTERO_API_KEY` | Zotero API 密钥 | 留空跳过 |
| `ZOTERO_USER_ID` | Zotero 用户 ID | 留空跳过 |
| `ZOTERO_LIBRARY_TYPE` | `user` 或 `group` | `user` |
| `ZOTERO_COLLECTION_ID` | 限定某个 collection | 留空用全部 |

Zotero 支持的标签（在 Zotero 条目中打上即可生效）：

**正向标签：** `favorite` `important` `interested` `high-interest` `must-read` `relevant`
**负向标签：** `not-interesting` `irrelevant` `low-interest` `skip`

---

## 星级评分

内部推荐分数由三部分相加组成：

- **关键词命中**：标题+摘要中出现关键词，每命中一次 +0.8（上限 3.0）
- **Zotero 相似度**：与 Zotero 条目文本重叠度 × 标签权重（可选）
- **分类加成**：每个 arXiv 分类 +0.2

然后映射为星级：

| 分数范围 | 星级 | 含义 |
|----------|------|------|
| >= 5.0 | 5 星 | 高度符合兴趣 |
| >= 3.0 | 4 星 | 明显相关 |
| >= 1.2 | 3 星 | 中等相关 |
| >= 0.4 | 2 星 | 领域相关但信号弱 |
| < 0.4 | 1 星 | 保底收录 |

星级规则可在 [recommender.py](arxiv_digest/recommender.py) 的 `score_to_rating()` 中调整。

---

## 部署到 GitHub Actions

### 1. 推送代码

```powershell
git init
git add .
git commit -m "Initial commit: Daily arXiv Digest"
git remote add origin https://github.com/你的用户名/仓库名.git
git branch -M main
git push -u origin main
```

> 注意：GitHub 不再支持密码登录，推送时需要 Personal Access Token。在 https://github.com/settings/tokens 生成，勾选 `repo` 权限，推送时用 token 代替密码。

### 2. 配置 Secrets 和 Variables

仓库 → **Settings** → **Secrets and variables** → **Actions**。

#### Secrets（加密存储，适合密钥/密码）

| Name | 值 |
|------|----|
| `OPENAI_API_KEY` | 你的 API key |
| `ZOTERO_API_KEY` | Zotero key（可选） |
| `ZOTERO_USER_ID` | Zotero 用户 ID（可选） |
| `SMTP_HOST` | `smtp.gmail.com` |
| `SMTP_PORT` | `587` |
| `SMTP_USERNAME` | 邮箱账号 |
| `SMTP_PASSWORD` | 邮箱密码 |
| `EMAIL_FROM` | 发件地址 |
| `EMAIL_TO` | 收件地址 |

#### Variables（明文存储，适合普通配置）

| Name | 值 |
|------|----|
| `ARXIV_CATEGORIES` | `astro-ph.GA,astro-ph.CO,astro-ph.HE,astro-ph.IM,astro-ph.SR,astro-ph.EP` |
| `ARXIV_KEYWORDS` | `galaxy,cosmology,black hole,star formation` |
| `DIGEST_TIMEZONE` | `Asia/Shanghai` |
| `ARXIV_FETCH_MAX_RESULTS` | `100` |
| `ARXIV_REQUEST_TIMEOUT` | `60` |
| `ARXIV_REQUEST_RETRIES` | `3` |
| `MAX_PAPERS` | `12` |
| `MAX_LLM_PAPERS` | `12` |
| `MAX_ABSTRACT_CHARS` | `1800` |
| `MIN_RATING` | `1` |
| `LOOKBACK_DAYS` | `1` |
| `OPENAI_MODEL` | `gpt-4o-mini` |
| `OPENAI_BASE_URL` | 留空（用 OpenAI） |
| `ZOTERO_LIBRARY_TYPE` | `user` |
| `ZOTERO_COLLECTION_ID` | 留空 |

### 3. 触发

- **自动**：每天 UTC 00:10（北京时间 08:10）定时运行
- **手动**：Actions → Daily arXiv Digest → Run workflow

修改 cron 编辑 [.github/workflows/daily-arxiv-digest.yml](.github/workflows/daily-arxiv-digest.yml)：

```yaml
on:
  schedule:
    - cron: "10 0 * * *"   # 改成想要的时间（UTC）
```

---

## 降级策略

系统在以下情况会自动降级，不会阻塞邮件发送：

| 条件 | 行为 |
|------|------|
| 未配置 OpenAI key | 中文摘要退回到英文原文摘要 |
| LLM API 调用失败 | 仅该次摘要缺失，不影响其他论文 |
| Zotero 不可用 | 只使用关键词和分类排序 |
| 主流程异常 + SMTP 正常 | 发送失败告警邮件 |

---

## 项目结构

```
arxiv_digest/
  models.py         数据模型（Paper, ZoteroPreference）
  config.py         配置加载（环境变量 → Config 对象）
  arxiv_client.py   arXiv API 抓取、XML 解析、重试、去重
  recommender.py    关键词匹配 + Zotero 相似度评分、星级映射
  zotero_client.py  Zotero API 读取、标签加权
  llm.py            LLM 调用、中文摘要生成、降级处理
  emailer.py        Jinja2 渲染 HTML 邮件、SMTP 发送
  pipeline.py       管道编排（数据流串联）
  main.py           入口：运行管道 + 发送邮件
  templates/        邮件 HTML 模板
scripts/
  preview_digest.py 本地预览工具（不发邮件）
.github/workflows/
  daily-arxiv-digest.yml  GitHub Actions 定时任务
```

---

## 更新代码

改完代码后：

```powershell
git add .
git commit -m "描述你改了什么"
git push
```

GitHub Actions 会在下次定时触发时自动使用最新代码。
