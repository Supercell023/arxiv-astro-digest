# 两维反馈与安全回读（v0.13）

相关性和有用性分别取 0/1/2。六个邮件链接先打开确认页，点击确认后才写入，防止邮件扫描器误记。只修改所选维度，另一维保留。

学习依赖分类和作者元数据。流水线持久化论文元数据，使 CSV 回读可以学习；未知论文只能保存票值，元数据补齐后才参与排序。作者偏好按姓氏聚合，是排序启发式，不是人物消歧。

## 本机端点

```powershell
python scripts/feedback_server.py
```

配置 `FEEDBACK_URL=http://127.0.0.1:8765/log` 后重新生成邮件。本机 GET 不写文件，POST 使用一次性确认令牌；非本机监听必须设置 FEEDBACK_SIGNING_KEY。电脑需要在线，手机通常无法访问本机地址。

数据写入被忽略的 FEEDBACK_PATH（默认 config/feedback.json）；从 state.json 和可选 digest.json 补元数据，不再要求每次点击都有当天快照。

## 私有 Sheet + Apps Script

使用仓库中的 [google_feedback.gs](../scripts/google_feedback.gs)，不要沿用旧版无签名 GET 写入示例。无需把 Sheet 共享为任何人可查看。

1. 建私有 Sheet，表头依次为 `item,relevance,usefulness,t`。
2. 新建 Apps Script，粘贴 scripts/google_feedback.gs。
3. 在脚本属性设置 FEEDBACK_SPREADSHEET_ID、FEEDBACK_SIGNING_KEY、FEEDBACK_CSV_READ_TOKEN。后两个使用不同的私密随机值，不放源码、仓库或日志。若这张表还复用给长期热点档案（见 `docs/ARCHIVE.md`），再加 `FEEDBACK_SHEET_NAME=feedback`，把投票写在指定 tab 而不是"第一张"。
4. 部署为 Web 应用，以“我”身份执行，允许邮件浏览器访问部署入口；Sheet 本身仍私有。
5. 在私有 .env 配置：

```ini
FEEDBACK_URL=https://script.google.com/macros/s/<deployment-id>/exec
FEEDBACK_SIGNING_KEY=<与脚本属性相同的私密值>
FEEDBACK_SHEET_CSV_URL=https://script.google.com/macros/s/<deployment-id>/exec?action=csv&token=<独立读取凭据>
```

Actions 中签名密钥和 CSV URL 放 Secret。修改 Apps Script 后发布新部署版本；修改签名密钥后重新生成邮件。链接有效 30 天，确认令牌最多 15 分钟；缓存提前回收时重新打开邮件链接。

CSV 读取凭据只能读表；有效签名链接的持有人可以记录该链接对应的反馈，不要公开转发包含个人反馈入口的邮件。

API 用法对照官方 [Web App](https://developers.google.com/apps-script/guides/web)、[HMAC](https://developers.google.com/apps-script/reference/utilities/utilities)、[Cache](https://developers.google.com/apps-script/reference/cache/cache) 文档。签名编码、确认流程、CSV 权限有离线桩测试；本次没有部署或验证真实 Google 服务。

## 合并与持久化

- CSV 使用标准解析器，支持带引号的 DOI；第四列为实际点击时间，支持 ISO 8601 时区。
- 两维独立取最新记录。无时间戳的旧行按顺序合并，不覆盖较新的有时间戳决定。
- 完整票值保存在忽略的 feedback.json；默认 workflow 不把个人 votes 提交进 state.json。
- 云端每轮从 Sheet 重取票值，state 保留学习需要的公开论文元数据。
- 与热点档案共用一份表格时：反馈行固定写 `feedback` tab（或 `FEEDBACK_SHEET_NAME` 指定的 tab），CSV 地址里的 `gid` 要指向同一张 tab；档案脚本只认自己的白名单表，读不到投票原文。投票还会按主题聚合进档案的 `feedback_view` 表，和 `weekly_topics` 用同一个键并排看。
- 成功发送或确实没有待发送内容后，才提交本轮合并；预览只计算，不消费业务状态。

## 命令行

```powershell
python scripts/feedback.py record --id 2610.31660v1 --rel 2
python scripts/feedback.py record --id 2610.31660v1 --use 0
python scripts/feedback.py import votes.csv
python scripts/feedback.py stats
```

旧 --vote up/meh/down 兼容为两维同档。CLI、HTTP 与流水线采用锁和原子替换；未知元数据时提示只能记票。相关性权重 0.65、有用性 0.35，按分类/作者聚合后加入反馈分数，并同步重算星级。
