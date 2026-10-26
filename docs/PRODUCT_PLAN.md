# 从"论文摘要器"到"领域情报简报"：v2 产品方案

> 历史评审/方案记录。2026-10-26 的现状、修复和验证以 [审阅基线](REVIEW_2026-10-26.md)、[改动报告](CHANGE_REPORT_2026-10-26.md) 与 [AI 交接](AI_HANDOFF.md) 为准；下文旧数量和环境结论不代表当前状态。

> 本文回答两件事：**信息面怎么铺开**、**邮件怎么写才有用**，并给出可落地的代码结构与里程碑。
> 前提约束（来自上一轮）：个人优先、可逆设计、不破坏现有 env 契约、不引入常驻服务。

---

## 0. 一句话重定义

现在：**"今天 arXiv 上新了 12 篇，这是它们的中文摘要。"**
目标：**"今天宇宙学领域发生了什么，哪 3 件事值得你花 20 分钟，其余 9 件你扫一眼就知道不用看。"**

差别不在"源更多"，而在**从"罗列"转向"编辑"**。多源只是原料，真正的产品是**编辑判断**：选什么、为什么选、读者该怎么用。

---

## 1. 信息源体系：七层，按"能否改变你的行动"排序

不是所有源都值得接。判断标准只有一条：**这条信息能不能让你今天做一个不同的决定**（读某篇、改某段代码、投某个会、联系某个人）。按这个标准分七层：

### A. 一手文献（决定"读什么"）

| 源 | 接口 | 频率 | 成本 | 优先级 |
| --- | --- | --- | --- | --- |
| arXiv 新提交 | `export.arxiv.org/api/query`（现有） | 每日 | 免费 | **P0** |
| arXiv 每日公告 RSS | `rss.arxiv.org/rss/<category>`，每日午夜 ET 更新 | 每日 | 免费 | **P0** |
| arXiv **修订版**（v2+） | API 按 `lastUpdatedDate` 排序 | 每日 | 免费 | **P1** |
| 期刊正式发表（ApJ/AJ/JCAP/PRD/MNRAS/NatAs） | Crossref `/works` 或期刊 RSS | 每日 | 免费 | **P1** |
| 高被引/热点新论文 | ADS `sort=trending` 或 OpenAlex `cited_by_count` | 每日 | 免费（ADS 需 token） | P3 |

> 关于**修订版**：现有代码用 `published` 过滤，永远看不到"去年提交、昨天更新到 v2"的论文。而 v2 往往才是可以读的版本（审稿后修正）。这是一个被忽略的高价值信号。

### B. 二次解读（决定"哪些不用读原文"）

Astrobites（`astrobites.org/feed/`，每日 astro-ph 摘要，研究生视角）、AAS Nova、机构 press release（NASA/ESA/STScI/ESO/ALMA/NOIRLab）、Quanta/Symmetry/Physics Today。

**价值最高的其实是 Astrobites**：它每天已经替你读过一遍 astro-ph 并写了标题党式的导读。把它的条目和你抓到的论文做**标题模糊匹配**，就能直接回答"这篇有没有人已经科普过了"——这本身就是筛选信号。

### C. 社区信号（决定"热度"，判断谁在讨论什么）

| 源 | 接口 | 备注 |
| --- | --- | --- |
| Bluesky | `public.api.bsky.app/xrpc/app.bsky.feed.searchPosts`，**公开 GET 无需鉴权** | 天文圈近年主要迁到这里，是最真实的"谁在聊什么" |
| Mastodon / astrodon.social | 实例公开 timeline API | 补充 |
| CosmoCoffee | 论文讨论版块（`cosmocoffee.info`） | 帖子量不大，但每条都是深度讨论，信噪比极高 |
| ADS / Semantic Scholar | 引用数、influential citations | 冷启动期引用为 0，只能做长期信号 |

社区信号必须**降噪**：按"提到同一 arXiv ID 的次数"聚合，而不是按关键词搜。否则每天都是同一批网红论文。

### D. 领域事件（决定"什么时候要动手"）

- **数据发布**：DESI / Euclid / Rubin-LSST / CMB-S4 / LiteBIRD / ACT / SPT / Planck。这类事件一年几次，但一旦发生就是全领域焦点，值得单独做提醒（初期手工维护一份 `data_releases.yaml` 就够）。
- **会议/workshop 截止日期**：Indico 有官方导出 API（JSON/XML/iCal/Atom），可订阅指定 category（Cosmo 系列、Moriond、Rencontres 等）。初期用「手工维护 + 每周检查一次 URL」更省事。
- **招聘**：AAS Job Register、Academic Jobs Online、INSPIRE jobs。**这条很分裂**——对找博后/教职的人极其重要，对已有稳定位置的人是纯噪音。默认关闭，配置项开启。

### E. 工具与数据（决定"能不能马上用"）

ASCL（天体物理源码库）新条目、Zenodo 新数据集、GitHub 上的新 cosmology 包。频率低、噪音低、但一旦命中就是"能直接用"级别。

### F. 元信息（"全局感"的真正来源）

- 各分类**每日投稿量**（arXiv API 的 `opensearch:totalResults`）：量级突然变化往往意味着某个热点
- **主题聚类 + 周环比**：本周 vs 上周，哪些主题在升温
- **谁引用了你的工作**：需要 ADS author ID，属于 P3

### G. 不做的事

推特/X（API 收费且不稳定）、微信公众号（无 API）、全网爬虫、合作组内部动态。**接不进来的东西硬接，只会让项目变成维护地狱。**

---

## 2. 一个必须先说的排程问题

arXiv 的节奏是：新提交截止 **14:00 ET** → 公告 **20:00 ET** → RSS/列表在**午夜 ET** 刷新。

现在的 cron 是 `37 0 * * *`（00:37 UTC）：

- 夏令时（EDT，UTC-4）= **20:37 ET**，刚好在 20:00 公告后 37 分钟 ✅
- 冬令时（EST，UTC-5）= **19:37 ET**，**比公告早 23 分钟** ❌

也就是说，**一年中有半年，这个任务跑在 arXiv 当天公告之前**，抓到的是上一次公告的内容。这解释了为什么某些日子会出现"今天没有新论文"或"内容重复"。

建议改成 **`30 5 * * *`（05:30 UTC = 13:30 北京）**：既在 20:00 ET 公告之后，也在午夜 ET 的 RSS 刷新之后（05:00 UTC 冬令 / 04:00 UTC 夏令），两条数据路径都稳。代价是从"早上 8:37 收到"变成"下午 1:30 收到"。如果更看重早上读，用 `15 1 * * *`（01:15 UTC = 09:15 北京），只走 API 不走 RSS。

> 这是"信息源越多，对时序越敏感"的典型例子：多源不等于同时可用，**每个源有自己的新鲜度窗口**。

---

## 3. 邮件重设计：七段式编辑简报

现在是一张"总览 + 12 个卡片"。新版按**阅读预算**组织，而不是按论文循环：

```text
主题 宇宙学日报 · 2026-10-24 · 3 条主线 · 14 篇新论文

① 今日导读        LLM 主编，3-5 句：今天真正发生了什么
   今日雷达        主题分布 + 与你画像的匹配度（小图/色条）

② 精读 4 篇 深度卡片（见 4.2）
③ 速览 10 篇 一行一篇：中文标题 + 一句话结论 + 一个关键数字
④ 方法与工具      新代码/数据/软件，2-3 条
⑤ 领域动向        数据发布/会议截止/值得注意的媒体解读
⑥ 本周趋势        周报时出现：主题升降温、量级对比
⑦ 完整清单        折叠的纯列表 + 反馈按钮
```

设计原则：

1. **时间预算驱动排版。** 用户设 `READING_MINUTES=20`，系统按 4 篇精读 + 10 篇速览分配版面，多出来的进附录。这比"页数上限"更贴近真实约束。
2. **一屏之内给结论。** 手机首屏必须能回答"今天有没有我需要的东西"。
3. **深度卡片只给 4 篇。** 篇数是版面决策：4 篇精读 ＋ 10 条速览，再多"深度"就等于没有深度——这既是产品判断，也是成本控制。**v0.12 起，分钟数只用来定篇数，不再用来裁单篇内容**：研究背景与方法细节按证据写足，删掉的是篇数，不是信息。
4. **可追溯。** 每条评论标注"基于摘要"还是"基于全文"，并给出原文锚点，用户能一眼核对。**v0.12 起排序分也按项拆开展示**（关键词 / Zotero / 分类 / 库内 / 反馈），分数不再只有总分。

---

## 4. 核心难点：怎么"评论"才不是胡扯

这是整个 v2 的关键。LLM 评论论文最常见的问题是：**用自信的语气复述摘要，再加两句正确的废话**（"该方法具有重要价值""未来可进一步研究"）。要做成有用的评论，靠三个机制。

### 4.1 读者画像 `profile.yaml`——比关键词强一个数量级

```yaml
researcher:
  level: phd/postdoc/faculty
  field: 星系宇宙学
  questions:            # 你正在追问的问题
    - 高红移星系团的观测选择效应
    - 弱透镜巡天的系统误差
  methods:              # 你熟悉/偏好的方法
    - 光度红移
    - 半解析模型
  data:                 # 你常用的数据
    - DESI DR2
    - HSC
  current_projects:
    - 用 DESI 星表检验晕占据分布
  never:                # 明确不关心的
    - 纯理论暴涨模型
reading:
  minutes_per_day: 20
```

**为什么比关键词强**：关键词只能判断"是否相关"，画像能判断"**怎么相关**"。同一篇论文，对做观测的人意味着"新的系统误差来源"，对做模拟的人意味着"新的检验基准"。没有画像，模型只能写通用评论。

### 4.2 两阶段生成：先抽事实，再写判断

```text
Stage A — extract（便宜模型，可缓存）
  输入：摘要（或全文）
  输出：严格 JSON —— 问题/方法/数据/样本量/主要结果(带数字)/局限性/声称的贡献
  约束：每个字段必须附一句原文引用；摘要没写的填 null，不许编

Stage B — assess（强模型）
  输入：Stage A 的事实 JSON + profile.yaml + 同批其他论文的标题
  输出：对你有什么用/该读哪部分/批判性评论/置信度
  约束：只能基于 Stage A 的事实做推断；引用事实时要指回字段
```

把"事实"和"判断"分开，是控制幻觉最有效的手段：Stage A 有原文引用可核对，Stage B 即使判断有偏差，事实层也是干净的。而且 Stage A 结果按 `(item_id, prompt_version)` 缓存，重跑免费。

### 4.3 三条硬规则

1. **数字回查。** 评论里出现的所有数字必须在原文中出现过，用正则回查；查不到就删掉该句。这条能干掉绝大多数"编造样本量"。
2. **禁止无源断言。** 每条批判性评论必须挂在 `limitation` 或 `method` 字段上，不允许凭空生成"可能存在的问题"。
3. **区分"摘要级"与"全文级"。** 只有摘要时，评论必须限定措辞（"从摘要看…"）；只有读了全文才允许评价"实验设计""统计检验是否充分"。

### 4.4 全文精读：只给 top-N

默认基于摘要。对 `depth_items`（默认 4 篇）下载 PDF → 抽文本（`pypdf`）→ 让模型重点看方法节和图表标题，回答三个问题：样本/数据是否支撑结论、有没有明显的系统误差讨论、结果与摘要宣称的是否一致。

成本与风险：PDF 下载有礼貌性问题（加延迟、尊重 429）、解析质量参差、单篇 token 可达 30k。所以**严格限制在 4 篇**，且失败就静默退回摘要级。

---

## 5. 代码结构

### 5.1 泛化数据模型

现在只有 `Paper`。v2 需要 `DigestItem` 承载论文/新闻/事件/招聘：

```python
Kind = Literal["paper", "news", "event", "job", "tool"]

@dataclass(frozen=True)
class DigestItem:
    kind: Kind
    id: str                    # 稳定主键：arxiv:2401.00001 / doi:10.xxxx / url:<hash>
    title: str
    url: str
    published_at: datetime
    source: str                # "arxiv" / "astrobites" / "journal_rss" / "bluesky"
    text: str = ""             # 摘要或正文摘要
    meta: dict[str, Any] = field(default_factory=dict)   # authors/doi/categories/citations
    signals: dict[str, float] = field(default_factory=dict)  # citations, mentions, recency
    score: float = 0.0
    analysis: "Analysis | None" = None

@dataclass(frozen=True)
class Analysis:
    title_zh: str
    tldr: str
    problem: str | None
    method: str | None
    data: str | None
    result: str | None
    limitation: str | None
    relevance: str                       # 对你有什么用（2-3 句）
    relevance_kind: Literal["direct", "adaptable", "background", "none"]
    read_advice: Literal["skim", "figures", "deep_read"]
    critique: str                        # 批判性评论
    confidence: int                      # 1-5，模型自评
    evidence: list[str]                  # 原文锚点
    basis: Literal["abstract", "fulltext"]
```

`extra: dict` 逃生舱 + 3 个强类型字段，避免以后每加一个信号就改模型。

### 5.2 流水线五阶段

```text
collect   并行拉取各源 → list[DigestItem]（每源独立 try/except + 超时 + 健康状态）
   ↓
normalize 去重（arxiv id 去 vN / DOI / 标题指纹）、时间窗口过滤
   ↓
prescreen 规则 + 向量粗筛：paper 留 top 25，news 留 top 5（避免后面 LLM 成本爆炸）
   ↓
analyze   Stage A 抽取（缓存） → Stage B 评估（仅 paper 深度项 + news 摘要）
   ↓
compose   主编导语（跨条综合：共同主题/互相矛盾/方法趋同）+ 按阅读预算分栏
   ↓
render    email / markdown，输入是 digest.json
```

**关键：中间产物 `digest.json` 落盘。** 渲染只读 JSON，测试用 golden 文件，调试不用重跑 API——这一条能把开发迭代速度提一个数量级，也让"昨天 vs 今天"的对比成为可能。

### 5.3 源接口

```python
class Source(Protocol):
    name: str
    def fetch(self, window: Window, profile: Profile) -> list[DigestItem]: ...
```

每个源：独立超时、独立失败、返回健康状态写入运行报告。**任何一个源挂掉，邮件照发**（延续现有的降级哲学）。

### 5.4 新增配置（全部追加，不改动现有名字）

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `PROFILE_FILE` | `profile.yaml` | 读者画像路径 |
| `ENABLED_SOURCES` | `arxiv` | 逗号分隔，逐步开启 |
| `READING_MINUTES` | `20` | 阅读预算，驱动排版 |
| `DEPTH_ITEMS` | `2` | 深度分析篇数 |
| `BREADTH_ITEMS` | `8` | 速览篇数 |
| `FULLTEXT_DEPTH_ITEMS` | `0` | 全文精读篇数（0 = 关闭） |
| `LLM_MODEL_FAST` | 同 `OPENAI_MODEL` | 抽取用便宜模型 |
| `LLM_MODEL_SMART` | 同 `OPENAI_MODEL` | 评估/导读用强模型 |
| `DIGEST_KIND` | `daily` | `daily` / `weekly` |
| `COST_BUDGET_USD` | `0.5` | 单次运行预算，超了就降级 |

---

## 6. 里程碑（每个都能独立上线）

| 里程碑 | 内容 | 周期 | 验收标准 |
| --- | --- | --- | --- |
| **M1 编辑能力** | 画像 + 两阶段分析 + 七段式模板 + `digest.json`。**仍然只用 arXiv 一路** | 3-5 天 | 同一批 12 篇，新版能明确回答"哪 3 篇与我有关、各有什么用"；人工抽查 10 条评论，事实错误 ≤1 |
| **M2 多源接入** | arXiv RSS/修订版 + Astrobites/AAS Nova + 期刊 RSS + 事件源 | 4-6 天 | 每日 item 30-80 条，去重正确，任一源失败不影响其他源与发送 |
| **M3 全局视角** | 主题聚类、周环比、引用信号、投稿量统计 | 3-5 天 | 周报能给出"本周 XX 主题较上周上升 N%"且可核对 |
| **M4 闭环** | 反馈按钮、全文精读、权重学习 | 3-5 天 | 连续两周反馈后，前 5 名命中率有可测量提升 |

**建议从 M1 开始**，理由很实际：M1 只动 LLM 和模板，不碰抓取，风险最低，但**用户可感知的提升最大**——邮件从"摘要列表"直接变成"有人读过并给你意见的简报"。M1 跑两周，如果"这封邮件值得读"成立，再铺信息源。

---

## 7. 成本与风险

**成本估算**（每天一次，14 篇论文，用 gpt-4o-mini 级别）：

| 阶段 | 调用次数 | 输入 | 输出 | 估算 |
| --- | --- | --- | --- | --- |
| Stage A 抽取 | 14 | ~1.5k tok | ~400 tok | ~$0.01 |
| Stage B 评估 | 12 | ~1k tok | ~500 tok | ~$0.01 |
| 主编导语 + 趋势 | 2 | ~6k tok | ~800 tok | ~$0.01 |
| 全文精读 | 2 | ~30k tok | ~1k tok | ~$0.05 |
| **合计** | 30 次 | | | **≈$0.08/天，$2.4/月** |

多源接入后 item 数上升，但 `prescreen` 会把进入 LLM 的数量控制在 25 以内，成本不会线性爆炸。

**主要风险**：

1. **源越多，越脆。** 每个源都是一个 parser + 一个会变的页面结构。对策：每源写 fixture 测试 + 每日报告里显示各源健康状态，坏了要能一眼看出来。
2. **评论质量的隐性退化。** LLM 输出看起来永远"很像那么回事"，退化是静默的。对策：建 20 篇人工标注的小评测集（每条评论标"有用/无用/错误"），每改一次 prompt 就跑一次。这是 v2 唯一需要"工程纪律"的地方。
3. **信息过载换个形式回来。** 源从 1 个变成 7 个，邮件很容易变成 60 条。对策：阅读预算是硬约束，超出的直接进附录；宁少勿多。
4. **时序依赖。** 各源新鲜度窗口不同（见第 2 节），跑太早会漏，跑太晚会旧。对策：每个源记录 `data_as_of`，在邮件里标注。

---

## 8. 已确认的设定（2026-10-24）

| 项目 | 决定 | 影响 |
| --- | --- | --- |
| 阅读预算 | **20 分钟 ≈ 4 篇精读 + 10 篇速览**（v0.12 起分钟数只换算篇数，不再限制单篇内容长度） | `DEEP_PAPERS=4`、`BRIEF_PAPERS=10`，其余进附录 |
| 事件类 | **要**会议/workshop 截止 + **要**数据发布；招聘默认关闭（留开关） | 事件源按领域包挂载 |
| 语言 | **英文原标题 + 中文评论** | 模板不改标题，中文标题只作为 `title_zh` 附注（可选显示） |
| 覆盖范围 | **从"宇宙学专用"改为"全学科可选"** | 见第 9 节，这是本次最大的范围变更 |

---

## 9. 全学科扩展：领域包（Domain Pack）

### 9.1 先说实话：全学科不等于每科都深

这个需求会碰到两个绕不开的约束，提前讲清楚比后期返工便宜：

1. **二手源的分布极不均匀。** astro-ph 有 Astrobites、AAS Nova、一堆机构 press release，几乎每天有人替你消化一遍；而 `math.AG`、`econ.EM` 基本只有 arXiv 本身 + 偶尔一篇 Quanta。所以"深度"不可能全学科一致——**每个领域包必须自报能力等级**，邮件里也要诚实标注"本领域仅支持摘要级"。
2. **通用 prompt 会稀释评论质量。** 同一个"批判性评论"指令，对宇宙学要问"系统误差和选择效应"，对机器学习要问"baseline 是否公平、ablation 是否完整"，对生物要问"样本量与统计功效"。**一把通用尺子量所有领域，结果就是正确的废话。** 这决定了必须按领域注入评价维度。

### 9.2 领域包是什么

一个 YAML 文件就是一个领域，**纯数据扩展，加新学科不用改代码**：

```yaml
# arxiv_digest/packs/cosmology.yaml
id: cosmology
name: 宇宙学
arxiv_categories: [astro-ph.CO, astro-ph.GA, gr-qc, hep-ph]
capability: high                     # high / medium / low，决定邮件里怎么标注
sources:
  secondary: [astrobites, aas_nova, press_release]
  events: [data_release, conference]
  journals: [apj, mnras, prd, jcap]
evaluation:                          # ← 注入 Stage B，决定"评论什么"
  focus: [选择效应, 系统误差, 统计显著性, 与既有测量的一致性]
  red_flags: [过度外推, 未讨论系统误差, 依赖单一数据集]
  reader_context: 读者关心测量精度与宇宙学参数约束
title_hint: 中文评论中保留英文术语（如 BAO、RSD、σ8），不强行翻译
```

用户勾选 `cosmology` + `cs_ml`，系统做三件事：

- **并集**：arXiv 分类取并集，源取并集
- **分派**：每篇论文按主分类匹配到最具体的领域包，用该包的 `evaluation` 去评估
- **标注**：邮件里注明条目来自哪个领域包、该包的能力等级

这就是"各学科各方向都具有"的正确实现方式：**不是做一套通用逻辑，而是做一套装配机制 + 一组领域知识。**

### 9.3 领域能力矩阵（首发范围建议）

| 领域包 | arXiv 分类 | 二手源 | 首发 | 深度 |
| --- | --- | --- | --- | --- |
| `cosmology` 宇宙学 | astro-ph.CO, astro-ph.GA, gr-qc, hep-ph | Astrobites, AAS Nova, PR | ✅ | ★★★★★ |
| `astro` 天体物理（泛） | astro-ph.SR/EP/HE/IM | 同上 | ✅ | ★★★★★ |
| `cs_ml` 机器学习 | cs.LG, cs.AI, cs.CL, cs.CV, stat.ML | HF Daily Papers, Papers with Code | ✅ | ★★★★★ |
| `physics` 物理 | cond-mat.*, quant-ph, hep-th, hep-ex | Quanta, APS/Physics 新闻 | ✅ | ★★★★ |
| `math` 数学 | math.* | Quanta | ✅ | ★★★ |
| `bio` 生物医学 | q-bio.*, bioRxiv 镜像 | bioRxiv/PubMed 新闻 | ⬜ | ★★★ |
| `econ` 经济金融 | econ.*, q-fin.* | NBER, RePEc | ⬜ | ★★★ |
| `stat` 统计 | stat.* | —（依赖 `cs_ml` 的源） | ⬜ | ★★ |

**建议首发 5 个包**（✅ 行）。理由：它们覆盖了 GitHub 上最可能出现的目标用户，且每个都有可用的二手源，`capability: high` 名副其实。剩下三个等有人真的要用再加——这正好印证"领域包是纯数据扩展"的价值。

### 9.4 "勾选"到底怎么勾

GitHub Actions 环境下没有设置页，所以给三条路径，按成本递增：

| 方式 | 体验 | 成本 | 何时做 |
| --- | --- | --- | --- |
| `FIELDS=cosmology,cs_ml` 环境变量 | 文本，易写错但零成本 | 0 | **M1** |
| `config/fields.yaml` 带注释的勾选清单（取消注释即可） | 直观，适合新手 | 1 小时 | **M1** |
| `workflow_dispatch` 下拉选择（`type: choice`，≤10 项） | 手动触发时最舒服 | 2 小时 | M2 |
| 静态网页向导（勾选 → 生成 YAML → 复制） | 最好，也是开源引流利器 | 1-2 天 | 开源阶段 |

第四条值得单独说：一个"勾选你的领域 → 复制这段配置"的静态页面，既零后端，又是最好的 README 替代品。但它属于"门面"，不属于"能力"，等 M1/M2 跑通再做。

另外保留一条**逃生通道**：`ARXIV_CATEGORIES` 仍然可以直接写任意分类（如 `math.AG`、`eess.AS`），即使没有对应领域包也能用，只是会退化为"仅摘要"模式。现有用户配置不因此失效。

### 9.5 关键洞察：多用户不需要后端

"让不同用户勾选自己的领域"听起来像要做账号系统，其实不用：

> **每个人 fork 一份仓库、配自己的 Secrets、勾自己的领域包即可。**

GitHub Actions 天然就是多租户，成本为零。真正需要后端的只有一件事——**反馈闭环**（👍/👎 需要一个能写入的端点）。所以：

- 多用户、多领域、多时区 → **纯配置问题，不需要服务端**
- 只有"越用越准"的个性化学习 → 需要一个轻量存储（Cloudflare Worker + KV，或干脆让用户把权重提交回自己的 fork）

这个判断能省掉整个后端，也是决定项目能否长期维护的分水岭。

### 9.6 领域包与"技能"的关系

领域包不是技能，但它是**技能的配置载体**：

```text
领域包（数据：分类/源/评价维度） → 决定"看什么、怎么评"
技能（代码：Enricher）           → 决定"做什么加工"
```

例如 `trend` 技能对所有领域通用；但 `evaluation.focus` 只有领域包知道。这样加一个新学科 = 写一个 YAML（10 分钟），加一个新能力 = 写一个 Enricher（半天）。两条扩展轴互不干扰。

---

## 10. 修订后的里程碑

| 里程碑 | 内容 | 周期 | 验收 |
| --- | --- | --- | --- |
| **M1 编辑能力 + 领域包骨架** | `profile.yaml`、`packs/` 加载与合并、两阶段分析、七段式模板、`digest.json`。仍只用 arXiv 一路 | 4-6 天 | 换 `FIELDS` 能明显改变筛选与评论角度；同一批 12 篇能回答"哪 3 篇与我有关、各有什么用" |
| **M2 多源接入** | arXiv RSS/修订版 + 首批 5 个领域包的二手源与事件源 | 5-7 天 | 每日 30-80 条，去重正确，任一源失败不影响发送；邮件标注各源 `data_as_of` |
| **M3 全局视角** | 主题聚类、周环比、引用信号、投稿量 | 3-5 天 | 周报能给出可核对的主题升降幅 |
| **M4 闭环** | 反馈按钮、全文精读、偏好学习 | 3-5 天 | 连续两周反馈后前 5 名命中率可测量提升 |
| **M5 门面** | 网页配置向导、英文 README、截图、LICENSE、CI 徽章 | 2-3 天 | 陌生人 5 分钟跑通 |

与第 6 节相比的变化只有一条：**M1 增加了领域包骨架**。原因是 `evaluation.focus` 会被写进 prompt 结构，如果 M1 不做，M2 接二手源时就要重构分析层——这是典型的"现在省两小时，以后还两天"。

---

## 11. 补充：几个值得加、但优先级低于 M1 的能力

| 能力 | 价值 | 成本 | 说明 |
| --- | --- | --- | --- |
| **已读过滤** | ★★★★★ | 0.5 天 | 已在 Zotero 收藏的论文不再推送，或标为"已收录"。这是最容易被忽略、但最能消除"又给我看一遍"的负面体验 |
| **术语一致性** | ★★★★☆ | 0.5 天 | 建一个 `glossary.yaml`（BAO→重子声学振荡、RSD→红移空间畸变…），注入 prompt，避免同一概念每天换译法 |
| **汇报导出** | ★★★☆☆ | 0.5 天 | `--export slides` 把今日必读导成 Markdown/PPT，组会直接讲 |
| **危险信号标记** | ★★★☆☆ | 1 天 | 论文结论与领域内已确立结果冲突时标红（需要一张"已知结论"清单，维护成本不低） |
| **引用我的工作** | ★★☆☆☆ | 1 天 | 需要 ADS author ID，属于长期信号 |

---

## 12. 领域大动态追踪：能否在 GitHub Actions 上实现

### 12.1 先把平台边界说清楚

GitHub Actions 不是服务器，有五条硬边界，直接决定"哪些功能能做、哪些做不了"：

| 边界 | 具体限制 | 对本项目的影响 |
| --- | --- | --- |
| **单次运行上限** | 6 小时（job 级） | 够用；但全文精读 + 多源抓取要控在 10 分钟内，否则每天排队浪费配额 |
| **无持久存储** | 每次跑都是干净的文件系统 | **一切状态必须回写 git 或走 cache**（见 12.2） |
| **cron 不准点** | 最小 5 分钟间隔，高峰期常延迟 10-30 分钟，极端情况数小时 | 不能依赖"准点 8:00 发信"；邮件要自带 `data_as_of` |
| **公共仓库 60 天无活动会停用定时任务** | 官方行为 | 每日把状态 commit 回仓库，既解决持久化，又顺手避免被停用 |
| **出口 IP 是 Azure 段** | 部分邮件服务商/MTA 会拒收 | Gmail/QQ/163 一般可用；不行就换 Resend/SendGrid API |

**结论：能实现，但"状态放哪"是唯一的真问题。**

### 12.2 状态怎么存（三种方案，推荐第二种）

| 方案 | 做法 | 优点 | 缺点 |
| --- | --- | --- | --- |
| artifact | 上传 seen.json | 无需改动仓库 | 90 天过期、无法读取历史 |
| **cache** | `actions/cache` 缓存 `state/` | 零侵入、快 | 7 天不访问会被清、跨分支不共享 |
| **git commit 回仓库** | 每日 commit `state/seen.json` | 永久、可审计、避免 60 天停用 | 需要 `contents: write` 权限，产生提交噪音 |

**推荐 cache + 每周一次 git commit 兜底**：日常靠 cache，周末把状态固化到仓库，兼顾性能与可靠性。

### 12.3 "天文学界的大动态"具体是什么

这个概念拆开其实是三类，实现方式完全不同：

#### A. 已排期的确定性事件（价值最高、最容易做）

数据发布、会议召开、提案截止——**这些日子是提前几年就定好的**，不需要"抓"，只需要"记"。

```yaml
# events/astronomy-calendar.yaml
- date: 2026-11-12
  kind: conference
  title: "DESI Collaboration Meeting"
  url: "https://..."
  tags: [cosmology, survey]
- date: 2026-12-01
  kind: deadline
  title: "JWST Cycle 5 提案截止"
  url: "https://stsci.edu/..."
- date: 2027-03-15
  kind: data_release
  title: "Euclid DR1 预计发布"
  url: "https://..."
  confidence: 中        # 预计日期要标注确定性
```

每天做三件事：**倒计时**（还有 N 天）、**当天提醒**、**过期清理**。零外部依赖、零失效风险，是"全局感"性价比最高的部分。

#### B. 机构新闻与 press release（自动，但需要降噪）

| 源 | 形态 | 说明 |
| --- | --- | --- |
| NASA / ESA / STScI / ESO / ALMA / NOIRLab / Chandra | RSS 或新闻页 | ESO 有官方 RSS；其余多数支持 |
| DESI / Euclid / Rubin-LSST / CMB-S4 / LiteBIRD | 项目官网新闻页 | 无统一格式，需逐站适配 |
| Quanta / Symmetry / Physics Today | RSS | 高质量但更新慢 |
| arXiv 新闻组？ | — | 不存在，别找 |

降噪手段：**关键词白名单**（`data release`、`DR1/DR2`、`first light`、`survey`、`Cycle`、`call for proposals`、`result`）+ **冷启动期人工审核**（前两周把抓到的条目人工标一遍，校准阈值）。

#### C. 社交与媒体热度（可选）

Bluesky 公开搜索（无需鉴权）按 arXiv ID 聚合提及次数，判断"有没有人在讨论"。噪音大，只在 `signals` 里当参考分，不单独成板块。

### 12.4 会议报告（talks）怎么抓 —— 这是最被低估的一块

**Indico 是天文/物理界的事实标准日程系统**，且提供结构化导出：

```text
https://<indico-host>/export/event/<event_id>.json?detail=contributions
→ [{ title, speakers, description, start_dt, track, ... }, ...]
```

这意味着你可以**不爬 HTML**，直接拿到一场会议的全部报告标题与摘要。实现方式：

```yaml
# conferences.yaml —— 你关心的一批会议（一年维护一次）
- name: "Cosmo 2026"
  indico: "https://indico.cern.ch/event/1234567"
  tags: [cosmology]
```

每日任务对**未来 7 天内召开**的会议拉一次 contributions，按 `profile` 过滤出相关报告，交给 LLM 做一版"这场会议在讲什么"。会议报告的价值在于：**很多结果在会议讲了但论文还没写**，这是 arXiv 抓不到的信息。

局限：需要知道 event id（大多数会议有，且能通过 Indico 搜索页找到）；不同 Indico 实例的 JSON 字段略有差异，要做兼容。

### 12.5 GitHub Actions 可行性矩阵

| 功能 | 可行 | 实现依赖 | 主要风险 |
| --- | --- | --- | --- |
| arXiv 新论文 + 修订版 | ✅ | 现有 API | 429（已有退避） |
| 期刊正式发表 | ✅ | Crossref / 期刊 RSS | 源失效 |
| 机构新闻 / press release | ✅ | RSS + 关键词过滤 | 无标准格式，需逐站适配 |
| **会议议程与报告** | ✅ | Indico export JSON | 需维护 event id 列表 |
| 数据发布 / 会议 / 截止日期 | ✅ | **策展 YAML**（不依赖外部） | 需要你每年更新一次 |
| 社交热度（Bluesky） | ✅ | 公开 GET，无需鉴权 | 限流、噪音 |
| 引用信号（ADS/OpenAlex） | ✅ | token / 免费 | ADS 5000 req/天配额 |
| 全文精读 | ⚠️ | 下载 PDF + 解析 | 时间、费用、礼貌性；限制 4 篇 |
| 邮件发送 | ✅ | SMTP | Azure IP 可能被个别服务商拒 |
| 跨天去重 | ⚠️ | cache / git commit | 需要写权限，注意并发 |
| 反馈闭环（👍/👎） | ❌ | 需写入端点 | 用 GitHub Issue 或外部 Worker |
| 实时告警（如引力波） | ❌ | 需常驻监听 | Actions 不适合，跳过 |

### 12.6 更新后的邮件结构

加入"大动态"后，首屏需要重新排序——**先给世界，再给你**：

```text
① 今日头条   1-3 条：数据发布 / 重大结果 / 会议开幕（带链接）
② 主编导语   3-5 句：这些东西连起来意味着什么
③ 精读 4 篇  论文或会议报告，含"对你有什么用"与批判性评论
④ 速览 10 篇  一行一篇
⑤ 日历       倒计时：会议 D-3、提案截止 D-14、数据发布 D-45
⑥ 方法与工具 新代码/数据/软件
⑦ 附录       完整清单 + 反馈
```

### 12.7 里程碑影响

- **M2 拆成两半**：`M2a` 大动态（日历 YAML + 机构新闻 RSS + 关键词降噪），`M2b` 论文多源（期刊 RSS + arXiv 修订版 + 二手解读）。**M2a 应先做**，因为它零外部依赖、当天就能出效果。
- **M3 新增**：会议报告接入（Indico），因为它的数据质量最高，但依赖 `conferences.yaml` 的人工维护。

### 12.8 已核实的事件事实（2026-10-24 检索）

首批日历已落盘：[`config/events.yaml`](../config/events.yaml)（数据发布与里程碑）与 [`config/conferences.yaml`](../config/conferences.yaml)（会议系列与届次）。

| 条目 | 日期 | 确定性 | 来源 |
| --- | --- | --- | --- |
| **Euclid DR1 Foundation** | **2026-11-12** | 确定 | [ESA Cosmos timeline](https://www.cosmos.esa.int/web/euclid/timeline) |
| Euclid DR1 Complete | 2027 年中 | 预计 | 同上 |
| Euclid DR2 / DR3 | 2029-03 / 2031-10 | 预计 | [Caltech 时间线](https://euclid.caltech.edu/page/data-release-timeline) |
| Rubin DP1 | 2025-06-30（已发布） | 确定 | [dp1.lsst.io](https://dp1.lsst.io/) |
| Rubin Early DP2 | 2026-07-27（已发布） | 确定 | [dp2.lsst.io](https://dp2.lsst.io/) |
| Rubin DP2 完整版 | 未公布 | 待定 | [Rubin 数据发布页](https://rubinobservatory.org/for-scientists/data-products/recent-data-releases) |
| DESI DR2 第二批论文 | 2026-07-30 | 确定 | [DESI DR2](https://data.desi.lbl.gov/doc/papers/dr2/) |
| DESI DR3（五年巡天） | 约 2028 | 待定 | [DESI 数据释放](https://data.desi.lbl.gov/doc/releases/)，五年巡天完成约 96% |
| PFS 正式全运行 | 2025-01-10 | 确定 | [pfs.naoj.org](https://pfs.naoj.org/) |
| PFS 首次数据发布 | 未公布 | 待定 | 同上 |
| **CSST 发射** | 2027（年精度） | 预计 | 多次推迟，2025 年初口径为 2027 |
| 中国天文学会 2026 年会 | 2026-10-27～31，石家庄 | 确定 | [年会通知](https://jsas.nju.edu.cn/d6/2c/c21057a841260/pagem.htm) |
| 第 28 届郭守敬（星系宇宙学） | 2026-05-22～25，海口 | 确定 | [会议站](https://conf.koushare.com/conf/gsj2026/) |
| 第 10 届海峡两岸粒子物理与宇宙学 | 2026-01-18～22，广州 | 确定 | [Indico](https://indico.itp.ac.cn/event/361/) |
| CSST 2026 年度科学年会 | 2026-07-27～31，喀什 | 确定 | [国家天文台](https://nao.cas.cn/news/tz/202606/t20260602_8212337.html) |

**三个由此暴露的schema约束**（已写进 YAML，实现时必须支持）：

1. `date` 必须允许 `null` —— 大量"知道在等、但不知道哪天"的条目存在（Rubin DP2、PFS DR1、CSST）。
2. 需要 `date_precision`（`日`/`月`/`年`）—— 把 2027 年发射渲染成"还有 3 天"是错的。
3. 国内会议需要 **series + instances 双层** —— 届次日期公布很晚，只能靠"该系列历年都在 5 月"给软提醒。

### 12.9 由此确定：读者的领域包是「巡天宇宙学」，不是泛宇宙学

用户等待的五项（Euclid / DESI / Rubin / PFS / CSST）**全部是巡天类项目**，无一例外。这比泛化的 `cosmology` 包信号强得多，所以 `packs/cosmology.yaml` 应按巡天宇宙学写：

```yaml
evaluation:
  focus:
    - 系统误差来源（光度红移、剪切标定、选择函数）
    - 巡天面积/深度与统计量
    - 与 Planck/DESI/Euclid 既有约束的一致性
    - BAO/RSD/弱透镜三类探针的交叉检验
  red_flags:
    - 未讨论测光系统误差
    - 用单一探针下强结论
    - 误差棒来源不明
reader_context: |
  读者做巡天宇宙学，关心新数据（Euclid DR1、DESI、Rubin、PFS、CSST）能否
  改进参数约束，以及方法上的坑。对纯理论、非巡天数据的论文关注度低。
```

### 12.10 M2a 已实现（2026-10-24）

按"日历先于 LLM"的顺序落了第一块，同时把 4 精读 + 10 速览的版面配比一起做了。

**新增**

| 文件 | 作用 |
| --- | --- |
| `arxiv_digest/calendar.py` | 读两个 YAML → `CalendarBundle`；处理 null 日期、三档精度、系列推断；任何异常都降级为空日历 |
| `config/events.yaml` | 数据发布与里程碑（Euclid/DESI/Rubin/PFS/CSST） |
| `config/conferences.yaml` | 国内会议系列 + 已排期届次 |

**修改**

| 文件 | 改动 |
| --- | --- |
| `pipeline.py` | `PipelineResult` 增加 `calendar`；日历在 arXiv 抓取**之前**加载，保证 arXiv 挂了日历照样成立 |
| `emailer.py` | 接受 `calendar`；拆 `deep_papers` / `brief_papers`；`generated_at` 改用 `DIGEST_TIMEZONE`（原先用 UTC，Actions 里差 8 小时）；新增 `_snippet` 做速览截断 |
| `templates/digest_email.html` | 新增「近期日程」块、「精读 / 速览」两个分组；头部补 `background-color` 兜底（原先 Outlook 下白底白字看不见） |
| `main.py` | 透传日历与时区；修掉 `try: notify_failure(...) finally: raise` 用发信异常覆盖真实错误的问题 |
| `config.py` | 新增 5 个日历配置项 + `DEEP_PAPERS` / `BRIEF_PAPERS`；`MAX_PAPERS` 默认 12 → 14 |
| `scripts/preview_digest.py` | 加一行日程纯文本输出，方便不联网自查 |
| `requirements.txt` / `pyproject.toml` | 加 `pyyaml`；`pyproject` 补 `dependencies` |

**新增环境变量**（都有默认值，不配也能跑）

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `CALENDAR_ENABLED` | `true` | 关掉就完全不出日历块 |
| `CALENDAR_EVENTS_PATH` | `config/events.yaml` | |
| `CALENDAR_CONFERENCES_PATH` | `config/conferences.yaml` | |
| `CALENDAR_HORIZON_DAYS` | `120` | 超过这个天数的只显示"更远"一条 |
| `CALENDAR_RECENT_DAYS` | `14` | 刚过去的会议显示多久 |
| `DEEP_PAPERS` | `4` | 精读篇数 |
| `BRIEF_PAPERS` | `10` | 速览条数 |

**两条不需要联网、不需要 API Key 的自查命令**

```powershell
# 1) 只验日历逻辑
python -c "from arxiv_digest.calendar import load_calendar, format_plain_text; print(format_plain_text(load_calendar('config/events.yaml','config/conferences.yaml')))"

# 2) 只验模板渲染（不联网、不发信）
python -c "from arxiv_digest.calendar import load_calendar; from arxiv_digest.emailer import render_email_html; c=load_calendar('config/events.yaml','config/conferences.yaml'); open('preview.html','w',encoding='utf-8').write(render_email_html('（测试总览）', [], c))"
```

预期：第 1 条应输出 `D-19 数据发布｜Euclid DR1 Foundation 数据发布` 和 `D-3 会议｜2026 年学术年会 · 中国天文学会学术年会` 两行，加一行"日期未公布"，加一行"更远：2027 年 6 月 · Euclid DR1 Complete"。

**已知未做**：`llm.py` 仍是「14 篇一次 prompt + 单次 `json.loads`」的旧结构。14 篇比原来的 12 篇更容易触发截断，所以在 M1 换成两阶段（extract/assess）之前，**这条是当前最大的单点故障**。如果近期想降风险，最小改动是把 `generate_digest_content` 改成分批（每批 5 篇）+ 逐批容错。

---

## 13 读者画像（v1）

画像不是"我喜欢什么"的形容词列表，而是**输出契约**：它规定评论必须按什么顺序、回答哪些问题、什么情况下允许说"不知道"。所以它落在 YAML 里而不是 prompt 的一段散文里——散文改不动、也没法逐条核对。

### 13.1 读者的自我描述

| 维度 | 内容 |
| --- | --- |
| 身份 | 博士生 |
| 方向 | 星系宇宙学 / 星系形成与演化 |
| 手上的数据 | 星系巡天、宇宙学模拟、由模拟转出的 mock（含巡天选择函数） |
| 主探针 | 星系群、星系团 |
| 交叉关注 | 弱引力透镜、聚类、BAO/RSD、丰度、与其他探针的一致性 |

### 13.2 五个问题 → 七个输出字段

读者明确说了他想知道什么，顺序就是他的优先级。这个顺序照搬进 `profile.yaml` 的 `questions`，Stage B 必须逐条填：

| # | 读者想问 | 输出字段 | 读不到时 |
| --- | --- | --- | --- |
| 1 | 用了什么数据 | `data` | 写"摘要未提" |
| 2 | 怎么筛选处理 | `processing` | 写"摘要未提" |
| 3 | 怎么校正误差、提取信号 | `systematics` | 明确写"未见讨论" |
| 4 | 用什么方法研究什么问题 | `method` | 写"摘要未提" |
| 5 | 得到什么新结果 | `result` | 必填 |
| — | 作者自己承认的局限 | `caveat` | 写"作者未讨论" |
| — | 这篇能怎么用到我手上的工作 | `for_you` | 必须是动作 |

两个额外字段的理由：

- **`caveat` 单独成列**，不和 `systematics` 合并。作者承认的局限与"我看出来的漏洞"是两件事，混在一起会让人以为作者自己说了。前者是事实，后者是我的判断。
- **`for_you` 必须是动作。** 这是画像里最重要的一条约束。写成"提供了有价值参考"等于没写；写成"它的 richness-mass 关系可以直接替换你 mock 里的那条"才有用。

### 13.3 为什么是"方法读者"而不是"意义读者"

这位读者关心的是**技术链条**（数据→cut→系统误差→估计量→误差棒），不是领域叙事。这条判断带来三个直接后果：

1. **`for_you` 权重高于"这有多重要"。** 一封信里 14 条，每条都评价意义会变成噪声。
2. **速览只给 `result` + `for_you`，各不超过 40 字。** 没有空间讲背景。
3. **禁止解释教科书概念。** 画像里 `background` 字段就是为此存在：它告诉模型"红移、光度距离、晕质量函数不用解释"。

### 13.4 领域包的评价标准

两个包（`galaxy_cosmology`、`galaxy_formation`）的能力等级都是 `high` —— 因为这是读者本人的方向，模型判断的可信度最高，邮件里不必加免责标注。

评价标准才是包的价值所在。以 `galaxy_cosmology` 为例：

- **focus（做到什么算扎实）**：质量标定是否进误差预算、选择函数与 Eddington bias 是否处理、投影与成员污染、多探针是否自洽、mock 是否含真实选择函数。
- **red_flags（出现即扣分）**：用未标定的质量-观测关系做定量宇宙学、单探针强断言、只报统计误差、mock 不含选择函数却做观测对比、把张力直接说成新物理。

`caveat` 字段会用 red_flags 反查——这是"防幻觉"之外的第二道闸：不是防止模型编造，而是防止模型漏掉这个领域公认的坑。

### 13.5 落盘文件

| 文件 | 作用 | 是否已被代码读取 |
| --- | --- | --- |
| `config/profile.yaml` | 读者画像 + 输出契约 + 打分锚点 | ❌ M1 接线 |
| `config/packs/galaxy_cosmology.yaml` | 星系宇宙学：分类、评价标准、源 | ❌ M1 接线 |
| `config/packs/galaxy_formation.yaml` | 星系形成与演化 | ❌ M1 接线 |
| `config/packs/generic.yaml` | 兜底包（`capability: low`） | ❌ M1 接线 |
| `arxiv_digest/prompts/assess.md` | Stage B 提示词骨架，用画像渲染 | ❌ M1 接线 |

**这五个文件都是配置，不是代码**——现在改它们不会影响正在运行的信，接进 M1 后改完第二天生效。

---

## 14 实施记录（按批次）

每批次只做一件事，配一条离线自检命令；跑通再进下一批。

### 14.0 开发环境（2026-10-25 打通）

之前所有代码都是"写完不能跑"的状态：本机没装 Python，`python` 只指向 Microsoft Store 的占位别名；DSH 的 Windows 沙箱又用受限令牌启动 PowerShell，.NET 运行时在受限令牌下初始化失败，退出码 `0xC0000142`。两件事一起解决：

| 项 | 结果 |
| --- | --- |
| 沙箱 | 会话权限切到 `danger-full-access` 后 `pwsh` 正常（`dsh-pwsh-sandbox/lib/index.js:154` 在 full-access 分支直接走本地执行器，不套受限令牌） |
| Python | 装 `Python 3.11.9`（per-user，`%LOCALAPPDATA%\Programs\Python\Python311`） |
| 依赖 | 项目内 `.venv`，用清华镜像装 `requirements.txt`（默认 PyPI 源在本机不可用，会挂住） |

**今后每个批次的验收命令**（先设 UTF-8，否则控制台中文是乱码）：

```powershell
$env:PYTHONIOENCODING='utf-8'; [Console]::OutputEncoding=[System.Text.Encoding]::UTF8
.\.venv\Scripts\python.exe -m arxiv_digest.selfcheck
```

### 14.1 A 档第 1 批次：LLM 分批容错（2026-10-25）

**问题**：`generate_digest_content` 把 14 篇塞进一次 prompt，拿回一段文本后单次 `json.loads`。三个失效模式全都会让整封邮件退化成英文原文：

1. 输出超长被截断 → JSON 不完整；
2. 某些兼容网关无视 `response_format`，返回 ```json 围栏或前后带说明文字；
3. 一次 429/超时 → 全部 14 篇一起降级。

**改法**（`arxiv_digest/llm.py`）：

| 机制 | 实现 | 换来的性质 |
| --- | --- | --- |
| 分批 | `_chunk(llm_papers, batch_size)`，默认 5（`LLM_BATCH_SIZE`） | 单批失败只污染那一批，其余篇目中文摘要照常 |
| 容错解析 | `_parse_json_object()`：去 Markdown 围栏 → 截取首尾 `{...}` → 依次尝试；都失败才抛 `ValueError` | 围栏/闲聊前缀不再导致全量退化 |
| 省输出 token | 只有第一批要 `overall_summary`，后续批次只返回 `paper_summaries` | 输出更短 → 截断概率进一步下降 |
| 逐批降级 | 每批 `try/except` 独立处理，失败计入 `failed_batches` | 失败可观测（`LOGGER.warning`），不再静默 |
| 尾部提示 | 有批次失败时总览末尾追加"本次有 N 篇未能生成中文摘要" | 读者知道自己看到的是降级结果，而不是以为模型偷懒 |

**新增** `arxiv_digest/selfcheck.py`：一条命令 `python -m arxiv_digest.selfcheck` 跑 7 项离线检查（导入链、默认配置、JSON 容错解析、分批切分、无 key 降级、日历读取、模板渲染），不需要联网/API key/发信；加 `--write-preview preview.html` 可顺手写出一份 HTML 看版式。

**改动清单**

| 文件 | 改动 |
| --- | --- |
| `arxiv_digest/llm.py` | 重写为分批 + 容错解析 + 逐批降级（123 → 约 250 行） |
| `arxiv_digest/config.py` | 新增 `llm_batch_size`（`LLM_BATCH_SIZE`，默认 5） |
| `arxiv_digest/pipeline.py` | 透传 `batch_size=config.llm_batch_size` |
| `arxiv_digest/selfcheck.py` | 新增，离线自检 |
| `.env.example` | 补 `LLM_BATCH_SIZE`、`DEEP_PAPERS`、`BRIEF_PAPERS`、`CALENDAR_*`（之前只在文档里） |
| `README.md` | 变量表更新（`MAX_PAPERS` 14、新增 6 个变量）+ 本地运行加"先跑自检" |

**成本影响**：批次数从 1 变 3（14 篇 / 5），system prompt 重复 3 次，单日成本增加不到 $0.01。

**已知未做**：仍是「一次总结 + 每篇一段评论」的单阶段，没有事实抽取层，因此无法核对评论里的数字是否来自原文——这是 M1 第 2 批次（两阶段 extract/assess）要解决的，不是本批次的范围。

### 14.2 A 档第 2 批次：把画像与领域包读进代码（2026-10-25）

上一批是"抗故障"，这一批是"让配置真的生效"——§13 那五个文件此前只是文档，代码完全没读过。

**新增两个模块，只做读取与校验，不碰 LLM**

| 模块 | 职责 | 关键设计 |
| --- | --- | --- |
| `arxiv_digest/profile.py` | `load_profile()` → `Profile`；`render_prompt_block()` 渲染成 prompt 段落 | 所有字段有默认值，读文件失败退化 `empty_profile()` + 一条 warning，**不抛异常**；`Profile` 不含排版参数（`deep_papers` 这类归渲染层，模型不该决定信的长短） |
| `arxiv_digest/packs.py` | `load_packs()` → `tuple[Pack, ...]`；`dispatch()`；`select_packs()`；`collect_categories()` / `collect_sources()` | 分派是**可解释的三级强度**：主分类命中(3) > 包内任一分类(2) > 前缀命中(1)；都不中才落到 `generic` |

**两个必须记住的坑（都是这次真跑出来的）**

1. `Profile` 里不能有叫 `field` 的属性——会和 `dataclasses.field()` 撞名，类体直接 `TypeError: 'str' object is not callable`。改名为 `discipline`。
2. `config/profile.yaml` 的 `relevance_kind` 是**顶层键**，不是 `analysis` 的子键。解析器要同时兼容两种位置，否则静默得到空元组，直到 Stage B 才发现没有合法取值。

**分派规则实测**

| 论文分类 | 分派到 | 说明 |
| --- | --- | --- |
| `["astro-ph.CO", "astro-ph.GA"]` | `galaxy_cosmology` | 主分类（`astro-ph.CO`）在前 |
| `["astro-ph.GA", "astro-ph.CO"]` | `galaxy_formation` | 同一对分类，只是主次相反 → 结果不同 |
| `["hep-th"]` | `generic`（capability: low） | 未归类论文走兜底，邮件里标注"通用判断" |

这两行是刻意的设计：**它不猜"哪个领域更重要"，只尊重论文自己的主分类**。

**自检新增 2 项**（合计 10 项）：`读者画像`（角色/5 问/7 字段/6 相关类型 + 渲染块含问题清单）、`领域包分派`（三级命中 + FIELDS 勾选 + 未知 id 回退 + 分类并集）。

**已知未做**：这两个模块**还没有接进 pipeline**——现在的邮件仍然走旧路径。接线（`ARXIV_CATEGORIES` 由包的并集生成、Stage A/B 用画像渲染 prompt）是第 3 批次，也是 M1 真正见效的那一步。

### 14.3 第 3 批次：速览双语 + 两阶段分析落地（2026-10-25）

**需求（m00594）**：即使速览也要中英摘要并排。

#### 14.3.1 速览双语

| 位置 | 改动 |
| --- | --- |
| `emailer.py` | 新增 `_en_snippet(text, limit=170)`：按英文句界（`. ` / `; ` / `? ` / `! `）截断并补 `…`；速览行上下文从 `brief_snippet` 拆成 `brief_zh` + `brief_en` |
| `digest_email.html` | 速览行改为两行：中文 13px `#2c3e50`，英文 12px `#7f8c8d`；精读卡的英文摘要从 `<details>` 折叠改成**直接展开**——Gmail/Outlook 会把 `<details>` 整段吞掉，等于没写 |

英文取的是 arXiv 摘要原句（不翻译、不再调一次模型），所以它同时是一个**核对锚点**：中文摘要跑偏时，读者一眼能对照原文。

#### 14.3.2 两阶段分析

新增 [arxiv_digest/analysis.py](../arxiv_digest/analysis.py)，`PROMPT_VERSION = "a1-b1"`：

| 阶段 | 函数 | 输入 | 输出 |
| --- | --- | --- | --- |
| A 抽事实 | `extract_facts()` | 标题 + 分类 + 摘要（截 2200 字） | `dict[str, Fact]`，六字段各带 `value` + 英文 `quote`；原文没写 → `null` |
| B 写评论 | `assess()` | 事实 + `Profile.render_prompt_block()` + `Pack.render_prompt_block()` | `Analysis`：relevance / kind / 七问 + confidence + evidence + basis |

`models.py` 新增 `Fact`（`value`/`quote`，`is_missing`）与 `Analysis`（`.fields` 返回七问的有序三元组，模板直接遍历），`Paper` 增加 `title_zh` 与 `analysis: Analysis | None`。

**为什么拆两步**（写进模块 docstring 的三条）：可核对（每条事实必须挂原文）、可降级（摘要没写就只能写"摘要未提"，模型没机会编 120,000 个星系）、可缓存（阶段 A 只依赖论文与提示词版本）。

**接线**：`pipeline._maybe_analyze()`，只在 `ANALYSIS_ENABLED=true` 且拿到 client 时跑；**只分析前 `DEEP_PAPERS` 篇**（速览不花这个钱）；任何异常 `LOGGER.warning` 后返回原论文——**分析失败不能导致不发信**。`_write_digest_json()` 在 `DIGEST_JSON_PATH` 非空时落盘。

**开关默认 `false`**：这条链路每小时都在烧钱，先把"能不能用"验证完再默认开，而不是默认开再让用户去找怎么关。

#### 14.3.3 自检扩到 11 项

新增 `两阶段分析`：假客户端（`_FakeClient` 按顺序返回预设 JSON）跑通 extract→assess，验证 ① null 事实降级成"摘要未提" ② 评估 prompt 里真的带上了画像问题清单与领域包判据 ③ 客户端抛异常时整封信不跟着炸。`邮件渲染` 增加：速览必须同时出现英文原句与中文摘要、带 `analysis` 的卡片必须渲染七个标签。

**一个真跑出来的认知**：阶段 B 会**重写** `value`（不是照抄阶段 A），所以断言不能写"以某前缀开头"，只能查关键内容是否在——这条也解释了为什么 `evidence` 必须保留原文 quote：它是唯一能追回阶段 A 的线索。

**验证**：`selfcheck` 11/11；`preview.html` 41 KB，前两篇带七问七答、其余为旧中文摘要（这样一眼能对比两种版式）；`compileall` 干净。








## §14.4 第 4 批次：全文 PDF + 双语摘要 + 可回放预览

**用户要求**（m00706）："卡片里面中英文摘要都要有，然后精读文章一定是根据整篇文章 PDF 生成，先生成一个 HTML 预览"。

### 做了什么

| 文件 | 改动 |
| --- | --- |
| `arxiv_digest/fulltext.py` | **新增**。`download_pdf()` / `extract_pdf_text()`（pypdf）/ `clean_text()`（修 `sam-\nple` 断行、去孤立页码）/ `cut_references()`（砍参考文献，只在命中位置 >30% 时才砍）/ `condense()`（开头 45% + 含关键词句窗口）/ `fetch_fulltext()`（任何一步失败返回 `None`）/ `pdf_url_for()` |
| `arxiv_digest/analysis.py` | `build_extract_prompt(paper, *, fulltext=None)` 支持全文；`MISSING_FULLTEXT="全文未提"` 与 `MISSING="摘要未提"` 分开；`assess(..., basis=)`、`analyze_paper(..., fulltext=)`、`analyze_papers(..., fulltexts=)` |
| `arxiv_digest/config.py` | 新增 `fulltext_enabled` / `fulltext_max_chars=40000` / `fulltext_max_pages=40` / `fulltext_timeout=120` |
| `arxiv_digest/pipeline.py` | 新增 `_fetch_fulltexts()`：只给前 `DEEP_PAPERS` 篇抓全文，单篇失败回退摘要并记日志 |
| `arxiv_digest/templates/digest_email.html` | 精读卡新增"中文摘要"块（有 `analysis` 时）；徽标改为显式 **全文**（绿）/ **仅摘要**（橙） |
| `scripts/build_preview.py` | **新增**。`--discover N` 抓真实论文生成 replay 骨架；默认读取 `config/preview_replay.json` 渲染 `preview.html` |
| `arxiv_digest/arxiv_client.py` | **修本地真 bug**：`urlopen(..., context=_ssl_context())`，用 certifi 证书链。此前本机跑必报 `CERTIFICATE_VERIFY_FAILED`（GitHub Actions 有系统证书，所以 CI 一直没暴露） |
| `requirements.txt` / `pyproject.toml` | 加 `pypdf==6.21.2` / `pypdf>=6.0` |
| `.env.example` / `README.md` | `FULLTEXT_*` 四行 |

### 三个设计选择

1. **为什么先砍参考文献**：arXiv PDF 里参考文献常占 1/3 篇幅，且几乎不含七问需要的信息。砍掉后同样的 40k 预算，正文密度高得多。
2. **为什么 `condense` 保留"开头 + 关键词句"而不是均匀采样**：开头是摘要+引言（给语境），关键词句覆盖样本/系统误差（给判断依据）。均匀采样会把预算摊到公式与图注上。
3. **为什么全文缺失要显式标"仅摘要"**：读者必须知道这张卡值不值得信。同样七问，全文支撑和摘要支撑的可信度差一个量级，藏在数据里就是欺骗。

### 预览样张的诚实说明

无 API key，`preview.html` 里的分析内容由**人工按同一套 prompt 规则产出**（`config/preview_replay.json` 录制），不是模型实时输出。
两篇（2610.31660、2610.31545）基于**真实 PDF 全文**（脚本现场下载抽取，分别为 26,267 / 33,033 字符），另两篇故意走"仅摘要"，正好展示降级形态。

**验证**：`selfcheck` 12/12（新增"全文处理"：断行修复 / 砍参考文献 / 预算裁剪 / PDF 链接推导）；`compileall` 干净；`preview.html` 49 KB，中文摘要 ×4、English abstract ×4、全文徽标 ×3、仅摘要 ×2。

## §14.5 第 5 批次：精读卡从"一张表"变成"一份简报"

**用户要求**（m00856）："精读的体现还不够明显，只停留在七问七答……我希望将七问七答定义为一个结构化的呈现，但精读部分内容还可以更丰富一些……还有英文摘要可以折叠，点击才展开。"

### 卡片现在的结构（10 个分区）

| 顺序 | 分区 | 谁产出 | 为什么放在这个位置 |
| --- | --- | --- | --- |
| 1 | 星级 / 相关度 / 分数 | 推荐器 | 决定"要不要往下看" |
| 2 | **一句话结论 verdict** + **阅读建议**（今天读 / 看方法即可 / 留档） | Stage B | 只读这一句也能决定要不要点开；把"读不读"的判断前置，而不是让人读完七问再自己掂量 |
| 3 | **方法链 chain**（4-6 个标签，箭头串联） | Stage B | 3 秒看懂骨架：数据→切选→误差→估计量→结论。七问是纵向细节，方法链是横向骨架 |
| 4 | 七问七答（数据/处理/系统误差/方法/结果/作者局限/对你有用） | Stage B | **保留为"结构化呈现"**，不再承担"丰富性" |
| 5 | **关键数字 numbers**（3-6 条，每条带原文引文） | Stage A | 做群团宇宙学的人最常问"样本多少、误差多大"；单独成表，还能一句句回查原文 |
| 6 | **图表线索 figures**（PDF 里抽的图注） | 正则（零成本） | 图注常一句话写完结果；不用点开 PDF 就知道有哪些图 |
| 7 | **今天能做什么 actions**（3 条动宾句） | Stage B | 把"对你有用"从形容词变成动作；与七问的 `for_you` 分工：`for_you` 说"能借什么"，`actions` 说"今天做什么" |
| 8 | **红旗反查 red_flags**（领域包清单逐条对照：已讨论/部分涉及/未提及） | Stage B + 领域包 | 从"作者说有什么局限"升级为"本领域的坑，这篇踩了没踩"。这是读者自己列不出来的东西 |
| 9 | **术语小抄 concepts** | Stage A 抽 + Stage B 净 | 生僻词一句话解释；只解释画像词表外的，不解释教科书概念 |
| 10 | 中文摘要 → **English abstract（折叠）** | 模型 / arXiv 原文 | 中文给结论，英文给核对锚点 |

### 三个实现细节

1. **英文摘要用原生 `<details>`**：支持它的客户端（Apple Mail、iOS、Thunderbird）默认收起；不支持的（Gmail、Outlook）**会把整段直接展开**——这是可接受的降级，而不是"吞掉"。之前删掉 `<details>` 是因为当时里面装的是正文；现在装的是辅助材料，两种表现都能接受。
2. **图注不花模型的钱**：`extract_figure_captions()` 纯正则，先压平换行再匹配（PDF 里 "Figure 3:" 常独占一行），命中后在句界截断。`_INTEREST_WORDS` 里加了 `figure` / `fig.` / `caption`，让图注能活过 `condense()` 的裁剪。
3. **红旗反查的清单来自领域包**：`config/packs/*.yaml` 的 `red_flags` 已经写好了本领域的坑（未标定质量-观测关系就做宇宙学、单探针强结论、只报统计误差、mock 不含选择函数……），Stage B 逐条判 discussed/partial/missing。**换领域包就换一套审查标准**，无需改代码。

### 成本与契约变化

- `PROMPT_VERSION` 从 `a1-b1` 升到 `a2-b2`（缓存 key 变了，老缓存作废）。
- Stage A 新增 `numbers` / `concepts` 两个数组；Stage B 新增 `verdict` / `read_advice` / `read_reason` / `chain` / `actions` / `red_flags`。
- 输出 token 约为原来的 1.8 倍（一篇精读约 900 → 1600 token）。按 4 篇 × 2 次调用算，每天多约 $0.01。
- `paper.analysis.numbers` 与 `figures` 是**由代码填充**的，不来自 Stage B 的 JSON——这样型号不同的模型也不会把这些字段丢掉。

### 验证

`selfcheck` 12/12（两阶段分析项新增 8 条断言：verdict / read_advice / chain 长度 / numbers 条数与标签 / actions / red_flag tone 归一 / concepts 术语）。`compileall` 干净。`preview.html` 83 KB：4 篇精读全部带方法链、关键数字、今天能做什么、红旗反查、术语小抄；2 篇基于真实 PDF 全文，自动抓出 2 条图注。

**顺带修的健壮性问题**：`download_pdf` 改为 2 次尝试 + 退避。本轮真跑时 2610.31660 第一次 `WinError 10060` 超时，重试后拿到 27,411 字符——单次失败就整篇退回摘要在真实网络下太脆。

## §14.6 第 6 批次：多源接入、跨天状态与初稿交付（2026-10-25）

用户 m01008："请你完成设计方案中的各个规划，给出一份初稿"。这一批次把方案里剩下的可执行部分落地，并给出 v0.2 初稿。

### 新增能力

| 能力 | 文件 | 说明 |
| --- | --- | --- |
| 条目公共契约 | `arxiv_digest/sources/base.py` | `Item(id, kind, title, url, source, published, text, tags, meta)`、`SourceResult`、`plain_text()`、`dedupe()` |
| RSS 抓取 | `arxiv_digest/sources/rss.py` | 标准库 urllib + xml.etree，同时支持 RSS 2.0 与 Atom |
| Indico 会议报告 | `arxiv_digest/sources/indico.py` | `export_url()` 把 `/event/1234` 推成 `/export/event/1234.json?detail=contributions` |
| 收集入口 | `arxiv_digest/sources/__init__.py` | `load_sources()` / `collect_sources()` / `merge_items()`，支持 `fields` 与 `enabled` |
| 跨天状态 | `arxiv_digest/state.py` | `State(version, seen, daily)`、`filter_unseen()`、`mark_seen()`、`record_day()`、`weekly_trend()`、`prune()` |
| 源配置 | `config/sources.yaml` | 4 个 RSS + 1 个 Indico 示例（默认关） |
| 邮件区块 | `templates/digest_email.html` | 「世界在动」（二手解读 / 会议报告）、「本周投稿量变化」、页脚信息源健康 |
| CI | `.github/workflows/daily-arxiv-digest.yml` | `selfcheck` job 在前，`send-digest` 在后；`persist state` 步骤回写 `state.json` |

### 四个设计取舍

1. **多源复用 arXiv 的"条目"抽象，而不是另建一条链。** `Item` 与 `Paper` 不必统一——论文要评分、要全文、要七问七答；二手解读只要标题、来源、链接、一句话。硬塞进 `Paper` 会逼着给新闻稿编造 `categories` 和 `score`。两者在渲染层汇合即可。
2. **状态文件是"唯一必须回写"的东西。** Actions 每次都是干净文件系统：没有 `state.json` 就会天天推同一批二手解读，也没有周环比。所以 `state.json` 默认写进仓库根目录，由 workflow 每天 commit 回去——这一步顺带解决"公共仓库 60 天无活动停用定时任务"。
3. **趋势只报"投稿量"，不报"热度"。** 分类计数是可核对的事实（我抓了 N 篇 astro-ph.CO）；"本周宇宙学变热了"不是。前者能每天跑，后者需要跨越论文与新闻的语义聚类，属 M3 后半段。
4. **预览不写状态。** `run_pipeline(config, persist_state=False)`：`scripts/preview_digest.py` 用它，否则"看一眼"就把条目算成已推送，第二天真邮件里就少了。

### 已验证

- `selfcheck` **14/14**（新增「多源抓取」：HTML 反转义、长文截断、feed 配置、Indico 导出地址推导、同标题去重保留长正文、失败源降级；新增「状态与趋势」：已读过滤、周环比 2→5 = +150% ↑、过期清理）。
- 真实抓取：AAS Nova 4 条、ESO 3 条，Astrobites `403`（数据中心 IP 被挡，Actions 上正常）。`preview.html` 90,423 字节，含「世界在动」与页脚源健康。
- `compileall` 干净；`config/preview_replay.json` 新增 `world` 区块，可离线重渲。

### 初稿的边界（说清楚比藏着好）

| 里程碑 | 状态 | 说明 |
| --- | --- | --- |
| M1 编辑能力 + 领域包 | ✅ | 画像、两阶段、全文、10 分区卡片、`digest.json` |
| M2a 大动态（日历） | ✅ | `events.yaml` / `conferences.yaml` + 倒计时 |
| M2b 多源 | ✅ | RSS + Indico；期刊 RSS、修订版追踪未做 |
| M3 全局视角 | ✅ | 投稿量周环比 + 主题聚类（本周脉搏）；引用信号未做 |
| M4 闭环 | ⬜ | 反馈按钮与权重学习未做 |
| M5 门面 | ◐ | README 快速开始、CHANGELOG、LICENSE、CI 已完成；网页配置向导未做 |

**下一步优先级建议**：先让用户连着跑两周（验证"这封邮件值得读"），再决定是补 M4 反馈还是 M3 聚类。工程上最该补的是**20 篇人工标注的小评测集**——评论质量的退化是静默的，没有评测集就发现不了。

---

## 14.7 第 7 批次：本周脉搏（`pulse`）

用户的原话是「当前实现的效果只是罗列当前的会议日期……我还想要知道当前天文学界有什么重大进展，或者大家正在关注和讨论什么问题」。罗列日程是**记录**，判断"什么在动"是**编辑**，两者之间差的是对材料的阅读与取舍。

### 7.1 做法：先把能数的事数清楚，再让模型在证据上说话

| 步骤 | 做什么 | 为什么 |
| --- | --- | --- |
| ① 摊平材料 | 论文 + 二手解读 + 会议报告统一成 `Material(id, kind, title, url, source, date, text)` | 三条分区的材料要能放进同一个池子里比较，否则"多源"永远只是三个独立列表 |
| ② 数主题 | 领域包里每个主题带一组 aliases，按标题权重 2、正文权重 1 计数 | 主题来自 `packs/*.yaml`，换领域包=换观察维度，不用改代码 |
| ③ 找共现 | 同一主题被 ≥2 个"源"提到 → `is_shared` | 只被 arXiv 提到是"新投稿集中"，被二手解读也提到才算"大家在说" |
| ④ 找争论 | 材料里出现 tension / discrepancy / inconsistent / puzzle / challenge / anomalous / at odds / under-predicted 等措辞 → 记入 `tension_items` | 争论在摘要的措辞里是可见的，不需要模型先去"理解" |
| ⑤ 写判断 | 模型只拿到材料块 + 主题表，输出 `headline / threads[] / quiet`，每条判断必须挂 `evidence` | 没挂证据的判断在 `coerce_threads` 里被**直接丢弃**，不是打折 |
| ⑥ 兜底 | 无 key 或全失败 → `fallback_pulse` 只说"哪个主题被提到几次" | 宁可只报数，也不让模型在没有材料时编故事 |

状态分三档：`new`（新结果）、`debate`（有争论）、`unsettled`（尚无定论）。模型只能在材料支持时用后两档，邮件里用绿/橙/灰三种胶囊标出来。

### 7.2 这周的真实现场（今天 40 篇论文 + 7 条二手）

```
DESI        19 次 / 5 条材料
暗能量       14 次 / 5 条
Rubin/LSST  13 次 / 4 条
环境依赖      8 次 / 5 条    ← 这里面有噪声
JWST         8 次 / 3 条
BAO/RSD      6 次 / 2 条
```

它确实抓到了一件**能说的事**：本周有 5 篇新投稿在拆同一个信号——`2610.31044` 检验 CPL 参数化本身是否自洽、`2610.30784` 指出暗能量与暗物质相互作用也能造出等效的 phantom divide 穿越、`2610.31660` 干脆把 `r_d` 放开把问题改写成 ΛCDM 一致性检验。这不是按关键词堆出来的清单，是"同一周、同一个问题、三种打法"。

### 7.3 三个真跑出来的坑

1. **源身份不能拿分类名充当**。第一版 `Material.source` 用的是 `astro-ph.CO, astro-ph.GA`，结果每篇论文都是"独立源"，`is_shared` 永远为真——跨源共现这个信号直接作废。改为论文统一 `source="arXiv"`。
2. **短别名必须按词边界匹配**。`desi` 命中了 `design`，DESI 的提及数虚高到 21；加 `(?<![A-Za-z0-9])…(?![A-Za-z0-9])` 后回落到 19。同理要防 `bao` 撞 `baobab`、`s8` 撞 `s88`。
3. **通用词当主题就是噪声**。`environment` 让"环境依赖"拿到 8 次提及、5 条材料，但其中 3 条是"大麦哲伦云里的超气泡"这种环境。这类词要么换成更专的别名，要么接受它只是弱信号——**不要因为计数高就把它写成头条**。

### 7.4 这周暴露的真问题：二手源对宇宙学是空转

7 条二手解读全是潮汐瓦解事件、黑洞占据率、系外卫星、邻近星团——**一条宇宙学都没有**，于是 8 个主题全部"单源"。这不是代码 bug，是信息源结构问题：AAS Nova 与 ESO 的产出分布与你的方向不重合。

这带来一个诚实的结论：**"大家在讨论什么"目前只能由 arXiv 侧支撑**，二手源在宇宙学上是低频源。要让这一块真正成立，需要补的是**同领域的高频二手源**（CosmoCoffee 讨论串、Twitter/Bluesky 上的宇宙学圈子、Euclid/DESI/Rubin 合作组公告），而不是把现有两个 RSS 的 `max_items` 调大。

### 7.5 成本与降级

- 主题计数、张力识别、图注抽取全部是本地正则，**零成本**。
- 只有最后的 `compose_pulse` 调一次模型：输入 30 条材料摘要块（约 6k token），输出 3-4 条判断（约 1.2k token），每天约 **$0.01**。
- 无 key / 调用失败 → `fallback_pulse`，邮件里照常出现「本周脉搏」但只报主题统计，页脚标注"未接入模型，只有主题统计"。

### 7.6 验证

`selfcheck` 增至 **15 项**，新增 `_check_pulse()`：材料摊平、主题排序、跨源识别、张力识别、无证据判断被丢弃、状态标签映射、证据转链接、兜底可用、领域包带 `pulse.topics`。`preview.html` 从 90 KB 增至 **100,156 字节**，「本周脉搏」区块含 headline + 3 条判断 + 主题分布条形图 + 页脚材料数。

---

## 14.8 第 8 批次：邻域雷达 + 二手翻译 + 反馈闭环 + 评测集（2026-10-26）

这一批同时回应了两件事：你提的「别只看自己领域」和「二手解读要带中文」，以及我上一轮建议的三个改进。

### 14.8.1 邻域雷达：只挑不读

| 设计 | 为什么这么做 |
|---|---|
| 每个方向只给 1 条 | 8 个方向 × 3 条 = 24 条，直接吃掉 20 分钟预算；雷达是"瞥一眼"，不是"读" |
| 先按标题信号词排序（first detection / first light / unprecedented / new class / tension） | 这些词是"这一条可能改变什么"的代理，比按时间排序有信息量 |
| 只显示一句 `why` | 不写摘要——写了就会有人去读，成本立刻回到精读 |
| 页脚给"本周各方向投稿量" | 让你知道"这个方向这周很热闹"和"我给它挑了一条"是两回事 |
| 同一条论文只上一次版面 | 分类会重叠（astro-ph.EP 同时属于"系外行星"和"太阳系"），原来会重复出现两次，等于骗读者 |
| 配置在 `config/radar.yaml`，关掉就是删一行 | 不想要的方向不必忍 |

实测（2026-10-22 前后真实抓取，5 个方向 100 篇）：

| 方向 | 分类 | 挑中的条目 |
|---|---|---|
| 系外行星 | astro-ph.EP | OGLE-2013-BLG-1125/MOA-2013-BLG-584: Discovery of a gas giant orbiting an M dwarf |
| 恒星与银河系 | astro-ph.SR | Unexpected Local Enhancement of Chromospheric Moreton Waves on the Sun |
| 高能天体 | astro-ph.HE | Weak Radio and Bright Early Optical Emission in EP260321a… |
| 仪器与方法 | astro-ph.IM | A cosmic filament catalog for COSMOS-Web Data Release 1 across 0.5 < z < 3.5 |
| 太阳系与小天体 | astro-ph.EP | （本周条目已被"系外行星"占用，不重复上版面） |

**两个真 bug 都是实跑抓出来的**：① 原实现按论文的 `categories[0]` 显示分类，于是"仪器与方法"那条显示成 astro-ph.CO（它确实主分类是 CO）——现在显示**匹配到的那个分类**；② 同一篇论文在"系外行星"和"太阳系"各出现一次——现在有全局 `used_ids` 去重，宁可少一条也不重复。

### 14.8.2 二手解读带中文

`translate_items()` 一次调用翻 6-12 条，规则写死在提示词里：只译不改、数字照抄、术语保留英文、正文不足以支撑一句话就只译标题。失败的条目原样返回英文——**不让整段消失**。模板里中文在前、英文原标题以灰色小字附后（核对锚点）。

### 14.8.3 反馈闭环：只学"长什么样"，不改关键词

邮件里每篇精读卡后面有三个链接：有用 / 一般 / 无关，指向 `FEEDBACK_URL`（Google Form、Cloudflare Worker 或自己的脚本都行）。落盘格式：

```json
{"version": 1, "votes": {"2610.31660v1": "up"},
 "history": {"2610.31660v1": {"categories": ["astro-ph.CO"], "authors": ["Vitorelli"]}}}
```

排序时只做两件事：同类分类 +0.6×权重、同作者 +0.35×权重，总加成上下限 ±1.8 分，且**只在候选排序阶段生效**，不碰关键词、不碰画像。

> 故意不做"关键词自动改写"——那是把人的偏好悄悄改掉，比不学习更糟。

命令行：`python scripts/feedback.py record --id … --vote up` / `stats` / `import votes.csv`。

### 14.8.4 20 篇评测集

`python scripts/eval_analysis.py build --count 20` 抓最近 20 篇 astro-ph.CO/GA 的摘要，生成 `config/eval_set.json`；跑过一次真实摘要后 `check` 出四个数：

| 指标 | 含义 | 达标线 |
|---|---|---|
| 覆盖论文 | 评测集里有几篇真的进了精读 | 只统计，不设线（受 `DEEP_PAPERS` 限制） |
| **引文保真** | `quote` 能在原文里原样找到的比例 | **≥90%，否则评论内容不可信** |
| 字段填充 | 七问里非"摘要未提/全文未提"的比例 | 观察趋势 |
| 关键数字召回 | 摘要里预置的数被写进「关键数字」的比例 | 观察趋势 |

实测生成：20 篇里 13 篇的摘要能抽出可核验的数（其余是定性摘要）。这一步的意义不是打分，而是**把"感觉写得好"变成一条能被证伪的线**——引文保真跌破 90% 时，先别信评论。

### 14.8.5 验证

`selfcheck` 从 15 项增至 **18 项**（新增 `_check_radar` / `_check_translate` / `_check_feedback`），全绿；`compileall` 干净；`preview.html` 从 100 KB 增至 **115,591 字节**，新增「邻域雷达」区块（4 条）、6 条二手中文、4 篇精读各 3 个反馈链接（×3 = 12 个 `digest-feedback` 链接）。

### 14.8.6 已知未做

- 反馈链接需要你自己有一个能收 GET 的端点；没有就整段不显示（`FEEDBACK_URL` 为空即隐藏）。
- 雷达的 `why` 在无模型时是模板句（"信号最强的条目之一"），接上模型后换成判断句。
- 20 篇是"可核验"的骨架，不是人工标注的质量金标；真正的金标要你读 20 篇后自己标"这条说得对不对"。
---

## 14.9 v0.5：把「别的方向在动什么」做成可折叠的雷达（2026-10-25）

用户六点要求对应六处改动。核心判断：**信息不是越多越好，而是"默认少、想要时能展开"**——每多一条默认可见的内容，都会挤掉精读的注意力。

### 14.9.1 六点怎么落的

| 要求 | 落法 | 取舍 |
|---|---|---|
| 每方向 2-3 条，只展示 1 条 | `RadarBlock(headline, rest, total)`，`rest` 进 `<details>` | 书签式浏览：先看一句话，感兴趣再点开 |
| 每条 100 字左右解读 | Stage 输出 `summary`（80-110 字）；无 key 时 `_rule_summary` 兜底 | 兜底句明确是"信号词"而非判断，不装懂 |
| 速览也给中英摘要并折叠 | `<details><summary>展开中英文摘要</summary>` + 英文原句 + 中文 | 速览占 10 条，全展开会淹掉精读 |
| 世界在动更多条目 | 每源 `max_items` 提到 5-6，新增 Phys.org / Universe Today | ESO feed 陈旧（7-8 月），保留但排在后面 |
| 三条二手源都要、并标注来源 | 每条前置色块 `AAS Nova #2c5f8a / Astrobites #b9770e / ESO #1e8449 / Phys.org / UT` | 来源必须一眼可辨，否则读者无法判断可信度 |
| 排版美观可读 | 正文行高 1.72、分区标题 13px/700/字距 0.8、卡片圆角 12px、外部阴影更柔 | 邮件客户端只用内联样式与 table，能用 `<details>` 但不用 flex/grid |

### 14.9.2 两个实现细节

- **折叠不损失信息**：`<details>` 在 Gmail/Outlook 会被剥掉标签、内容直接可见；在 Apple Mail / iOS / Thunderbird 正常折叠。两种都是可接受结果——这就是选它而不是自制 JS 的原因（邮件里 JS 不执行）。
- **解读必须有来源分工**：模型写的 `summary` 是判断句；规则兜底句只能是"这周信号最强的一条"这类**可核验的描述**。两者在渲染上没有区别，但在数据里有 `basis` 字段，回看时能分清哪条是机器说的、哪条是规则说的。

### 14.9.3 实跑数据（2026-10-25）

- 雷达 5 个方向共 15 条：系外行星 8 篇、恒星与银河系 16、高能天体 25、仪器与方法 15、太阳系与小天体 9（这是本周该分类的总投稿数，不是上版条数）。
- 二手 17 条（AAS Nova 6、ESO 3、Phys.org 5、Universe Today 3）；Astrobites 本机 403（数据中心 IP），Actions 上正常。
- `selfcheck` 18/18；`compileall` 干净；`preview.html` **142,312 字节**，精读 4 + 速览 10，10 个折叠摘要块、5 个雷达折叠块、17 条二手。

### 14.9.4 已知未做

- 「本周投稿量变化」在预览里仍是空的：它需要连续两天的 `state.json`，预览不写状态（`persist_state=False`），这是设计而不是缺陷。
- Bluesky 社交信号没接：它是唯一能提前于 arXiv 看到"大家在聊什么"的源，但也最容易变成噪音，建议单独一个开关、只做参考分。
- 雷达的 100 字解读在无 API key 时是规则句；接上模型后同一字段换成判断句，版式不变。

## 14.10 v0.6.0：机构源分频抓取 + 宋体/Times + 公式排版

用户原话（m01887）："可以接入 NASA 官网信息和各个巡天项目、望远镜项目网站，然后对于 NASA 这种更新比较频繁的可以每天抓取，对于巡天项目和望远镜网站更新不频繁的，一周抓取一次……还有，能否做到中文字体为宋，英文字体为 time new roman，公式符号等也能够完整呈现，比如 latex 格式渲染。"

### 14.10.1 源的探测结果（2026-10-25 实测）

先探再写，不做"配置里先加上、反正失败会降级"这种事——那只会让日志变脏。

| 站点 | 结果 |
| --- | --- |
| `nasa.gov/news-release/feed/` | ✅ 10 条（需 UA；混有 APOD，用 `exclude: ["APOD:"]` 挡掉） |
| `science.nasa.gov/feed/` | ❌ 第 8 行有非法字符，`ParseError: not well-formed` |
| `esa.int/rssfeed/Our_Activities/Space_Science` | ✅ 3 条 |
| `desi.lbl.gov/feed/` | ✅ 3 条（最新 2026-08-21，印证"一周抓一次就够"） |
| `keckobservatory.org/feed/` | ✅ 3 条 |
| `eso.org/public/news/feed/` | ✅ 3 条（内容陈旧，7–8 月） |
| hubblesite / webbtelescope / chandra / noirlab / skao / esahubble / stsci | ❌ 全 404 |
| rubinobservatory.org/news/rss | ❌ 403 |
| skyandtelescope.org/feed | ❌ 403 |

结论：**机构新闻只有 NASA、ESA 两家能稳定拿到**。其余望远镜/巡天项目的动态，靠它们在 arXiv 与会议上的产出更可靠——这也是为什么"邻域雷达"比"再找十个 RSS"更有用。

### 14.10.2 分频抓取：`cadence` + `state.fetched`

- `config/sources.yaml` 每个源加 `cadence: daily|weekly`（默认 `daily`）。
- `state.json` 增加 `fetched: {"nasa": "2026-10-25", ...}`，记录每个源上次抓取日。
- `state.should_fetch(cadence, last, today)`：`daily` 当天抓过就跳；`weekly` 距上次满 7 天才抓；日期读不出来时**当作没抓过**（宁可多抓，不要漏一周）。
- `state.due_ids(state, cadences, today)` 算出今天要跳过的 id；`sources.collect_sources(..., skip_ids=...)` 跳过；抓完 `mark_fetched`。
- 源 id 的推导规则必须与 fetcher 一致，所以 `source_ids()` 与 `fetch_feed`/`fetch_event` 用同一套 `id or name or url`，这是易错点，已写进 docstring。

**为什么不用 Actions 分两份 workflow 跑**：一份跑到一半失败，另一份不会自动补；把"该不该抓"放进同一份跑，判断只有一处，本地跑同样成立。

### 14.10.3 字体：宋体 + Times New Roman

```css
font-family: 'Times New Roman', Times, Georgia, 'Songti SC', 'SimSun', 'STSong', 'Noto Serif CJK SC', serif;
```

- 英文走 Times New Roman（macOS/Windows 都有）。
- 中文走宋体系：macOS 是 Songti SC，Windows 是 SimSun，Linux/Android 回退 Noto Serif CJK。
- **邮件客户端不保证字体**：Gmail 尊重 `font-family` 但 iOS Mail 可能替换，Outlook 桌面版最顽固。所以只改 `body` 的默认字体，各分区原有字号/颜色不动——字体退化时版式不塌。

### 14.10.4 公式：LaTeX → Unicode（`mathfmt.py`）

邮件不跑 JS，MathJax/KaTeX 用不了；公式转图片要外链或 CID，把信变重且易挂。所以做**尽力转换**：

| 输入 | 输出 |
| --- | --- |
| `$H_0 = 67.4 \pm 0.5$` | `H₀ = 67.4 ± 0.5` |
| `$10^{-3}$` | `10⁻³` |
| `$\Lambda$CDM` | `ΛCDM` |
| `$\sigma_8$` | `σ₈` |
| `$\gamma_{\rm cm}$` | `γ_cm`（`c`/`m` 没有 Unicode 下标，退回可读写法） |
| `$\frac{a}{b}$` | `a/b` |

实现要点（都是实测踩出来的）：

1. **命令名必须"已知优先"匹配**：否则 `$\Lambda$CDM` 会被 `\\[A-Za-z]+` 贪婪吃成 `LambdaCDM`。
2. **`_{\mathrm{200c}}` 要先变 `_{200c}` 再去壳**：顺序反了会散成 `_200c` 只吃到第一个字符。
3. **下标/上标只在全部字符可映射时才转**：`γ_cm` 里 `c`、`m` 没有 Unicode 下标，硬转会得到残字，不如保留 `_cm`。
4. 转换放在 **Jinja `finalize`**（`emailer._env`），不写进模型输出：同一份 `digest.json` 换成网页/终端输出时还能重新排版。
5. 认不出的命令去掉反斜杠保留名字——**绝不抛异常**，这封信不该因为一个公式挂掉。

### 14.10.5 验证

- `selfcheck` **19/19**（新增「公式排版」：6 个转换样例 + 模板 finalize 生效）。
- 实跑 9 个源：AAS Nova 6 / NASA 5 / Phys.org 5 / Universe Today 3 / ESA 3 / ESO 3 / DESI 3 / Keck 3；Astrobites 403（数据中心 IP）。
- 分频已验证：当日 `skip=['desi','esa','eso','keck']`，7 天后 `skip=[]`。
- `compileall` 干净；`preview.html` **142,076 字节**（脚本口径）。

### 14.10.6 已知未做

- 脉搏"更丰富"这一条本轮只做了调研，具体形态待定（候选：上周对比 / 活跃作者 / 只读一篇 / 跨领域共振）；其中"上周对比"必须往 `state` 加每日主题计数，属于格式变更，值得单独一批做。
- 公式只覆盖常见符号；矩阵、分段函数、`\begin{}` 环境会退化成纯文本。

---

## 14.11 v0.7：版式重做、摘要完整性与二手解读加长

### 14.11.1 改了什么

| 位置 | 之前 | 现在 |
| --- | --- | --- |
| 报头 | 深色渐变块 + 刊名 + 时间 | 暖纸底 + 刊名小字眉题 + 26px「今日简报」+ 一行量级摘要（精读/速览/二手/20 分钟） |
| 分区标题 | 13px 粗体 + 2px 黑色下划线 | 10.5px 英文眉题（READING / SKIM / THE FIELD）+ 16px 中文标题 + 1px 细线 |
| 配色 | 55 个色值，纯黑 `#1a1a2e`、亮橙 `#e67e22`、宝蓝 `#2c5f8a`、亮紫 `#8e44ad` | 一套语义色：纸 `#f3f0e9`、墨 `#1b2431`、正文 `#29323c`、次要 `#7b8794`、琥珀 `#b45309`、藏青 `#1f4e6b`、紫 `#6b4c9a`、绿 `#157a42`、红 `#b3261e` |
| 卡片 | 1px `#e8ecf1`、圆角 8 | 1px `#e6e1d7`、圆角 10、白底（与纸底拉出层次） |
| 正文 | 行高 1.72 | 行高 1.85（中文长段），标题 17.5px |

### 14.11.2 为什么这么改（不是审美偏好）

1. **长邮件的成本是扫描成本**：一天 14 篇论文 + 17 条二手，第一眼要能回答"今天有多少量、我该从哪开始"。报头那行「精读 4 · 速览 10 · 二手 17 · 近 1 天」就是干这个的（v0.12 起把"20 分钟"从报头拿掉，分钟数不再暗示内容被裁剪）。
2. **装饰性黑线抢视线**：原来每个分区标题下一条 2px 纯黑线，在小屏上和标题本身一样重，反而压住了内容。改成 1px 浅线 + 小字眉题，视线自然落到标题文字上。
3. **高饱和色在长文里是噪声**：亮橙的星级、宝蓝的分类章、亮紫的脉搏标题，各自都在喊"看我"。降饱和后强调色只剩一个（琥珀），读者的注意力才有主次。
4. **暖纸底比纯白耐读**：`#f3f0e9` 与墨色正文的对比度比纯白低一档，连读 20 分钟不刺眼；卡片用白底，反而让"每篇论文是一张独立的纸"这件事更清楚。

### 14.11.3 摘要完整性

**问题**：速览的折叠块里放的是 `brief_zh` / `brief_en`——它们是按 110 / 170 字截断的预览句，展开后看到的仍是被切断的半句话。

**改法**：折叠块改放 `chinese_summary`（完整中文）与 `summary`（arXiv 英文摘要原文全文），顺序中文在前、英文在后（英文是核对锚点，不是主文本）。截断句只保留在**列表里那行预览**——那是它的唯一用途。

### 14.11.4 二手解读加长

原来 `text_zh` 只有 40–60 字（"一句最关键的话"），读起来像标题的复述。现在要求 50–100 字，写清三件事：**它发现了什么 / 怎么做的 / 为什么值得知道**。`translate.py` 的提示词同步改成：

> 正文译成 50-100 字中文，写清三件事：它发现了什么、怎么做的、为什么值得知道；
> 如果正文不足以支撑 50 字，就只译标题，正文留空字符串——不要用套话凑长度。

最后半句是防"AI 凑字数"的关键：宁可短，不要空。

### 14.11.5 验证

- `selfcheck` **19/19**；`compileall` 退出码 0。
- `preview.html` **174,256 字节**（脚本口径 158,014）；折叠摘要块 ×10、`今日简报` ×1、`READING` ×1、`SKIM` ×1、`THE FIELD` ×1。
- 二手 17 条，`text_zh` 长度 **67–84 字**（脚本实测区间）。
- 17 条二手均 `font-size: 13px / line-height 1.85`，与正文同级。

### 14.11.6 忠实性：内容到底可不可信（自检清单）

这是最该被追问的一点，按层拆开：

| 层 | 内容来源 | 可核对的锚点 | 失真的可能 |
| --- | --- | --- | --- |
| 七问七答 / 数字 / 结论 | Stage A 从**全文**抽事实，每条带 `quote` | 每格下面的灰色小字就是原文引文 | 引文与结论对不上（要靠 `selfcheck` 的引文回查 + 人工抽检） |
| 关键数字 | Stage A 只允许从原文抄 | `KeyNumber.quote` | 单位/量级抄错 |
| 领域红旗 | 规则：红黄绿三态 | 状态本身可回查全文 | 模型把"没提"判成"已讨论" |
| 二手中文 | `translate.py`「只译不改，数字照抄」 | 英文原标题 + 日期 + 来源色块 | 译错术语、漏掉限定词 |
| 预览里的分析 | **人工录制**（`config/preview_replay.json`，无 API key） | 文件里 `basis` 字段 | 只有这一层是"人写的"，真实运行时由模型生成 |

**硬规则（写在 `analysis.py` 提示词里）**：只使用给定事实；数字必须原文可查；摘要级与全文级分开标注（`摘要未提` vs `全文未提`）；`relevance` 不是论文质量；`for_you` 必须是可执行动作。

## 14.12 v0.8：模型调用层（DeepSeek 适配）

### 14.12.1 为什么要单独一层

模型调用分散在六个模块里，每个都直接 `client.chat.completions.create(...)`。这样写有两个后果：一是**同一个坑要修六遍**；二是各家的端点差异被埋进业务代码里。

官方文档（2026-10）里三条硬约束直接决定了这层的行为：

| 约束 | 后果 | 处理 |
| --- | --- | --- |
| `max_tokens` 不设上限时 JSON 可能被截断 | 整批解析失败 → 全部降级英文 | 每处调用显式传 `max_tokens` |
| JSON 模式**有概率返回空 content** | 同上 | 空内容自动重试（默认 2 次） |
| 思考模式默认开启、思考 token 按输出计费 | 抽取任务白花钱、变慢 | 抽取显式 `thinking=false` |
| `temperature` 在思考模式下被静默忽略 | 传了没意义 | `thinking=true` 时不传 |
| `thinking` 是 DeepSeek 专有参数 | 发给 OpenAI 直接 400 | 三态：`None` 不发 |

### 14.12.2 降级路径

`response_format` 不是所有兼容端点都认。收到报错后**去掉该参数重试一次**，而不是让整封信降级——因为绝大多数网关仍能听懂"只输出 JSON"这句话。

```
调用 → 失败且错误含 response_format/json_object → 去掉参数重试
     → 返回空 content → 重试（最多 attempts 次）
     → 仍失败 → 抛给调用方（调用方按业务决定降级）
```

这一层**不吞异常**：吞掉之后排查线上问题时看不到真实原因，而各调用方本来就都有兜底（脉搏退主题统计、翻译保留原文、分析保留旧摘要）。

### 14.12.3 配置三态

```
ANALYSIS_EXTRACT_THINKING=false   # 抽取：显式关
ANALYSIS_ASSESS_THINKING=         # 评论：留空 = 跟随端点
```

留空与 `false` 是两件事：**留空 = 完全不发这个参数**。用 OpenAI 的端点时必须留空，否则请求被拒。

### 14.12.4 成本

`deepseek-flash` 的缓存命中价是 `$0.003/M`（未命中 `$0.15/M`）。这意味着把"读者画像 + 领域包判据"这类**每天不变的长 prompt 放在最前面**是真实省钱手段——命中部分按 1/50 计价。当前实现里画像块已经在 prompt 前部。

按 flash + 4 篇精读估算：约 $0.02–0.04/天。若评论换 `deepseek-v4-pro`，约 $0.1/天。

### 14.12.5 验证

`selfcheck` 新增「模型调用层」一项，用假 client 覆盖五条路径：围栏 JSON、空内容重试、格式降级、thinking 三态、max_tokens 存在。全部通过（20/20）。

### 14.12.6 max_tokens 定值依据（v0.8.1）

先澄清一件事：**max_tokens 是天花板，不是预付费**。没生成到的部分不收钱，所以"给宽"几乎零成本，"给窄"却会直接失败——`finish_reason="length"` 时 content 是被切断的半截 JSON。

各调用点的实测/估算输出（DeepSeek 官方口径：1 个中文字符 ≈ 0.6 token）：

| 调用 | 输出内容 | 实测/估算 | 早期值 | 现值 |
| --- | --- | --- | --- | --- |
| Stage A 抽取 | 6 事实(value+quote) + 6 数字 + 4 术语 | ~1,100–1,500 | 4000 | **6000** |
| Stage B 评论 | 21 个字段的完整 analysis JSON（`preview_replay.json` 实测量 1,426–2,246 字符 ≈ 900–1,400 token） | ~1,400 | 6000 | **9000** |
| 总览 + 单篇摘要 | 5 篇 × 120 字 + 总览 200 字 | ~700 | 2500 | **4000** |
| 本周脉搏 | 4 条判断（问题+摘要+对你+证据） | ~600 | 6000 | **8000** |
| 二手翻译 | 12 条 × (标题 30 + 正文 100 字) | ~1,000 | 2000 | **3000** |
| 雷达解读 | 5 条 × 100 字 | ~300 | 4000 | **3000** |

**思考模式要另算**：`completion_tokens` 包含 `reasoning_tokens`（API 里是 `usage.completion_tokens_details.reasoning_tokens`）。开 `thinking=true` 时，同样一段 1,400 token 的答案可能先花掉 1,000–3,000 的思考，6000 就不一定够。因此抽取默认关思考，评论/脉搏即使开，也有下一节的兜底。

**截断自动放大**：`call_json` 检测到 `finish_reason == "length"` 时，把 `max_tokens` 翻倍重试一次（上限 32,000），并把这次重试计入 attempts。这样"给窄了"退化成"慢一次"，而不是"整篇降级成英文"。

**用量日志**：每次调用打印 `模型用量 deepseek-flash：输入 8123 / 输出 1450（其中思考 0）`。第一次真跑后看这行就能校准，不用猜。

### 14.13 首次真跑踩到的五个坑（v0.8.2）

第一次接真实 API 跑 `python -m arxiv_digest.main` 时，连着暴露了五个问题。记录在此，因为它们都不是"偶发故障"，而是设计缺口。

| # | 现象 | 根因 | 修法 |
| --- | --- | --- | --- |
| 1 | `ValueError: SMTP settings are incomplete.` | `main` 的最后一步是发信；用户只想看效果，却必须先有 SMTP | 开跑前就检查，并打印 `--out` 用法；退出码 2，不浪费几分钟抓取 |
| 2 | `model passed: '# 留空则用 OPENAI_MODEL.'` | `.env.example` 写成 `PULSE_MODEL=      # 注释`；python-dotenv 只在**值非空**时才把 `#` 之后当注释，空值会原样读成 model 名 | example 里注释独立成行；`_env_model()` 清洗行内注释 / 引号 / 空值 |
| 3 | `精读 0 / 速览 0，共 0 篇` | 当天是周日，arXiv 只在工作日美东 20:00 公告；`LOOKBACK_DAYS=1` 严格按一天抓就是空的 | 回看窗口自适应：1→2→3→5→7→10→14 天递进，邮件头部标注「覆盖近 N 天」 |
| 4 | 翻译 `模型连续 2 次返回空内容` | DeepSeek **默认开思考**，翻译任务把 3000 token 全花在 reasoning 上，content 为空；放大到 6000 仍然全吃掉 | 非显式指定时 DeepSeek 一律关思考；若仍遇思考吃光预算，自动关思考重试 |
| 5 | `cannot access local variable 'content'` | 截断分支里先读了 `content`，而它在后面才赋值 | 调整顺序：先取 content，再判 finish_reason |

**为什么第 3 条重要**：周日、节假日、arXiv 公告延迟，都会让固定窗口退回空简报。空简报比没有简报更糟——用户会以为抓取坏了。自适应窗口把"今天没有"变成"这是最近三天的新投稿"。

**为什么第 4 条重要**：思考模式的 token 计入 `completion_tokens`，而 `max_tokens` 卡的是总量。当一个任务对模型太简单（翻译就是），思考可能把预算全吃光、一个字的答案都不剩。这不是"偶尔慢"，是"必然失败"。

**实测（2026-10-25 22:03，deepseek-flash）**：59 篇候选 → 14 篇（4 精读 + 10 速览）、4/4 全文、14 次模型调用、输入 64,131 / 输出 14,456 token、思考 0、耗时 2 分 38 秒、按 peak 价约 $0.037/封。`preview_live.html` 208 KB。

### 14.14 六处模型调用与思考模式开关（v0.9.0）

思考模式此前是"跟着端点默认走"——DeepSeek 默认开、OpenAI 没有这个概念。结果是用户想全开却不知道从哪开，想关又不知道哪里还在偷偷烧钱。这版把六个调用点全部显式化。

| # | 调用点 | 干什么 | 环境变量 | 建议 |
| --- | --- | --- | --- | --- |
| 1 | `llm.generate_digest_content` | 今日总览 + 速览一句话 | `LLM_THINKING` | 开 |
| 2 | `analysis.extract_bundle` | Stage A：从全文抽七问事实 + 关键数字 | `ANALYSIS_EXTRACT_THINKING` | 开 |
| 3 | `analysis.assess` | Stage B：画像打分 + 写评论 + 红旗 | `ANALYSIS_ASSESS_THINKING` | **最值得开** |
| 4 | `pulse.compose_pulse` | 本周脉搏的三条判断 | `PULSE_THINKING` | 开 |
| 5 | `radar.annotate_radar` | 邻域雷达每方向 100 字解读 | `RADAR_THINKING` | 开 |
| 6 | `translate.translate_items` | 二手解读中译 | `TRANSLATE_THINKING` | **建议关** |

规则统一为三态：`true` 强制开、`false` 强制关、**留空 = 跟随模型默认**（DeepSeek 端点关，其他端点不传这个参数——发给 OpenAI 会 400）。

**为什么默认关**：实测翻译任务开思考时，3000 token 全被 reasoning 吃掉、`content` 为空。这不是偶发——任务越简单，思考越容易把预算耗光。`call_json` 里保留了一条兜底：遇到"思考吃光预算"会记一条 warning 并关掉思考重试，所以即使误开也不会静默失败，只是多花一次调用。

**同一天同一份数据的对比（deepseek-flash，2026-10-25）**：

| 指标 | 全部关思考 | 全部开思考 |
| --- | --- | --- |
| 调用次数 | 14 | 19（含 2 次重试） |
| 输入 token | 64,131 | 88,421 |
| 输出 token | 14,456 | 57,455（其中思考 39,533） |
| 思考占比 | 0% | 68.8% |
| 耗时 | 2 分 38 秒 | 4 分 25 秒 |
| 单封成本（peak 价） | $0.037 | $0.096 |

思考 token 按输出价计费，所以 2.6 倍成本主要来自这里，输入只涨了 38%（多的是重试）。值不值只能人眼看：如果三类判断（脉搏、"对你"、红旗）确实更靠谱，一个月多 $1.7 完全值得。

**「世界在动」前半中文、后半英文的问题**：不是 `max_tokens` 耗尽，是 `translate_items` 的 `MAX_ITEMS = 12` 把 31 条截成了 12 条——而且是一次调用翻 12 条，一旦这批被截断就整批作废。现在改成 `TRANSLATE_LIMIT=40` + 每批 8 条分批翻，单批失败只损失那 8 条。实测：12/31 → 30/31 有中文。

唯一剩下的那条是 Keck 源里的一条人物讣告（`Manuel F. Ruiz-Herrera Bernal`，标题本身就是人名），模型没有可译内容，原样保留——这属于源噪声，不是翻译失败。

### 14.15 可下载转发 / 世界在动加厚 / 板块配色（v0.10.0）

**1. 下载与转发**

不做 PDF：生成 PDF 要么依赖无头浏览器，要么依赖 WeasyPrint，两者都会让"5 分钟跑通"变成"先装 300MB"。改成**把整封 HTML 作为附件**：`arxiv-digest-2026-10-25.html`，下载后手机浏览器直接打开，微信里也能当文件发。页脚写明附件名，用户不用去翻附件列表。

真要 PDF 也留了后路：浏览器打开附件 → 打印 → 存为 PDF，版式是 table 布局，打印不会散。

**2. 世界在动加厚**

原摘要 50-100 字，读起来像新闻标题的复述。现在改成 150-220 字，按四段写：是什么 / 做了什么 / 结论与边界 / 意味着什么。翻译取样从 600 字符放宽到 900，否则长文没有素材可用。

英文原标题原来是一行 11px 灰色斜体，跟中文标题贴得太近、分不清谁是谁。现在加 `EN` 小标签 + 独立行，层级清楚：

```
稀有月球陨石或填补月球演化空白          ← 中文标题 14px 600
[EN] Rare Lunar Meteorites Could Fill Gaps In Moon's Evolution   ← 11.5px 灰斜体 + 标签
几乎所有陨石都来自小行星……              ← 正文 13.5px / 行高 1.95
```

实测（31 条）：平均 151 字，最短 87（原文信息本来就少，按规则不凑字数），最长 222。

**3. 板块配色**

原来是"一片米白 + 六个黑色标题"，扫描时找不到段落边界。现在给每个板块一个色相，但约束在同一套明度/饱和度里：

| 板块 | 色相 | 标题底色 | 用法 |
| --- | --- | --- | --- |
| 精读 | 琥珀 `#b45309` | `#fdfaf1` | 最重的一块，暖色抓注意 |
| 速览 | 靛蓝 `#2b5f8a` | `#f6f9fc` | 冷色后退，表示"扫一眼" |
| 世界在动 | 松绿 `#2f6b52` | `#f5faf7` | 外部信息，和自产内容区分 |
| 本周脉搏 | 紫 `#6b4c9a` | 无 | 观点类内容，紫=判断 |
| 邻域雷达 | 钢青 `#3f6273` | 无 | 次要方向，压到最低存在感 |
| 近期日程 | 赭 `#8a6a2f` | 无 | 事件档案感 |

三条原则保证"丰富但不花"：色相只出现在**标题行、4px 色条、来源标签**上，正文一律深灰；底色只给三个主板块，且是同一暖纸系的浅一档（不是饱和色块）；所有颜色的明度接近，所以并排时不打架。

**4. 标题层级**

`16px` → **`21px`**，并加左侧 4px 色条（比加粗更省空间，也是期刊目录的常见做法）。眉题 `READING / SKIM / THE FIELD` 保留 10.5px + 2px 字距，作为"这一块是第几层"的无声提示。脉搏/雷达/日程的小标题从 11px 提到 14px —— 它们不是一级板块，但不该比正文还小。

### 14.16 深空冷色版（v0.11.0）

**为什么推翻上一版**

上一版是"暖纸 + 六个色相"，问题在于它像**印刷杂志**，而不像天文人每天面对的东西——夜里的观测界面、DS9、Aladin、终端里的 FITS header，都是深底冷色。深色不是审美偏好，是这个职业的默认工作环境：夜里看屏幕，白底是刺眼的。

**色板**

| 角色 | 色值 | 说明 |
| --- | --- | --- |
| 页面底 | `#05080f` | 近黑带蓝，纯黑会让边缘发灰 |
| 容器 | `#0b1220` | 比页面亮一档，靠 1px 冷描边分层 |
| 卡片 | `#0f1a29` | 每篇论文是一块"面板" |
| 分隔线 | `#1d2f44` | 蓝灰，不用纯灰（纯灰在蓝底上发脏） |
| 正文 | `#cfe0f0` | 冷白，不用 `#fff`（纯白在深底上发飘） |
| 弱化 | `#8ba1b6` | 次要信息 |
| 信号青 | `#22d3ee` | 精读 / 链接 / 报头细线 |
| 电靛 | `#818cf8` | 速览 |
| 极光绿 | `#2dd4bf` | 世界在动 |
| 紫外 | `#c084fc` | 本周脉搏 |
| 天蓝 | `#38bdf8` | 邻域雷达 |
| 琥珀 | `#fbbf24` | 近期日程 |

**三条约束（防止"赛博朋克 disco"）**

1. 高饱和色只做**发光点**：标题行、4px 色条、状态胶囊、链接，面积不超过版面 5%。大面积仍是深蓝灰。
2. 所有正文对比度 ≥ 7:1（WCAG AAA）。实测：正文 13.89、标题 16.27、弱化 7.02、青 10.36、靛 6.28、绿松 10.06、紫 6.97、天蓝 8.74、琥珀 11.22。
3. 冷暖只在一个地方对撞：**结论横幅**用深棕 `#2a1708` 承托青边，提示"这是仪器读数，不是又一段正文"。

**改了什么**

- 报头：`#060b13` + 顶部 2px 青线 + `border-bottom` 换成 1px `#22374f`（原来的 3px 黑线在深底上是"断头"）。
- 主容器：`#0b1220` + 1px `#1d2f44` 描边 + 青色外发光 `0 0 0 1px rgba(34,211,238,.06)`。
- 61 个色值全部重映射（202 处），事后把三档过暗的灰（`#64748b`/`#5b6b80`/`#6b7d92`）提到 `#8296ab`/`#7d90a6`/`#8092a6`，否则小字在深底上不到 AA。
- 方法链 chip 深青字 → 亮青 `#7fe3d3`（原色在深底上读不出）。
- 七问面板比卡片亮一档（`#101d2d`），避免"挖洞"感。

**邮件端的两个坑**

- Gmail 移动端会强制反色，深色邮件可能被反转。已加 `<meta name="color-scheme" content="dark only">` 与 `supported-color-schemes`，但**这是建议不是保证**，Apple Mail / Outlook 桌面正常。
- Outlook 桌面不认 `border-radius` 与 `box-shadow`，退化后是直角深色块，不影响阅读。

**验收方式**：用 headless Edge 截图逐屏看（`msedge --headless=new --screenshot`），而不是只看 HTML 源码——颜色问题只有渲染出来才看得见。

### 14.17 网格纹理 / 卡片发光 / 双主题（v0.11.1）

#### 14.17.1 为什么要做"令牌化"

上一版把 185 个十六进制色值散落在模板里。想做第二套配色时，任何一次"再调一次颜色"都要重新搜一遍全部 185 处，而且必然漏掉几处——漏掉的后果不是"有点丑"，是某一行在深底上变成读不清的深灰。

所以先做一步看起来跟视觉无关的改造：模板里 **不出现任何颜色字面量**，只写 `{{ P.read }}`；颜色由 `arxiv_digest/theme.py` 的 `DARK` / `LIGHT` 两张表提供，每张 54 个键，两张表的键集必须完全一致（自检会比对）。

颜色因此变成"渲染期的参数"：同一份 `digest.json` 可以渲染成深色邮件、浅色邮件，以后接网页或 PDF 也不用再动数据。

有一处坑：模板里有 7 处颜色是**跟着条件变**的（争论/未定/新结果三态胶囊、日程紧急度、红旗三态、投稿量箭头、来源色）。这些必须先手工改写成 Jinja 表达式，再交给令牌替换——否则替换出来的 `{{ }}` 会被当成普通文本，屏幕上直接显示花括号。

#### 14.17.2 网格纹理：为什么是 24px、为什么这么淡

报头铺两层 `repeating-linear-gradient`（0° 与 90°，周期 24px）。深色版颜色 `rgba(34,211,238,0.055)`——**5.5% 不透明度**。

这个数值是调出来的，不是拍的：

- 低于 3.5%，1440px 宽的屏幕上几乎看不见，等于白做；
- 高于 8%，文字开始被网格线穿过，读起来像"屏幕有摩尔纹"；
- 5.5% 的结果是：正看几乎只觉"这区域不是纯色"，斜看或放大时能看出是格子。

浅色版用 10%，因为纸面上的线天然更淡，同样的不透明度在浅底上会弱一档。

退化行为：Outlook 的 Word 渲染引擎不认 `background-image`，会直接显示底下的纯色。可接受——网格是装饰，不是信息。

#### 14.17.3 卡片发光：亮的是"卡"还是"字"

精读卡加了 `box-shadow`：

- 深色：`0 0 0 1px rgba(34,211,238,0.06), 0 0 24px rgba(34,211,238,0.10)` —— 外面一圈几乎透明的青光；
- 浅色：`0 1px 2px rgba(26,26,46,0.05), 0 10px 24px rgba(26,26,46,0.06)` —— 中性投影。

判断标准只有一条：**读的时候眼睛会不会被卡片边缘拽走**。24px 的模糊半径、10% 的透明度，结果是"卡片浮在底色上"，而不是"卡片在发光"。如果做到 30px/20%，它就会变成霓虹灯，把正文压下去。

#### 14.17.4 双主题的边界

| 能力 | 支持 |
| --- | --- |
| `EMAIL_THEME=dark\|light` 配置 | ✅ |
| 命令行 `--theme light` 临时覆盖 | ✅ |
| 别名（深色/暗色/夜、浅色/亮色） | ✅ |
| 跟随系统深色（`prefers-color-scheme`） | ❌ 邮件客户端不认，只能显式配置 |
| Outlook 里的网格/发光 | ❌ 退化为纯色，不影响可读 |

#### 14.17.5 验证

- `python -m arxiv_digest.selfcheck` → **22 项全绿**（新增「主题配色」）。
- 两版渲染：`preview_dark.html` 178,918 字节、`preview_light.html` 179,411 字节。
- 用 headless Edge 截图逐屏核对（`--window-size=800,1500`），确认网格在报头成立、卡片边缘有可见的层次、正文没有被发光干扰。

