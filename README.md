# arXiv 每日简报

[**中文**](README.md) · [English](README.en.md)

把 arXiv 和几本天文期刊的每日新文，变成一封 **带判断、且知道每篇为什么被选上** 的研究简报。篇数按 20 分钟档位排版；单篇内容按证据写足，不按分钟裁剪。

每天定时抓取新论文与领域动态 → 抽取事实 → 按你的读者画像打分与评论 → 渲染成一封邮件（或 HTML 文件）发出。

`v0.17.1` · Python ≥ 3.11 · MIT

**v0.17 反馈与热点合表**：长期热点档案新增第五张表 `feedback_view`——把邮件里投的"相关性 / 有用性"按论文主题分摊聚合（`votes` / 两维均值 / 有用命中），与 `weekly_topics` 同键并排看"这个方向在变多"和"这个方向对我是有用的"的差别。反馈与档案可以共用一份 Google 表格：反馈 tab 由脚本属性 `FEEDBACK_SHEET_NAME` 指定，档案脚本的白名单读不到投票原文。[合表说明](docs/FEEDBACK.md)。

**v0.16 长期热点档案**：每天把**抓到的全部论文**（不只入选的 14 篇）按周写进一张私有 Google 表格——`papers` 明细、`weekly_topics` 周×主题（share / 环比 / 四周移动平均）、`weekly_overview` 周概览、`dict` 口径字典。纯规则打标（领域包 topics + 关键词），精读 4 篇额外带模型术语；只在发信成功后写，幂等 upsert，失败不影响投递。影子期只写表、不改邮件。[部署与口径](docs/ARCHIVE.md)。

**v0.15 期刊论文也能读全文**：ApJ / MNRAS / NatAs / AJ / A&A 的论文，按标题反查 arXiv 预印本取 PDF（标题相似度 + 作者 + 年份三道校验），命中就做全文精读，**来源徽标、DOI 与链接仍是期刊**；没命中回退摘要。Nature 系在 Crossref 常只给标题，现在先补摘要再过主题闸门。[实现与实测](docs/CHANGE_REPORT_2026-10-26_JOURNAL_FULLTEXT.md)。

**v0.14 周末精选与更广的世界**：默认周日回顾过去七个完整自然日，按关键词、画像主要研究对象和领域主题重新挑选相关论文；可包含工作日已推荐论文，最多 12 篇、精读上限 6 篇，相关不足就少选。工作日也过滤只有分类相关的弱匹配。“世界在变”扩展为天文、物理、化学及历史/考古资讯，目标约 30 条、丰富时最多 36 条；天文与其他领域合计约 1:1，库存不足相互补位。[详细规则和验收](docs/CHANGE_REPORT_2026-10-26_V014.md)。

```bash
python -m arxiv_digest.main --edition weekly --out weekly.html  # 实时抓取/模型，可能计费；不发信
python scripts/eval_analysis.py build --count 20                # 冻结评测输入，访问 arXiv
python scripts/eval_analysis.py run --out eval-digest.json       # 分析全部冻结输入，调用模型
python scripts/eval_analysis.py check --digest eval-digest.json  # 只读检查
```

**v0.13 可靠性升级**：预览不消费已读状态；发信成功才提交；跨进程去重；完整事实快照和引文/数字回查；七日材料脉搏；默认离线回放。Zotero 标题/标签默认只在本地使用，`ZOTERO_SHARE_WITH_MODEL=true` 才进入模型请求。

- [审阅基线](docs/REVIEW_2026-10-26.md) · [改动与迁移](docs/CHANGE_REPORT_2026-10-26.md) · [AI 接续入口](docs/AI_HANDOFF.md)
- 离线验证：`python -B -m unittest discover -s tests -v`；真实外部服务和模型内容质量仍需单独验收。
- `state.json` 将自动升级到 v2；保留已读记录，旧的不可信统计清空重建。升级前建议备份；本次代码改动没有改写现存状态。

---

## 一、这是什么

大多数人订阅 arXiv 的方式是"每天收到一堆标题，然后一篇都不点开"。这个项目想解决的不是"信息不够"，而是**信息过载导致的判断瘫痪**：一天几十篇新投稿，你真正需要的只是"哪两三篇跟我有关、为什么有关、值不值得现在读"。

所以它做的不是摘要搬运，而是四件事：

1. **筛**——按领域包和读者画像打分，14 篇进邮件，其余不打扰；每篇的分数能拆开看（关键词 / Zotero / 分类 / 库内 / 反馈）。
2. **抽**——从 PDF 全文里抽出"用了什么数据、怎么筛、怎么校正、用什么方法、得出什么"，每个字段都带原文引文。
3. **评**——只基于抽出来的事实写评论，先说**为什么做这个研究**，再把**研究方法**讲细，最后给阅读建议并反查本领域的常见坑。
4. **看全局**——同期其他方向在动什么、这一周大家在争论什么、哪些数据发布快到了。

来源不限于 arXiv：ApJ、MNRAS、Nature Astronomy、AJ、A&A 的新文会经 Crossref 一并进入候选，
再按领域词闸门过滤掉行星/恒星物理这类跑题稿件。你在 Zotero 里已经收过的论文会被识别出来，
扣分、标注，或者直接不推。

---

## 二、它每天给你什么

邮件从上到下是"先给世界，再给你"的顺序：

| 板块 | 内容 | 说明 |
| --- | --- | --- |
| **LLM 总览** | 3–5 句今日导读 | 今天最值得注意的一条是什么 |
| **本周脉搏** | 本周集中出现的 3–4 个议题 | 每条判断必须挂 arXiv 编号作证据，无证据直接丢弃 |
| **邻域雷达** | 5 个非本领域方向各 1 条 | 系外行星 / 恒星 / 高能 / 仪器 / 太阳系，其余折叠 |
| **近期日程** | 数据发布、会议、提案截止 | `D-19` 倒计时，日期未公布也显示"预计 5 月" |
| **精读** | 4 篇，每篇 12 个分区 | 见下 |
| **速览** | 10 篇，一行一篇 | 中文一句话 + 可展开的中英摘要 + 紧凑反馈链接 |
| **世界在动** | 9 个信息源的二手解读 | 中文标题 + 150–220 字中文解读 + 来源标注 |
| **采集论文量变化** | 近7天 vs 前7天的采集数 | 按实际发表日与 ID 去重的采集量，受分页上限影响 |

**精读卡的 12 个分区**（这是整个项目的重心）：

1. 星级 + 总分 + 分类/期刊标签
2. **分数构成**：关键词 / Zotero / 分类 / 库内 / 反馈 五项色条，合计对得上总分
3. 一句话结论 + 阅读建议（今天读完整篇 / 看方法与图表即可 / 留档）
4. **研究背景**：为什么做这个、领域缺的是什么
5. **研究方法**：估计量、建模、样本 cut、协方差，讲清到能复述
6. 方法链：`数据 → 筛选 → 估计量 → 推断` 的横向骨架
7. 七问七答：数据、处理、系统误差、方法、结果、局限、对你的用处
8. 关键数字表：样本量、误差、显著性，每个都带原文引文
9. 图表线索：从 PDF 里正则抽出的图注（不花模型的钱）
10. 今天能做什么 + 红旗反查（已讨论 / 部分涉及 / 未提及）
11. 术语小抄 + 中文摘要
12. English abstract（折叠）+ 两维反馈按钮（相关性 / 有用性）

---

## 三、五个关键设计

**① 两阶段，而不是一次生成。**
先让便宜模型从全文里抽结构化事实（每个字段带 `quote`），再让强模型拿着事实 + 你的画像写评论。好处是：抽错能定位到字段，评论受引文与数字回查约束，仍需人工核对；事实可以缓存复用；评论换模型不用重抽。

**② 每条判断都要挂证据。**
脉搏里"某篇论文说了什么"必须给出 arXiv 编号，给不出就丢弃这条判断。没有证据的判断宁可不说——这比说得漂亮重要。

**③ 领域知识写在 YAML，不写在代码里。**
`config/packs/*.yaml` 定义分类、评审重点、常见红旗、脉搏主题。换方向是编辑 YAML，不是改 Python。

**④ 排序分必须可拆。**
总分不是黑箱：邮件里直接摊开哪几项加了分、哪项扣了分（库内命中就是负的）。这样"为什么这篇排第一"不需要信我，肉眼能对账。

**⑤ 反馈问两个问题，不是一个。**
反馈链接打开后需要确认一次，防止邮件扫描器误记。"跟我有关"和"读了有用"是两件事：可以很相关但没用，也可以不相关但方法能抄。两维分开记，权重分开学，第二天的排序才有方向。

---

## 四、快速开始（5 分钟）

```bash
git clone https://github.com/Supercell023/arxiv-astro-digest.git
cd arxiv-astro-digest

python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt        # Windows
# source .venv/bin/activate && pip install -r requirements.txt     # macOS / Linux

cp .env.example .env        # Windows: copy .env.example .env
```

编辑 `.env`，最小可用的一组：

```ini
OPENAI_API_KEY=sk-...
OPENAI_BASE_URL=https://api.deepseek.com     # 用 OpenAI / 其他兼容端点就改这里
OPENAI_MODEL=deepseek-flash
ANALYSIS_ENABLED=true                        # 默认 false，不开就只有摘要没有精读
```

然后：

```bash
python -m arxiv_digest.selfcheck                 # 27 项自检，不联网、不花钱
python -m arxiv_digest.main --out preview.html   # 真跑一次，只出 HTML 不发信
```

满意后再填 SMTP 五项，去掉 `--out` 就会真正发信。

> 国内网络装依赖建议加镜像：`-i https://pypi.tuna.tsinghua.edu.cn/simple`

---

## 五、常用命令

| 命令 | 用途 |
| --- | --- |
| `python -m arxiv_digest.selfcheck` | 27 项离线自检（导入、配置、解析、分派、渲染、反馈、期刊、主题…） |
| `python -m arxiv_digest.main --out preview.html` | 真跑一次，写 HTML 不发信、不写状态 |
| `python -m arxiv_digest.main` | 完整流程，会发信并写 `state.json` |
| `python scripts\build_preview.py` | 用录制数据渲染预览（不联网、不花钱） |
| `python scripts\build_preview.py --theme light` | 切浅色主题预览 |
| `python scripts\build_preview.py --replay digest.json --out replay.html` | 离线回放真实快照（先设置 DIGEST_JSON_PATH） |
| `python -B -m unittest discover -s tests -v` | 核心事务、证据、反馈和契约回归，默认禁网 |
| `python scripts\preview_digest.py --out p.html` | 跑真实数据但只输出 HTML |
| `python scripts\feedback_server.py` | 本机反馈端点（`http://127.0.0.1:8765/log`） |
| `python scripts\feedback.py record --id 2610.31660 --rel 2 --use 0` | 手工记一条两维反馈 |
| `python scripts\feedback.py stats` | 看学到的偏好 |
| `python -m scripts.check_sheets` | 只读检查反馈 CSV 与档案端点是否接好（合表时先跑这个） |
| `python scripts\eval_analysis.py check --digest dump.json` | 评测抽取质量 |
| `python scripts\refresh_pulse.py` | 重新抓一次脉搏材料 |

---

## 六、配置

完整变量见 [`.env.example`](.env.example)，以该文件为准。最常改的：

| 变量 | 默认 | 作用 |
| --- | --- | --- |
| `DEEP_PAPERS` / `BRIEF_PAPERS` | 4 / 10 | 精读与速览**篇数**（只决定排版，不再压缩单篇内容） |
| `ANALYSIS_ENABLED` | false | 是否走两阶段精读 |
| `FULLTEXT_ENABLED` | true | 精读是否下载 PDF 全文 |
| `PREPRINT_ENABLED` | true | 期刊论文是否按标题反查 arXiv 预印本取全文 |
| `PREPRINT_MAX_RESULTS` | 5 | 每次反查取几条候选 |
| `PREPRINT_ABSTRACT_LOOKUPS` | 4 | 每轮最多为几篇无摘要期刊补摘要 |
| `PREPRINT_TIMEOUT` | 20 | 单次 arXiv 查询超时（秒），请求间隔 ≥3 秒 |
| `JOURNALS_ENABLED` | true | 是否抓 ApJ/MNRAS/NatAs/AJ/A&A |
| `JOURNALS_PATH` | config/journals.yaml | 期刊与 ISSN、领域包绑定 |
| `JOURNALS_MAX_PER` | 6 | 每本刊最多留几篇（闸门之后） |
| `ZOTERO_SKIP_COLLECTED` | false | false=库内命中扣分标注；true=直接不推 |
| `FEEDBACK_URL` | 空 | 反馈按钮的落点（本机端点或 Apps Script Web App） |
| `FEEDBACK_SHEET_CSV_URL` | 空 | Google Sheet 的 CSV 导出地址，每天合并回本地 |
| `ARCHIVE_ENABLED` | true | 是否写长期热点表（需同时配 URL 与令牌） |
| `ARCHIVE_WEBHOOK_URL` | 空 | Apps Script Web App 的 `/exec` 地址 |
| `ARCHIVE_TOKEN` | 空 | 与 Apps Script 脚本属性一致的共享令牌 |
| `ARCHIVE_MAX_ROWS` | 400 | 单次最多写多少行（超出分批） |
| `ARCHIVE_SNIPPET_CHARS` | 160 | `papers` 表里摘要片段保留多少字 |
| `EMAIL_THEME` | dark | `dark` 深空冷色 / `light` 暖纸浅色 |
| `EMAIL_ATTACH_HTML` | true | 邮件附一份 HTML，可下载转发 |
| `CALENDAR_ENABLED` | true | 近期日程板块 |
| `RADAR_ENABLED` | true | 邻域雷达板块 |
| `PULSE_ENABLED` | true | 本周脉搏板块 |
| `TRANSLATE_ENABLED` | true | 二手解读中文翻译 |
| `FIELDS` | 空 | 勾选领域包，逗号分隔；空=全部启用的包 |

**模型相关**：`OPENAI_BASE_URL` / `OPENAI_MODEL` / `ANALYSIS_EXTRACT_MODEL` / `ANALYSIS_ASSESS_MODEL` / `PULSE_MODEL`。
DeepSeek 思考模式默认开启，六个 `*_THINKING` 开关可分别控制（留空则跟随模型默认，DeepSeek 下自动关）。

**成本**：真实预览也会请求模型。用量取决于篇数、缓存和供应商；`run_report.json` 记录实际返回的 token，不把历史样例费用当作当前报价。

---

## 七、换成你的方向

不用改代码，改三个文件：

| 文件 | 作用 |
| --- | --- |
| `config/profile.yaml` | 读者画像：你是谁、关注什么、七问要问什么（20 分钟不再限制内容量） |
| `config/packs/*.yaml` | 领域包：分类、评审重点、常见红旗、脉搏主题、期刊闸门词表 |
| `config/journals.yaml` | 期刊来源：ISSN + 归属领域包 |
| `config/sources.yaml` | 二手信息源与会议（Indico event id） |

领域包新增 `title_keywords`（标题辅助分派）、`journal_terms`（期刊闸门）。主分类优先；每篇选择一套评价标准。`title_hint` 是人工说明，普通日程网页继续由日历文件维护。

内置三个包：`galaxy_cosmology`（星系宇宙学）、`galaxy_formation`（星系形成演化）、`generic`（兜底）。
新建一个包就是复制一份 YAML 改内容，然后在 `FIELDS` 里填它的 `id`。

---

## 八、升级说明

### 版本沿革

| 版本 | 主题 |
| --- | --- |
| v0.1 | 基础版：抓取 → 打分 → 中文摘要 → 邮件 |
| v0.2 | 多源（RSS/Indico）+ 跨天状态 + CI 前置自检 |
| v0.3 | 本周脉搏（先统计、再判断、判断必挂证据） |
| v0.4 | 邻域雷达 / 二手翻译 / 反馈闭环 / 评测集 |
| v0.5 | 分区折叠 + 世界在动重做 + 排版统一 |
| v0.6 | LaTeX→Unicode 公式 + 源分频（日更/周更）+ 宋体/Times |
| v0.7 | 版式重做：期刊式报头、分区眉题、语义色板 |
| v0.8 | 统一模型调用层 `llm_client`，适配 DeepSeek 协议 |
| v0.9 | 思考模式全显式化 + 二手翻译分批 |
| v0.10 | HTML 附件 / 世界在动加厚 / 板块配色 / 大标题 |
| v0.11 | 深空冷色版 + 主题令牌化（`EMAIL_THEME`） |
| v0.12 | 分数构成 / 研究背景与方法详解 / 两维反馈 / 期刊来源 / Zotero 库内判断 |
| v0.13 | 状态 v2、发信成功才消费、digest v3 快照、引文/数字回查、反馈签名 |
| v0.14 | 周日精选与兴趣闸门 / 跨领域资讯 / 审查 R01–R09 修复 |
| v0.15 | 期刊论文反查 arXiv 预印本取全文（来源仍记期刊）/ 数值护栏按分句降级 |
| v0.16 | 长期热点档案：全量论文按周写 Google 表格（明细 / 主题 / 概览 / 口径字典），影子期只写表 |
| v0.17 | 反馈票按主题聚合进档案（feedback_view）/ 反馈与档案可共用一份表格 / 合表自检脚本 |

完整变更见 [CHANGELOG.md](CHANGELOG.md)。

### 从旧版本升级

1. **对比 `.env`**：新版新增变量都有默认值，不填也能跑；但 `EMAIL_THEME`、`FULLTEXT_*`、`RADAR_*`、`TRANSLATE_*` 建议显式写一行，避免"以为开了其实没开"。
2. **`.env` 注释要独立成行**：`KEY=   # 注释` 会被 dotenv 当成值本身（`# 注释`）。新版代码会剥掉，但旧配置值得顺手清理。
3. **`state.json` 兼容**：v0.13 迁移到 v2，旧 seen/fetched 保留；旧 daily 计数清空，新采集量按论文日期重建。随机 RSS ID 无法可靠迁移，资讯可能重复出现一次。
4. **`deepseek-chat` / `deepseek-reasoner` 已下线**：DeepSeek 现在的模型名是 `deepseek-flash` / `deepseek-v4-pro`。
5. **升级后先跑自检**：`python -m arxiv_digest.selfcheck`，27 项全绿再跑真实流程。
6. **v0.12 起反馈是两维的**：旧 `up/meh/down` 仍能用（折算成两维同档），但新按钮直接写 `rel=` / `use=`；`config/feedback.json` 会自动升级。

---

## 九、目录结构

```
arxiv_digest/
  main.py           入口：预检 SMTP、--out 模式、发信
  pipeline.py       主流程：抓取 → 分析 → 脉搏/雷达/翻译 → 渲染
  config.py         环境变量 → Config
  arxiv_client.py   arXiv API 抓取（分页、429 退避、自适应时间窗）
  journals.py       期刊抓取（Crossref：ApJ/MNRAS/NatAs/AJ/A&A）
  preprints.py      期刊论文按标题反查 arXiv 预印本（全文 PDF 兜底）
  zotero_client.py  Zotero 库：标签偏好、库内命中、相似度
  analysis.py       两阶段：抽事实 → 写评论
  llm_client.py     统一模型调用（JSON 兜底、思考开关、截断重试）
  fulltext.py       PDF 下载、抽取、清洗、裁剪、抽图注
  pulse.py          本周脉搏：主题统计 + 证据约束的判断
  radar.py          邻域雷达：跨方向采样与折叠
  translate.py      二手解读中文翻译（分批）
  feedback.py       反馈学习（两维：相关性 / 有用性）
  calendar.py       日程倒计时
  packs.py          领域包加载与分派
  profile.py        读者画像
  mathfmt.py        LaTeX → Unicode
  theme.py          两套 54 键色板
  state.py          跨天去重、源分频、采集量统计
  sources/          RSS / Indico 抓取
  templates/        邮件 HTML（无硬编码色值）
  selfcheck.py      27 项离线自检
config/            画像、领域包、期刊、源、日程、雷达、回放数据
scripts/           预览、评测、反馈、反馈端点、脉搏刷新
docs/              PRODUCT_PLAN.md / PROJECT_REVIEW.md / FEEDBACK.md
```

---

## 十、数据边界

**能稳定拿到**：arXiv API（限流友好，带退避）、Crossref（ApJ / MNRAS / Nature Astronomy / AJ / A&A，含 JATS 摘要；Nature Astronomy 部分条目没有摘要）、arXiv 预印本反查（期刊论文按标题找回预印本 PDF；命中约一半到七成）、AAS Nova、NASA、Phys.org、Universe Today、ESA、ESO、DESI、Keck、Indico 会议导出、Zotero API（你的库）。

**拿不到 / 不做的**：

| 项 | 原因 |
| --- | --- |
| Astrobites | 对数据中心 IP 返回 403（GitHub Actions 上通常正常） |
| Nature Astronomy 的部分摘要 | Crossref 只登记元数据，不保证有 abstract（该刊最常见）；现在先用预印本补，补不到才只有标题 |
| 期刊正文 PDF（直接） | IOP 返回反爬 HTML、doi.org 403、多数要订阅；改为反查 arXiv 预印本，命中才做全文，否则回退「仅摘要」 |
| 预印本与正式版的差异 | 审稿改动、更名、撤稿可能对不上；按标题 + 作者 + 年份校验，未命中不回退错误版本 |
| hubblesite / webbtelescope / Chandra / NOIRLab | 没有公开 RSS（404） |
| 实时引力波告警 | 需要常驻监听，Actions 做不到 |
| 论文公共讨论区 | 需要后端与审核，不做 |
| 引用图谱 | 需要离线索引，不做 |

---

## 十一、已知限制

- **扫描版 PDF** 抽不出文本，会退回摘要级分析（卡片上会标"仅摘要"）。
- **采集量变化**至少要有两个发表日的记录；受分页上限和实际采集范围影响。
- **折叠**依赖 `<details>`：Apple Mail / iOS 正常折叠，Gmail 与 Outlook 会直接展开（可接受，内容不丢）。
- **网格纹理与卡片发光**在 Outlook 会被忽略，退化为纯色，不影响可读。
- **引文和数字回查不等于语义正确**——保留事实与来源文本供核对，科研解释、单位和结论仍需人工抽检。
- **SMTP 与磁盘不能原子提交**：服务器收信后、状态保存前崩溃仍可能重发。
- **采集范围有上限**：投稿量改称采集量；脉搏是近七天材料选样，不代表全领域全量动态。

---

## 十二、文档与许可

- [docs/PRODUCT_PLAN.md](docs/PRODUCT_PLAN.md) — 设计文档（为什么这样做，每个版本踩了什么坑）
- [docs/PROJECT_REVIEW.md](docs/PROJECT_REVIEW.md) — 项目评审（早期问题清单与评分诊断）
- [docs/FEEDBACK.md](docs/FEEDBACK.md) — 反馈怎么记：本机端点 / Google Sheet 两套方案
- [CHANGELOG.md](CHANGELOG.md) — 变更记录
- [MIT License](LICENSE)
