# maodocs 子技能：来源、实测取证与硬约束

本目录收录的是**本项目自行编写**的子技能（与上游字节一致的 chenyiwei-bbs 不同，
docs.maoyanqing.com 只是一个内容站，官方没有提供技能件，所以取法由本项目按站内实测写）。

用来把母技能 ③「库外专业口径」那一层从"只有会计准则"扩到**会计 / 审计 / 内控 /
评估 / 证券监管**五域的规范原文。五个税收数据源都不收这一层。

## 收录来源

| 项 | 值 |
|---|---|
| 站点 | `https://docs.maoyanqing.com`（VuePress 静态站，Tencent EdgeOne 托管） |
| robots.txt | `User-agent:* / Disallow:`（全放行），并声明 `Sitemap: /sitemap.xml` |
| sitemap.xml | 1,249 页，逐页带 `lastmod`、`changefreq=daily`；整站 Last-Modified 2026-09-28（在维护） |
| 五大域页数 | accounting 612 / auditing 342 / control 112 / appraisal 92 / securities 90 |
| 本目录文件 | `SKILL.md`（技能说明）、`maodocs.py`（纯标准库取文脚本） |
| `maodocs.py` SHA-256 | `0de9f8a6ad6c494d6db6274cf573198399f295b3b948751b70cda4d4b6826a34`（截至本机 2026-09-29 版本） |
| 依赖 | 仅 Python 标准库 + HTTPS 出站；**不调用任何模型，不消费任何模型额度** |
| 抓取时间 | 2026-09-29 |

## 与税屋取法的区别（为什么这里没有过 WAF 那一段）

税屋（`scripts/tax_shui5.py`）前置阿里云 WAF，正文要点真浏览器实跑 JS 才放行。
MaoDocs **无前置防护**：直连 curl/urllib 即返 200 带全文。实测两个准则页
（`/auditing/csa/1101.html`、`/accounting/ent/cas/`）HTML 里都含真实条文、且 WAF
挑战位恒为 `False`。所以本脚本没有 `BrowserSession`、没有 `acw_sc__v2`、没有 Jina
兜底——那三段对本站是多余的。脚本仍保留一处 WAF 检测，是为将来站方若上防护时能在
正文缺失时说清"是被拦不是空页"，而不是静默返回空。

## 实测校正（以本机当日返回值为准）

| 现象 | 实测证据 | 对主线的影响 |
|---|---|---|
| **文号不证明版次** | `cas/02.html` 标题带"（财会〔2014〕14号）"、正文是 2014 修订后措辞；同分类 `cas/01.html` 仍带"（财会〔2006〕3号）" | 引用只写规范名＋条款号；与 chenyiwei-bbs 的 `docNo` 陷阱同源，见该目录 NOTE.md |
| **"时效性：现行有效"是站方录入** | 详情页发文信息块带"时效性：现行有效"文字，非接口状态位、无修订链 | 不接进 `tax_evidence.judge_validity`；别据此写"经判定现行有效" |
| **索引页混挂全局导航** | 分类索引页 HTML 同时含站点全局导航（会计法/证券法/注册会计师法/企业会计制度）与本类子条目 | 脚本 `search` 只认 `/{分类前缀}/` 下的链接；否则"准则""法"这类词会把导航项当命中带回 |
| **一个准则可能是一整篇、不按主题分页** | `cass`（小企业会计准则）本类只有 2 页：正文 + 附录（会计科目/账务处理/报表）；搜"存货"返回 0 是**确实没有单独存货篇**，不是失败 | 空结果先按分类粒度判断，别当取数失败；要按主题找应先确认该系列是否分篇 |
| **`garr` 是跨专题大类** | `securities/garr/` 下按 accounting/auditing/appraisal/issuing/listing/overseas… 分子目录，原生 38 篇；搜"减持"返回 0 因该站此分类无此标题篇 | 命中不稳时先 `search garr ""` 列全类再定位；不要退回 360  broad-web |
| **Git Bash 路径改写** | `fetch "/accounting/ent/cas/02.html"` 里以 `/` 开头的参数被 MSYS 改写成 `D:/Git/accounting/...` → 404 | 相对路径要配 `MSYS_NO_PATHCONV=1`，或直接传完整 URL（脚本对 `http` 开头不做拼接） |

## 与 chenyiwei-bbs 的分工

两个子技能并排，同属母技能 ③「库外专业口径」层，取的东西不同：

- **maodocs**：某一规范**逐条原文全文**（准则/指引第 XX 号第 X 条怎么写的）。静态站，
  确定性强（按分类索引页标题匹配），无实务解读。
- **chenyiwei-bbs**：陈版主**实务答疑**、准则被引用的问答、监管处罚案例；准则原文
  只是它的附带能力（`/api/public/ref/{slug}`），且 `slug` 需从检索里取、不能拼。

同一篇企业会计准则，两边都能给条文。**取"原文逐条"优先走 maodocs**（静态、可复现、
不依赖第三方 API 额度）；取"实务上怎么判 / 谁被罚过 / 这条准则衍生哪些问答"走
chenyiwei-bbs。两者都不进五源、不进 ⑧ 定级，触发条件与在答案里的位置都写在母技能
`SKILL.md` ③ 与 `references/out_of_library_layers.md`。

**本技能不自行决定何时调它。**
