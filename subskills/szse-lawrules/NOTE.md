# szse-lawrules 子技能：来源、实测取证与硬约束

本目录收录的是**本项目自行编写**的查询式子技能（对标 `maodocs/`、`chenyiwei-bbs/`、
`税屋`）。上游只有一个人工维护的抓取脚本（`D:\CPAHelperForExcel\…\szse-lawrules\
scripts\szse_lawrules_scraper.py`，playwright+openpyxl 全站爬成 Excel），本项目**不改
那份原技能**，只把"深交所法律规则"这一信息源按查询式重做成仓库原生子技能。

用来给母技能 ③「库外专业口径」层补上**证券交易场所自律规则**这一格——五个税收源、
MaoDocs、chenyiwei-bbs 都不收交易所自己的业务规则。

## 收录来源

| 项 | 值 |
|---|---|
| 站点 | `http://www.szse.cn/www/lawrules/`（深交所官网，公开匿名可访，无 token） |
| 左侧导航通道 | 49 个 `index.html` 叶子页（实测当日枚举） |
| 本目录文件 | `SKILL.md`（技能说明）、`szse.py`（build/query/fetch/categories） |
| `szse.py` SHA-256 | `a9b6fd151b5c062aae9bd94cac98146a5c5564f80a609b08e49d75c2bd462d6f`（本机 2026-09-29 版本） |
| 索引产物 | `szse_index.json`（`build` 生成，随技能分发不含，已在 `.gitignore` 内） |
| 依赖 | 标准库直连；浏览器通道复用母技能 `scripts/tax_browser.py`（**不新增依赖**） |
| 模型额度 | **不调用任何模型，不消费任何额度** |
| 抓取时间 | 2026-09-29 |

## 站点渲染的两种形态（决定了 build 的默认范围）

| 形态 | 通道 | 取法 | 当日实测 |
|---|---|---|---|
| **内联 SSR** | 法律 / 行政法规 / 司法解释 / 证监会规章 / 适用指引 / 规范性文件 / 废止公告 | 纯 urllib：每条是 `<li>` 里 `var curHref`/`var curTitle` + `<span class="time">`；翻页 `index.html→index_N.html` | 7 通道 582 条，`build` 默认覆盖 |
| **运行时模板** | 12 类"本所业务规则"（股票/债券/基金/衍生品/REITs/会员/交易通则…） | 直连只返回 `_.each(data,…)` 空壳（`/common/js/articleList.js` 直连 404）；须 `tax_browser.BrowserSession.read_html()` 渲染出 `a.art-list-link` 锚点后解析 | `build --browser --channel stock` 实测 14 条（Edge，无模型） |
| **落地页快照** | `lawrules/index.html` | 内联混排约 122 条最新业务规则，stdlib 直取 | `build --snapshot` 实测 122 条 |

默认 `build`（不加 `--browser`）产出 724 条（582 业务通道 + 122 快照，去重后）；确定性好、
不开浏览器。交易所自律规则若快照不够，再 `--browser` 补对应通道。

## 实测校正（以本机当日返回值为准）

| 现象 | 实测证据 | 对主线的影响 |
|---|---|---|
| **URL 的 t 编号不表版次** | `…/t20250327_612564.html` 标题"（2023年修订）"、列表日期 2023-12-15，t20250327 只是 CMS 生成序号 | 引用写"规则名＋（修订年份）"，别拿 URL 编号或文号当版本依据（与 chenyiwei `docNo`、MaoDocs 文号陷阱同源） |
| **列表日期≠施行日** | 详情页正文写"自发布之日起施行"并另列废止旧文号，与列表 `class="time"` 日期口径不同 | 本技能只给目录与定位；生效时点按正文自述，不接进 `tax_evidence.judge_validity` |
| **正文附件常是 PDF/DOC** | 规范性文件/司法解释多为 `P….pdf`/`.doc`，`fetch` 对非 `.html` 只回链接 | 要逐条原文得下载附件另行解析；`fetch` 回显会明确标 `type:pdf` |
| **业务规则页 502 抖动** | `rule/stock/trade/index.html` 直连偶发 HTTP 502，重试即通 | `_get_retry` 带退避重试；一次 502 不算通道失败 |
| **浏览器通道依赖本机浏览器** | 当日仅探到 `edge` 可启动；playwright 若不在则 `build --browser` 跳过该通道并记原因 | 分发到他机时业务规则通道可用性取决于本机是否装了 Edge/Chrome 与 playwright |
| **Git Bash 路径改写** | `fetch "/lawrules/…"` 里以 `/` 开头参数被 MSYS 改写成 `D:/Git/…` | 传完整 URL；脚本对 `http` 开头不拼接 |

## 与其他专业口径子技能的分工

三者并排在母技能 ③「库外专业口径」层，取的东西不同：

- **szse-lawrules**：证券**交易所自律规则**与规章/规范性文件的**目录＋原文定位**（先建索引、离线查）。
- **maodocs**：会计/审计/内控/评估/证券监管**规范逐条原文全文**（静态站直连）。
- **chenyiwei-bbs**：陈版主**实务答疑**、处罚案例、准则衍生问答。

同一份证监会文件，MaoDocs `garr` 与深交所 `csrc_*` 都可能收到；**要目录与官网链接走
szse，要逐条原文走 MaoDocs**。三者都不进五源、不进 ⑧ 定级，触发条件与在答案里的位置
都写在母技能 `SKILL.md` ③ 与 `references/out_of_library_layers.md`。

**本技能不自行决定何时调它。**
