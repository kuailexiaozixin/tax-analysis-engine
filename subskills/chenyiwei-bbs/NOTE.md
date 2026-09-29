# chenyiwei-bbs 子技能：收录说明与实测校正

本目录收录的是**外部编写的子技能文档**，用来补齐主线的一个取证缺口：税法条文
大量以会计确认为前提（"确认收入""计提减值""权益法核算""纳入合并范围"），
而五个税收数据源里没有任何一处写会计准则怎么规定。

## 收录来源

| 项 | 值 |
|---|---|
| 文件 | `SKILL.md`（与上游字节一致，未作任何改写） |
| 上游地址 | `https://bbs.auditdog.cn/chenyiwei-skill/SKILL.md` |
| 抓取时间 | 2026-09-29 |
| SHA-256 | `0ba6ef6a1481f0875da924e2fc86deb7f794993bec40f51d348f609346497c5a` |
| 服务基址 | `https://bbs.auditdog.cn`，匿名可访问，无需 token 与 API Key |
| 依赖 | `curl` 与 HTTPS 出站；不调用任何模型，不消费任何模型额度 |

**同步方式**：`curl -o subskills/chenyiwei-bbs/SKILL.md https://bbs.auditdog.cn/chenyiwei-skill/SKILL.md`
后与本文件的 SHA-256 比对；对不上说明上游改过，要重读一遍下面"实测校正"是否仍然成立。

**为什么不改编上游文件**：它同时是可以被 Claude Code / Codex / Cursor 直接安装的独立
技能，改一个字就会让"与上游 diff"失去意义。本项目的补充说明只写在这个文件里。

## 实测校正（以本机当日返回值为准）

上游文档写的是接口应有的样子，下面是实际打出来的差异。**引用准则前先看这一节。**

| 现象 | 实测证据 | 对主线的影响 |
|---|---|---|
| **准则实体的 `docNo` 不表示条文版次** | `/api/public/ref/cas-33` 返回 `docNo=财会〔2006〕3号`、`doc.officialUrl` 指向 2008 年的财政部页面，而第二条正文已是"拥有对被投资方的权力"并出现"结构化主体"——这是 2014 年修订后的措辞；`/api/public/ref/cas-2` 同样报 `财会〔2006〕3号`，第二条已是"以及对其合营企业的权益性投资"（共同控制在 2014 修订后归《合营安排》准则） | 把 `docNo` 当"这是 2006 版条文"会引错版本；当"这就是现行版"又没有证据。**版次只能由条文措辞自证**，引用时写准则名＋条款 heading，不写 `docNo` 当版本依据 |
| **`status` 没有可判版次的取值** | 本次实测取到的准则条目 `status` 均为 `current`，`supersededBy` 均为 `null`；未取到非 `current` 的样本 | 不能凭 `status=current` 说"这就是最新版"。主线的时效判定（`tax_evidence.judge_validity`）不接管这一栏，会计口径的在效结论要另找证据 |
| **`slug` 拼不出来，必须从检索里取** | `/api/public/ref/cas-14` 与 `cas-2-2014` 都返回"未找到该准则"，而 `cas-14-2017`、`cas-2`、`cas-33` 有内容——年份后缀时有时无 | 每次取准则原文都要先 `/api/learn/search` 或 `/api/public/references` 拿 `slug`，再调 `/api/public/ref/{slug}`；不得由编号自己拼 slug |
| **`recent` 可以整天空** | `/api/public/recent?days=1` 返回 `total=0`、`count=0`，结构完整、HTTP 200 | 空结果只说明窗口内没有新帖，不代表服务故障，也不代表陈版主没答过这个主题；这时改用 `search` 或 `by-tag` |
| **同一套分页字段在不同接口类型不一样** | `/api/public/search` 的 `page`/`pageSize`/`count` 是整数；`/api/public/penalties?year=2025` 的 `total`/`page`/`pageSize` 是字符串（`"203"`、`"1"`、`"20"`） | 两个接口的分页字段不能共用一句"取出来比大小"的处理；拿 `penalties` 的数字前先转一遍 |
| **返回里有上游文档没写的字段** | `search` 顶层另有 `fallback`、`highlight_terms`，条目另有 `tags`；`ref/{slug}` 顶层另有 `guide`、`doc`、`cardsByUnit`、`unitCiteCount`、`fallbackTids`；`tags` 接口报 `count=64` | `tags` 可直接用来把检索词换成论坛在用的标签（`by-tag`），比自由词命中稳；其余字段忽略即可，不要当成状态位 |
| **`latest_time` 两种格式** | `search` 条目是 ISO 串（`2026-03-04T12:20:24.000Z`），`ref-posts` 条目是日期（`2026-08-28`） | 转成"三天前"这类人话时两条路径都要走，别按一种格式解析 |

## 主线里的唯一入口

**本技能不自行决定何时调它。** 触发条件、在答案里的位置、禁止事项都写在母技能
`SKILL.md` 的 ③「会计口径缺口」那一节；本节只登记来源与实测差异。
