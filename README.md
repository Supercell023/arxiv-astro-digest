# arXiv 每日简报

[**中文**](README.md) · [English](README.en.md)

把 arXiv 的一条分类流，变成一封 **20 分钟能读完、且带判断的** 每日研究简报。

每天定时抓取新论文与领域动态 → 抽取事实 → 按你的读者画像打分与评论 → 渲染成一封邮件（或 HTML 文件）发出。

`v0.11.1` · Python ≥ 3.11 · MIT

---

## 一、这是什么

大多数人订阅 arXiv 的方式是"每天收到一堆标题，然后一篇都不点开"。这个项目想解决的不是"信息不够"，而是**信息过载导致的判断瘫痪**：一天几十篇新投稿，你真正需要的只是"哪两三篇跟我有关、为什么有关、值不值得现在读"。

所以它做的不是摘要搬运，而是四件事：

1. **筛**——按领域包和读者画像打分，14 篇进邮件，其余不打扰。
2. **抽**——从 PDF 全文里抽出"用了什么数据、怎么筛、怎么校正、用什么方法、得出什么"，每个字段都带原文引文。
3. **评**——只基于抽出来的事实写评论，给阅读建议，并反查本领域的常见坑。
4. **看全局**——同期其他方向在动什么、这一周大家在争论什么、哪些数据发布快到了。

---

## 二、它每天给你什么

邮件从上到下是"先给世界，再给你"的顺序：

| 板块 | 内容 | 说明 |
| --- | --- | --- |
| **LLM 总览** | 3–5 句今日导读 | 今天最值得注意的一条是什么 |
| **本周脉搏** | 本周集中出现的 3–4 个议题 | 每条判断必须挂 arXiv 编号作证据，无证据直接丢弃 |
| **邻域雷达** | 5 个非本领域方向各 1 条 | 系外行星 / 恒星 / 高能 / 仪器 / 太阳系，其余折叠 |
| **近期日程** | 数据发布、会议、提案截止 | `D-19` 倒计时，日期未公布也显示"预计 5 月" |
| **精读** | 4 篇，每篇 10 个分区 | 见下 |
| **速览** | 10 篇，一行一篇 | 中文一句话 + 可展开的中英摘要 |
| **世界在动** | 9 个信息源的二手解读 | 中文标题 + 150–220 字中文解读 + 来源标注 |
| **投稿量变化** | 本周 vs 上周各方向投稿数 | 只报可核对的数字，不编"热度" |

**精读卡的 10 个分区**（这是整个项目的重心）：

1. 一句话结论 + 阅读建议（今天读完整篇 / 看方法与图表即可 / 留档）
2. 方法链：`数据 → 筛选 → 估计量 → 推断` 的横向骨架
3. 七问七答：数据、处理、系统误差、方法、结果、局限、对你的用处
4. 关键数字表：样本量、误差、显著性，每个都带原文引文
5. 图表线索：从 PDF 里正则抽出的图注（不花模型的钱）
6. 今天能做什么：3 条动宾句
7. 红旗反查：本领域常见问题的逐条核对（已讨论 / 部分涉及 / 未提及）
8. 术语小抄：只解释你画像词表之外的生僻词
9. 中文摘要
10. English abstract（折叠，点击展开）

---

## 三、三个关键设计

**① 两阶段，而不是一次生成。**
先让便宜模型从全文里抽结构化事实（每个字段带 `quote`），再让强模型拿着事实 + 你的画像写评论。好处是：抽错能定位到字段，评论不会凭空编造；事实可以缓存复用；评论换模型不用重抽。

**② 每条判断都要挂证据。**
脉搏里"某篇论文说了什么"必须给出 arXiv 编号，给不出就丢弃这条判断。没有证据的判断宁可不说——这比说得漂亮重要。

**③ 领域知识写在 YAML，不写在代码里。**
`config/packs/*.yaml` 定义分类、评审重点、常见红旗、脉搏主题。换方向是编辑 YAML，不是改 Python。

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
python -m arxiv_digest.selfcheck                 # 22 项自检，不联网、不花钱
python -m arxiv_digest.main --out preview.html   # 真跑一次，只出 HTML 不发信
```

满意后再填 SMTP 五项，去掉 `--out` 就会真正发信。

> 国内网络装依赖建议加镜像：`-i https://pypi.tuna.tsinghua.edu.cn/simple`

---

## 五、常用命令

| 命令 | 用途 |
| --- | --- |
| `python -m arxiv_digest.selfcheck` | 22 项离线自检（导入、配置、解析、分派、渲染、主题…） |
| `python -m arxiv_digest.main --out preview.html` | 真跑一次，写 HTML 不发信、不写状态 |
| `python -m arxiv_digest.main` | 完整流程，会发信并写 `state.json` |
| `python scripts\build_preview.py` | 用录制数据渲染预览（不联网、不花钱） |
| `python scripts\build_preview.py --theme light` | 切浅色主题预览 |
| `python scripts\preview_digest.py --out p.html` | 跑真实数据但只输出 HTML |
| `python scripts\feedback.py record --id 2610.31660 --vote up` | 记一条反馈 |
| `python scripts\feedback.py stats` | 看学到的偏好 |
| `python scripts\eval_analysis.py check --digest dump.json` | 评测抽取质量 |
| `python scripts\refresh_pulse.py` | 重新抓一次脉搏材料 |

---

## 六、配置

完整变量见 [`.env.example`](.env.example)，共 57 个。最常改的：

| 变量 | 默认 | 作用 |
| --- | --- | --- |
| `DEEP_PAPERS` / `BRIEF_PAPERS` | 4 / 10 | 精读与速览篇数（20 分钟预算） |
| `ANALYSIS_ENABLED` | false | 是否走两阶段精读 |
| `FULLTEXT_ENABLED` | true | 精读是否下载 PDF 全文 |
| `EMAIL_THEME` | dark | `dark` 深空冷色 / `light` 暖纸浅色 |
| `EMAIL_ATTACH_HTML` | true | 邮件附一份 HTML，可下载转发 |
| `CALENDAR_ENABLED` | true | 近期日程板块 |
| `RADAR_ENABLED` | true | 邻域雷达板块 |
| `PULSE_ENABLED` | true | 本周脉搏板块 |
| `TRANSLATE_ENABLED` | true | 二手解读中文翻译 |
| `FIELDS` | 空 | 勾选领域包，逗号分隔；空=全部启用的包 |

**模型相关**：`OPENAI_BASE_URL` / `OPENAI_MODEL` / `ANALYSIS_EXTRACT_MODEL` / `ANALYSIS_ASSESS_MODEL` / `PULSE_MODEL`。
DeepSeek 思考模式默认开启，六个 `*_THINKING` 开关可分别控制（留空则跟随模型默认，DeepSeek 下自动关）。

**成本**：一次完整运行约 6 万输入 + 1.5 万输出 token；DeepSeek Flash 全关思考约 **$0.04/天**，全开约 **$0.10/天**。

---

## 七、换成你的方向

不用改代码，改三个文件：

| 文件 | 作用 |
| --- | --- |
| `config/profile.yaml` | 读者画像：你是谁、关注什么、每天几分钟、七问要问什么 |
| `config/packs/*.yaml` | 领域包：分类、评审重点、常见红旗、脉搏主题 |
| `config/sources.yaml` | 二手信息源与会议（Indico event id） |

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

完整变更见 [CHANGELOG.md](CHANGELOG.md)。

### 从旧版本升级

1. **对比 `.env`**：新版新增变量都有默认值，不填也能跑；但 `EMAIL_THEME`、`FULLTEXT_*`、`RADAR_*`、`TRANSLATE_*` 建议显式写一行，避免"以为开了其实没开"。
2. **`.env` 注释要独立成行**：`KEY=   # 注释` 会被 dotenv 当成值本身（`# 注释`）。新版代码会剥掉，但旧配置值得顺手清理。
3. **`state.json` 兼容**：v0.6 起多了 `fetched` 字段（源分频用），旧文件缺这个字段会自动补空，不用手工迁移。
4. **`deepseek-chat` / `deepseek-reasoner` 已下线**：DeepSeek 现在的模型名是 `deepseek-flash` / `deepseek-v4-pro`。
5. **升级后先跑自检**：`python -m arxiv_digest.selfcheck`，22 项全绿再跑真实流程。

---

## 九、目录结构

```
arxiv_digest/
  main.py           入口：预检 SMTP、--out 模式、发信
  pipeline.py       主流程：抓取 → 分析 → 脉搏/雷达/翻译 → 渲染
  config.py         57 个环境变量 → Config
  arxiv_client.py   arXiv API 抓取（分页、429 退避、自适应时间窗）
  analysis.py       两阶段：抽事实 → 写评论
  llm_client.py     统一模型调用（JSON 兜底、思考开关、截断重试）
  fulltext.py       PDF 下载、抽取、清洗、裁剪、抽图注
  pulse.py          本周脉搏：主题统计 + 证据约束的判断
  radar.py          邻域雷达：跨方向采样与折叠
  translate.py      二手解读中文翻译（分批）
  feedback.py       反馈学习（分类/作者权重）
  calendar.py       日程倒计时
  packs.py          领域包加载与分派
  profile.py        读者画像
  mathfmt.py        LaTeX → Unicode
  theme.py          两套 54 键色板
  state.py          跨天去重、源分频、投稿量统计
  sources/          RSS / Indico 抓取
  templates/        邮件 HTML（无硬编码色值）
  selfcheck.py      22 项离线自检
config/            画像、领域包、源、日程、雷达、回放数据
scripts/           预览、评测、反馈、脉搏刷新
docs/              PRODUCT_PLAN.md（设计文档）、PROJECT_REVIEW.md（项目评审）
```

---

## 十、数据边界

**能稳定拿到**：arXiv API（限流友好，带退避）、AAS Nova、NASA、Phys.org、Universe Today、ESA、ESO、DESI、Keck、Indico 会议导出。

**拿不到 / 不做的**：

| 项 | 原因 |
| --- | --- |
| Astrobites | 对数据中心 IP 返回 403（GitHub Actions 上通常正常） |
| hubblesite / webbtelescope / Chandra / NOIRLab | 没有公开 RSS（404） |
| 实时引力波告警 | 需要常驻监听，Actions 做不到 |
| 论文公共讨论区 | 需要后端与审核，不做 |
| 引用图谱 | 需要离线索引，不做 |

---

## 十一、已知限制

- **扫描版 PDF** 抽不出文本，会退回摘要级分析（卡片上会标"仅摘要"）。
- **投稿量变化**要连续两天状态才有数据，第一次运行是空的。
- **折叠**依赖 `<details>`：Apple Mail / iOS 正常折叠，Gmail 与 Outlook 会直接展开（可接受，内容不丢）。
- **网格纹理与卡片发光**在 Outlook 会被忽略，退化为纯色，不影响可读。
- **没有自动判定"摘要是否忠于原文"的手段**——只能靠引文可回查 + 每周人工抽检。

---

## 十二、文档与许可

- [docs/PRODUCT_PLAN.md](docs/PRODUCT_PLAN.md) — 设计文档（为什么这样做，每个版本踩了什么坑）
- [docs/PROJECT_REVIEW.md](docs/PROJECT_REVIEW.md) — 项目评审（早期问题清单与评分诊断）
- [CHANGELOG.md](CHANGELOG.md) — 变更记录
- [MIT License](LICENSE)
