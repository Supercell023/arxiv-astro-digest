# 配置指南

这份文档回答两个问题：**每个变量是什么意思、什么时候该改它**，以及**用哪个邮箱发信**。

配置有三个来源，优先级从高到低：

1. **环境变量**（GitHub Actions 里的 Secrets / Variables）
2. **`.env` 文件**（本地开发；已被 `.gitignore` 排除）
3. **代码里的默认值**（`arxiv_digest/config.py`）

空字符串等同"没设置"，会回落到默认值。所以留空是安全的，不会把功能关掉（开关类变量的空值语义见 [第四节](#四开关类变量空值是什么意思)）。

---

## 一、最小可用配置

### A. 只想先看看长什么样（不发信、不花钱）

```ini
OPENAI_API_KEY=sk-...
OPENAI_BASE_URL=https://api.deepseek.com
OPENAI_MODEL=deepseek-flash
ANALYSIS_ENABLED=true
```

```bash
python -m arxiv_digest.main --out preview.html
```

### B. 每天真的收到邮件（推荐先用这套）

在上面四行之外，再加七行：

```ini
SMTP_HOST=smtp.qq.com
SMTP_PORT=465
SMTP_USERNAME=12345678@qq.com
SMTP_PASSWORD=授权码（16 位，不是登录密码）
EMAIL_FROM=12345678@qq.com
EMAIL_TO=12345678@qq.com
DIGEST_TIMEZONE=Asia/Shanghai
```

其余变量全部留空即可——默认值已经是一套能用的配置（4 篇精读 + 10 篇速览 + 日程 + 脉搏 + 雷达 + 世界在动）。

---

## 二、用哪个邮箱发信

### 对比

| 服务 | SMTP | 端口 | 密码类型 | 国内可用 | 建议 |
| --- | --- | --- | --- | --- | --- |
| **QQ 邮箱** | `smtp.qq.com` | **465** | 授权码 | ✅ 稳 | **首选** |
| 163 邮箱 | `smtp.163.com` | 465 | 授权码 | ✅ | 次选，偶被判垃圾 |
| 腾讯企业邮 | `smtp.exmail.qq.com` | 465 | 邮箱密码/客户端专用密码 | ✅ | 有域名时用 |
| Gmail | `smtp.gmail.com` | 587 | 应用专用密码 | ❌ 本地连不上 | Actions 上可用 |
| Outlook / 365 | `smtp.office365.com` | 587 | 应用密码 | 半可用 | 微软在收紧，不推荐新配 |
| 发信服务（Resend 等） | — | 587 | API Key | ✅ | 发给别人时用，发给自己没必要 |

**推荐 QQ 邮箱**，理由很实际：465 端口是隐式 SSL，国内运营商不拦；授权码机制不会暴露登录密码；发给自己秒到，且不容易进垃圾箱。

### 拿 QQ 邮箱授权码

1. 电脑登录 [mail.qq.com](https://mail.qq.com) → 左上角 **设置** → **账号**
2. 找到「POP3/IMAP/SMTP/Exchange/CardDAV/CalDAV服务」
3. 开启 **服务：IMAP/SMTP服务**（会要求短信验证）
4. 点「生成授权码」，短信验证后拿到一串 **16 位字母**（例如 `abcdwxyzefghijkl`）
5. 这串就是 `SMTP_PASSWORD`，**只显示一次**，抄下来

> 授权码不是登录密码。改了 QQ 密码授权码依然有效；但"注销所有授权码"会让它失效。

### 三个必知的坑

**① `EMAIL_FROM` 必须等于 `SMTP_USERNAME`。**
QQ / 163 会校验发件人与登录账号是否一致，不一致直接拒发（报 `553 Mail from must equal authorized user`）。

**② 465 和 587 不能混。**
代码按端口自动选协议：**465 用隐式 SSL**，其他端口用 STARTTLS。填 587 却配 `smtp.qq.com` 会超时。

**③ 收件人写你自己就行。**
`EMAIL_TO` 可以是同一个地址，也可以另起一个小号做归档；多个地址用逗号分隔。

### 常见报错对照

| 报错 | 原因 | 处理 |
| --- | --- | --- |
| `535 Authentication failed` | 用了登录密码，或授权码过期 | 重新生成授权码 |
| `553 Mail from must equal authorized user` | `EMAIL_FROM` 与 `SMTP_USERNAME` 不一致 | 改成同一个地址 |
| `Connection timed out` | 端口与协议不匹配，或被运营商拦 | 用 465 + `smtp.qq.com` |
| `SMTP settings are incomplete` | 缺 `SMTP_HOST` / `EMAIL_FROM` / `EMAIL_TO` | 补齐这三项 |
| 邮件进了垃圾箱 | 发件人域名信誉 | 把发件地址加白名单；正文避免"促销"类词 |

---

## 三、变量详解（按用途分组）

### 1. 模型端点（最常改）

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `OPENAI_API_KEY` | 空 | **必填**。DeepSeek / OpenAI / 任何兼容端点的 key |
| `OPENAI_BASE_URL` | 空（=OpenAI 官方） | DeepSeek 填 `https://api.deepseek.com` |
| `OPENAI_MODEL` | 见下 | 主模型。不填时：base_url 含 `deepseek` → `deepseek-flash`，否则 `gpt-4o-mini` |
| `ANALYSIS_EXTRACT_MODEL` | = `OPENAI_MODEL` | 抽取用的模型（便宜的那个） |
| `ANALYSIS_ASSESS_MODEL` | = `OPENAI_MODEL` | 写评论用的模型（可以换成更强的） |
| `PULSE_MODEL` | = `OPENAI_MODEL` | 本周脉搏用 |

省钱组合：`EXTRACT=deepseek-flash` + `ASSESS=deepseek-v4-pro`，抽取便宜、评论强。

### 2. 思考模式（DeepSeek 专属，六个开关）

三态：**留空** = 不发这个参数（OpenAI 端点必须留空，否则 400）；**false** = 显式关闭；**true** = 显式开启。

| 变量 | 建议 | 原因 |
| --- | --- | --- |
| `ANALYSIS_EXTRACT_THINKING` | `false` | 结构化抽取，思考只费钱不提质量 |
| `ANALYSIS_ASSESS_THINKING` | 留空（跟随默认） | 写评论时可以开，但成本约翻倍 |
| `LLM_THINKING` | 留空 | 总览摘要，开不开差别小 |
| `PULSE_THINKING` | 留空 | 跨论文推理，开了更稳 |
| `RADAR_THINKING` | 留空 | 一句话解读，不值得开 |
| `TRANSLATE_THINKING` | 留空 | 翻译，开了纯浪费 |

**代价**：全开思考时输出 token 有 60–70% 是思考，单次成本从 ~$0.04 涨到 ~$0.10。

### 3. 内容量与阅读预算

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `DEEP_PAPERS` | 4 | 精读篇数（每篇约 3 分钟） |
| `BRIEF_PAPERS` | 10 | 速览篇数（每篇约 40 秒） |
| `MAX_PAPERS` | 14 | 一天最多处理多少篇（要 ≥ 精读+速览） |
| `MAX_LLM_PAPERS` | 14 | 参与评分的篇数 |
| `MIN_RATING` | 1 | 低于这个星级的直接丢弃 |
| `LOOKBACK_DAYS` | 1 | 回看几天；周末没公告时会自动放宽到 3/5/7 天 |
| `MAX_ABSTRACT_CHARS` | 1800 | 送进模型的摘要上限（字符） |

**时间账**：`4×3 分钟 + 10×40 秒 ≈ 19 分钟`。想再省时间就调成 `2 / 6`。

### 4. 全文 PDF（精读的前提）

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `FULLTEXT_ENABLED` | true | 关闭则精读退回摘要级（卡片标"仅摘要"） |
| `FULLTEXT_MAX_CHARS` | 40000 | 送进模型的正文上限，约 1 万 token |
| `FULLTEXT_MAX_PAGES` | 40 | 最多解析多少页 |
| `FULLTEXT_TIMEOUT` | 120 | 单篇下载超时（秒） |

关掉全文能省一半成本，但 `data / processing / systematics` 三个字段会大面积变成"摘要未提"。

### 5. 板块开关

| 变量 | 默认 | 关掉会失去什么 |
| --- | --- | --- |
| `ANALYSIS_ENABLED` | **false** | 精读卡退化成一段中文摘要（**最容易被忽略的一项**） |
| `CALENDAR_ENABLED` | true | 近期日程与倒计时 |
| `PULSE_ENABLED` | true | 本周脉搏（大家在讨论什么） |
| `RADAR_ENABLED` | true | 邻域雷达（别的方向在动什么） |
| `SOURCES_ENABLED` | true | 世界在动（二手解读、会议报告） |
| `TRANSLATE_ENABLED` | true | 二手条目的中文翻译 |
| `TREND_ENABLED` | true | 投稿量周环比 |

### 6. 样式与分发

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `EMAIL_THEME` | dark | `dark` 深空冷色 / `light` 暖纸浅色 |
| `EMAIL_ATTACH_HTML` | true | 邮件附一份 HTML，可下载转发到群里 |
| `DIGEST_TIMEZONE` | Asia/Shanghai | 邮件抬头时间与日程倒计时用的时区 |
| `DIGEST_JSON_PATH` | 空 | 填 `digest.json` 会把这一期结构化数据落盘，便于回放 |

### 7. 领域与个性化

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `FIELDS` | 空 | 勾选领域包，逗号分隔；空 = 全部启用的包 |
| `PROFILE_PATH` | `config/profile.yaml` | 读者画像 |
| `PACKS_DIR` | `config/packs` | 领域包目录 |
| `ARXIV_CATEGORIES` | 代码默认 | **逃生通道**：直接指定分类，绕过领域包 |
| `ARXIV_KEYWORDS` | 空 | 额外加权关键词 |

### 8. 状态与抓取（基本不用动）

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `STATE_PATH` | `state.json` | 跨天去重与源分频的状态文件；**必须随仓库提交** |
| `SOURCES_PATH` | `config/sources.yaml` | 二手源与会议清单 |
| `SOURCES_MAX_ITEMS` | 6 | 每个源最多取几条 |
| `RADAR_PER_FIELD` | 1 | 每个邻域方向展示几条（其余折叠） |
| `RADAR_MAX_FIELDS` | 5 | 最多展示几个方向 |
| `RADAR_LOOKBACK_DAYS` | 7 | 雷达回看天数 |
| `PULSE_THREADS` | 4 | 脉搏写几条议题 |
| `PULSE_MAX_MATERIALS` | 30 | 参与统计的材料上限 |
| `TRANSLATE_LIMIT` | 40 | 最多翻译多少条二手 |
| `ARXIV_PAGE_SIZE` | 25 | 每页请求数（arXiv API 上限） |
| `ARXIV_MAX_PAGES` | 4 | 最多翻几页 |
| `ARXIV_REQUEST_RETRIES` | 5 | 429 时的重试次数 |
| `ARXIV_RATE_LIMIT_BACKOFF_BASE` | 60 | 首次退避秒数 |

---

## 四、开关类变量：空值是什么意思

有两种开关，行为不同：

| 类型 | 例子 | 留空时 |
| --- | --- | --- |
| 普通布尔 | `ANALYSIS_ENABLED` | **回落到默认值**（false） |
| 三态布尔 | `LLM_THINKING` | **不发送这个参数**，由服务端决定 |

所以 `ANALYSIS_ENABLED=` 不等于"开启"，而是"用默认值（关）"。这是最常见的误配——以为把开关留空就是打开。

只有 `*_THINKING` 这六个是三态，其余布尔留空都是"回落到默认值"。

---

## 五、踩过的坑

**① 注释不能写在值的同一行。**

```ini
PULSE_MODEL=            # 留空则用 OPENAI_MODEL   ← 错
PULSE_MODEL=                                       ← 对
# 留空则用 OPENAI_MODEL                            ← 注释单独一行
```

`python-dotenv` 只在值非空时才把 `#` 之后当注释；上面那行会被读成值 `# 留空则用 OPENAI_MODEL`，然后作为模型名发给端点，返回 400 `The supported API model names are...`。新版代码会剥掉行内注释，但建议一开始就写对。

**② 本地改完 `.env` 要重新运行进程**，没有热加载。

**③ Actions 里没有 `.env`**，只认 Secrets / Variables。本地能跑通不代表云端能跑。

**④ `state.json` 不要加进 `.gitignore`**。Actions 每次都是干净文件系统，靠它去重；不提交就会天天推同一批论文。

---

## 六、成本调节

| 目标 | 做法 | 效果 |
| --- | --- | --- |
| 降价一半 | `FULLTEXT_ENABLED=false` | 精读变浅，$0.04 → $0.02 |
| 降价 60% | 全部 `*_THINKING=false` | $0.10 → $0.04 |
| 再降 | `DEEP_PAPERS=2` | 精读少一半 |
| 提质 | `ANALYSIS_ASSESS_MODEL=deepseek-v4-pro` | 评论更稳，约 +30% |

一次完整运行的典型用量：**输入 ~6 万 / 输出 ~1.5 万 token**。
