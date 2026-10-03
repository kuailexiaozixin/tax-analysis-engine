# 税务问题分析引擎 — 项目介绍

> **Tax Analysis Engine**（包名 `tax-analysis-engine`，原名 `tax-policy-search`）—
> 先判问题类型与缺失前提，再多源实时取证、按效力位阶与时效给依据定级，最后输出
> 带限制条件的分析结论。检索只是这道流水线的中间步骤，不是产品本身。

> **文档分工（单一事实来源）**：本 README 是**项目总览**（面向人：定位 / 架构 / 用法 / 目录）。
> Agent 的操作手册在 `SKILL.md`（面向 AI 的分析与回答规范），二者不重复描述同一细节。
> **版本号以 `SKILL.md` frontmatter 的 `version` 为唯一来源**；其余 `*.md` 为历史留档，不代表当前版本。

---

## 一、项目定位

### 目标用户

| 用户群 | 典型场景 |
|--------|---------|
| **企业主 / 财务人员** | "小微企业增值税有什么优惠？" "研发费用加计扣除比例是多少？" |
| **个体工商户** | "小规模纳税人开票限额？" "个体户需要汇算清缴吗？" |
| **代理记账 / 税务师** | "最新增值税优惠政策汇总" "金税四期风险指标有哪些？" |
| **法务 / 合规人员** | "关联交易转让定价规定" "跨境税务合规要求" |

### 核心原则

```
用户提问 → 意图识别 → 实时 API 搜索 → 格式化输出
                                    ↓
                  永远不凭训练数据直接给出税收政策答案
```

---

## 二、系统架构

```
前端  frontend/index.html（零框架单文件）
  搜索栏 · 高级筛选（范围/类型/时效/排序/来源/日期区间）
  智能引导 4 步向导 · 结果卡片 · 法规弹窗 4 标签页（原文｜官方解读｜AI 解读｜相关网页）
  地方口径省份下拉在"官方解读"页顶部（它只对该页生效，所以不放搜索栏）
        │ HTTP
        ▼
接口层  scripts/tax_server.py（Flask）
  /api/search · /api/text · /api/detail · /api/interpretations
  /api/ai-interpret · /api/web-related · /api/quick-tax-types（另含 / 与 /api/health）
        │
        ▼
编排层  tax_answer.py（判型 → 分轮检索 → 定级 → 依据分层）
        依赖 tax_analyze.py（问题类型、四根前提轴、会计口径缺口）、tax_evidence.py（位阶 × 时效评分）
        │
        ▼
检索层  tax_search.py（NPC）· tax_detail.py（详情与 DOCX 正文）
        tax_web_search.py（总局 search5）· tax_fgk.py（法规库目录与正文）
        tax_so360.py（360 site: 站内）· tax_shui5.py（税屋）· tax_wechat.py（公众号）
        tax_browser.py（复用本机浏览器过 WAF）
        tax_aggregator.py（五源并发聚合）
        旁路：tax_formatter.py 只把检索路的原始 JSON 排成 markdown，主线的四段式
              答案由上面那层的 tax_answer._print_answer 打印，两条路不共用模板
        │
        ▼
数据源  flk.npc.gov.cn        法律 · 行政法规 · 司法解释（官方 API）
        www.chinatax.gov.cn   总局公告与法规库 fgk.chinatax.gov.cn（search5 JSON）
        m.so.com              site: 检索，定位省局、财政部、国务院等站点
        www.shui5.cn          税屋：实务解读、专栏、答疑（正文可取）
        mp.weixin.qq.com      微信公众号（经搜狗微信检索）
        bbs.auditdog.cn       陈版主实务答疑、监管处罚、准则衍生问答（子技能，补专业口径）
        docs.maoyanqing.com   审计文库 MaoDocs：会计/审计/内控/评估/证券规范逐条原文（子技能）
        www.szse.cn           深交所法律规则：交易所自律规则/规章目录与原文定位（子技能）
```

### 代码构成

| 层级 | 文件 | 职责 |
|------|------|------|
| **分析层** | `tax_analyze.py` | 9 类问题判定 + 四根前提轴识别 + 会计口径缺口识别（`accounting_gap` / `accounting_note`）+ 立法过程文件识别（`legislative_stage` / `legislative_note`，问的是草案时说明库取不到草案）；都不联网 |
| **分析层** | `tax_evidence.py` | 依据效力位阶与时效定级、主依据挑选 |
| **分析层** | `tax_answer.py` | 判型→分轮检索→定级→依据分层的编排层 |
| **检索词整形** | `tax_terms.py` | 纯字符串规则，不联网：选项剥离、点名文件抽取、标题候选、文号抽取、标题字符命中率排序、**点名文件同一性判定**（`title_identity`：文种冲突即否，其余按核心标题互含或漏字缩略认"就是这一份"） |
| **核心搜索** | `tax_search.py` | 32 项税种与专题映射（含依据源路由）、NPC API 调用、意图识别、缓存、**NPC 串行闸**（跨进程文件锁，`NpcSerialGate`） |
| **浏览器** | `tax_browser.py` | 本机已装浏览器探测 + 过 WAF + 导 cookie（不装内核） |
| **法规详情** | `tax_detail.py` | NPC 详情 API、DOCX 下载、全文提取、章节解析；详情元数据磁盘缓存（**默认开**、TTL 1h、命名空间 `detail`）；与 `tax_search` **共用同一把** NPC 串行闸 |
| **Web 搜索** | `tax_web_search.py` | 税务总局 search5 JSON 检索（含 fgk 法规库） |
| **站内搜索** | `tax_so360.py` | 360 `site:` 检索，定位省局与地方文件；识别"访问异常出错"拦截页并回报 |
| **法规库目录与正文** | `tax_fgk.py` | 税务总局法规目录，`--body` 取正文、`--cache` 缓存清单（TTL 1h、正文不缓存）、翻页按需自适应（`--pages` 显式指定则关自适应） |
| **统一请求层** | `tax_http.py` | 全项目对外 HTTP 请求的**唯一出口**；`verify` 设计成**必填参数**（`tax_detail`/`tax_search` 用默认关闭的 `VERIFY_SSL`，`tax_fgk` 用 requests 默认的 `True`，给默认值等于悄悄改行为）。各自维护 Session / WAF 应对的 `tax_shui5`、`tax_so360`、`tax_wechat`、`tax_web_search` 在白名单内，其余脚本直接发请求会被 `test_http_layer.py` 拦下。同层另有 `short_reason()`：把取数失败的裸异常压成一句能进界面的话（抹链接、压平折行、丢 `(Caused by ...)` 嵌套、留异常类名），`tax_so360` / `tax_web_search` / `tax_fgk` 的"请求失败："都走它 |
| **共享缓存** | `tax_cache.py` | 缓存逻辑的**唯一实现**（`tax_search` / `tax_fgk` / `tax_detail` 三处共用）；按条目里的 `_ns` 字段分命名空间，各自的 `--cache-clear` 互不越界；只缓存清单 / 元数据，**正文永不缓存** |
| **离线目录同步器** | `tax_sync.py` | 两种同步外壳。**`Synchronizer`**（单资源直链 diff）：`locate()` 现抓直链 → 与 `state.json` 记录的 URL 比对 → 变了才下载 → 下载内容少于 `MIN_CONTENT_BYTES` 判异常页不覆盖旧产物 → 内容 SHA1 未变不白烧构建 → 构建回调返回的 `版本日期` 采纳进 state（现用于 `subskills/tax-preference`）。**`ListSynchronizer`**（分页列表集合 diff）：`fetch_page(page)` 按 `total` 爬全 → 对每行 `sig_fields`（如 url+时效性+文号+标题）算集合 SHA1 → 任一字段变（含时效性翻转）才重建；`--check` 只探首页比 total；空集合与爬不满 total（`max_pages` 截断）都判异常不覆盖。下载/请求只走 `tax_http` |
| **政策文件库清单源** | `tax_gov_list.py` | chinatax.gov.cn `getFileListByCodeId`（plain HTTP POST，无 cookie、不调模型）分栏目分页清单：每行规范化出 发文字号/效力等级/**时效性**/成文日期/税费类型/官方 url。同步复用 `tax_sync.ListSynchronizer`；`lookup`/`stats` 离线查；`seed-cache` 把文号（按 `tax_terms.doc_number_of` 归一）→ 官方 url（过 `tax_cited.is_official`）批量种进文号缓存，让 `locate_cited_document` 离线命中零网络；`missing` 反向列出清单里有官方 url、文号却未进缓存的待补条目（纯离线，只认官方域） |
| **文号链接缓存** | `tax_cited.py` | `is_official(url)`：只认 `chinatax.gov.cn` 及任意子域（用 `urlparse().hostname` 比后缀，防"官方域塞进路径"的假链接），拒商业站；`get/put_cited_link`：文号→已核实官方链接的持久缓存（`~/.cache/.../cited_links.json`），非官方域一律不落。`tax_answer.locate_cited_document` 命中缓存时零网络、`_from_cache=True` |
| **实务解读** | `tax_shui5.py` | 税屋：360 检索 + 浏览器过 WAF 后 HTTP 连读 |
| **实务解读** | `tax_wechat.py` | 微信公众号：搜狗微信 + 移动 UA |
| **多源聚合** | `tax_aggregator.py` | 五源并发、Jaccard 去重、权威度排序、源级失败上抛、跨源时间序 |
| **输出格式化** | `tax_formatter.py` | **检索路**的渲染器：stdin 收 `tax_search` / `tax_aggregator` 的原始 JSON，stdout 出四段式 Markdown。主线不经过它——主线的答案骨架是 `tax_answer.compose` 产出、`tax_answer._print_answer` 打印，两处模板不共用。`--intent` 是检索路自己的五档意图（`tax_search.INTENTS`），只决定标题措辞，与主线九类题型（`tax_analyze.QUESTION_TYPES`）不能互换，键集合由 `test_routing_terms.py` 钉住。命令行形态：`python scripts/tax_formatter.py --intent policy_lookup < result.json`（加 `--mode aggregated` 走多源归并；stdin 的字段要求见「命令行搜索」） |
| **模型通道** | `tax_llm.py` | 外部模型调用的唯一出口：付费闸门只认环境变量、不探测本机 CLI、额度类错误分型上抛 |
| **后端 API** | `tax_server.py` | Flask 路由、法规原文提取、解读搜索、AI 解读（走付费闸门）、取数失败与空结果分离 |
| **测试** | `test_tax_search.py` | 端到端 + 离线用例：parent_law 真实存在、聚合路由与跨源时间序、拦截页判别、sort=date、答题判分与解析、分层抽样、主依据健康度分档、净贡献配对覆盖、付费闸门与分发扫描 |
| **测试** | `test_eval_set.py` | 评测集规则的离线用例：时效判档、去重键、分类表同步 |
| **测试** | `test_routing_terms.py` | 判型词表与前提轴的离线用例：选项干扰词剥离、税种归类、判型的内容分与形态分主从、量过没收的信号词不许回表、会计口径缺口的命中与不命中、缺口贯通到作答层、点名文件同一性（引用式标题不许冒充被点名的那份）、立法阶段的命中与不命中（全打桩，不联网） |
| **探针** | `probe_routing.py` · `probe_classify.py` | 只打印不断言的两份离线探针，改表前后各跑一次再 diff。路由那份额外带 `--stats`，报整份评测集的归类覆盖率；判型那份带三个指标——`--stats` 零信号兜底率（18/1001）、`--oracle` 数值选项标尺（四个选项全是数值的题该判成测算，实测 100/298，判据只吃选项不吃题干，能证伪词表）、`--real` 真实提问面板（考题以外的用户原话写法） |
| **测试** | `test_tax_fgk.py` | fgk 离线用例：翻页上限与自适应收尾、文字/视频/空容器正文、缓存不含正文、命中标记与防污染（全打桩，不联网） |
| **测试** | `test_tax_sync.py` | 同步器离线用例：同链二次不重复下载、`--check` 只报不下、篡改 state.url 触发重检、下载内容过小判失败且不覆盖旧产物、直链变而内容 SHA1 相同则不重建（打桩 `tax_http.get`，不联网） |
| **测试** | `test_tax_cited.py` | 文号链接缓存用例：`is_official` 认子域、拒"官方域塞路径"与商业站；`locate_cited_document` 冷启动检索→落缓存→热命中 `_from_cache` 且不打检索；非官方域结果不入缓存（缓存指临时目录，不联网） |
| **测试** | `test_preference.py` | 减免税目录子技能用例：文号/标题抽取、AND 打分与有效加分、`build_index` 解析合成 xlsx、按代码/关键词/税种/状态离线查询；`locate()` 多入口回退（栏目页失败退首页、全失败才抛错，注入假 `tax_http`）；`query()` 官方链接标注（用全称文号+归一缓存键，去掉 `doc_number_of` 即取不到，钉住读写共键）；`build_index` 文号抽取覆盖率自检（造一行无文号形态→计数=1，规范两行→计数=0，写死计数即转红）（全用临时索引，不联网、不碰真实索引） |
| **测试** | `test_judge_validity.py` | 时效性分类状态判据用例：`部分失效/部分废止/部分无效` 判 effective（回归锁，防被子串"失效"压成 repealed）、`全文有效/全文废止/已修改/尚未生效` 各归位、`--at` 时点分支与制定依据援引不被破坏（全离线） |
| **测试** | `test_gov_list.py` | 分页列表同步器与清单源用例：按 `total` 停止爬全、`--check` 只探首页、集合未变不重建、**时效性翻转触发重建**、空集/截断守卫；`normalize_item` 按 `key` 跨分组取元数据；`seed_cited_cache` 官方域种入、非官方/空文号跳过、`only_missing` 不覆盖已有；`missing_cited` 只列未缓存的官方域文号、`limit` 截断（注入假 fetch_page 与临时缓存，不联网） |
| **测试** | `test_npc_gate.py` | NPC 串行闸用例：跨进程互斥、同进程多线程排队、超时后不锁死、接线检查 |
| **测试** | `test_detail_cache.py` | 详情缓存用例：键算法与历史缓存逐字节一致、命名空间不越界、命中留痕、老条目读时转正、缓存不含条文正文（红线）、原子写不留半截文件 |
| **测试** | `test_server_routes.py` | 服务端 9 路由用例：状态码与错误码、分支路由（npc/chinatax/fgk/aggregated）、付费闸门关着时不许碰模型、上游报错如实透出不吞成空结果、会计口径与立法阶段两条提示都按用户原话判定而非按改写后的检索词、全网那一趟在真实边界（`so360_search`）打桩断言 1/2/3 条都能到载荷（全打桩，不联网） |
| **测试** | `test_http_layer.py` | 统一请求层守门：AST 扫全项目、除白名单外禁止绕过 `tax_http` 的裸请求（含别名写法）、参数只透传不补全、`verify` 必填、取数失败那句错误说明的整形（丢链接与 `(Caused by ...)`、压平折行、截断不切词） |
| **测试** | `test_frontend_province_ui.py` | 前端省份控件契约：控件长在"官方解读"页而非搜索栏、高级筛选恰为 6 组、搜索请求不带 `province`、结果头不再显示省市标签、前端站点清单与 `tax_server.search_interpretations` 的默认 sources 逐字一致（读文件断言，不开浏览器） |
| **测试** | `test_frontend_aging_control.py` | 前端时效控件值域契约：`AGING_S5` 五档与后端 `tax_web_search.AGING_VALUES` 逐项一致、`AGING_FROM_NPC` 换出的文本必落在值域内、`syncFilterControls` 不再 `disabled`、拼 option 的两项数组不许写成空项（真浏览器里读到过 `undefined` 选项）、`filterStatus` 赋值只走 `setNpcStatus`、`doSearch` 按 `s5` 分流 `status`/`aging`（读文件断言，不开浏览器） |
| **测试** | `test_source_defects.py` | 源缺陷回归：fgk 深页标 `medium`、聚合缺口归因（税屋空归到 360）、网页渲染 `degraded_note`、定级层认 `_reliability`（low 出局 / 全 low 拒挑主依据 / 无标记口径不变）、渲染层不吞标记（单源也会印出"可能偏题"）、搜狗串行闸与最小间隔（并发峰值 == 1）、NPC 下载单一路径收口、**返回值契约**（AST 扫全项目：声明 `-> tuple[A, B]` 的函数不许 `return` 单值） |
| **门禁** | `check_doc_cli.py` | 文档-代码契约，两类检查：**命令级**（同行里点了脚本名的命令行，逐参数比对代码的参数表）+ **片段级**（孤立的 `` `--a --b` `` 必须能落在某一个脚本上），挡住"文档写了、代码没有"与"参数挂错脚本" |
| **测试** | `test_check_doc_cli.py` | 文档契约规则自检：构造样例钉住片段级检查既能报出坏片段、也不误报好片段。规则一旦改对就永远绿，没有自检就无法证明它还在工作 |
| **门禁入口** | `run_all.py` | 统一测试入口：默认跑离线组，`--online` 加联网组；退出码可直接接 CI（根目录 `run_tests.bat` 双击即跑） |
| **评测集** | `build_eval_set.py` | 归并公开财税题库为带出处与时效标记的统一评测集，按 SCOPE_EXCLUDE 剔除范围外题目 |
| **评测（主指标）** | `eval_answer.py` | 答题正确率：模型在环逐题判分，evidence/blind 双组对照；计费预告 + yes 确认 + 额度耗尽停批 |
| **评测（诊断）** | `eval_analysis.py` | 分析质量四指标 |
| **评测（诊断）** | `eval_retrieval.py` | 检索质量：路由覆盖 + 题库覆盖两级指标 |
| **技能定义** | `SKILL.md` | 分析主线与禁止行为清单 |
| **参考文档** | `references/*.md` | SKILL.md 主线判据的细节下沉：各源命令、输出模板、依据定级、税种映射、源缺陷、网页界面、评测方法、判型指标，另含搜索策略与风险框架 |
| **前端** | `frontend/index.html` | 搜索界面 + 4 步向导 + 法规弹窗四标签页 |

---

## 三、核心功能模块

### 1. 实时法规检索

```
用户输入问题 → 9 类题型判定 → 四根前提轴识别 → 32 项税种与专题路由（按 authority 分派到 NPC 或总局）→ 依据定级与分层 → 分段作答
```

| 功能 | 说明 |
|------|------|
| **标题搜索** | 默认入口。NPC 的正文范围已按相关度排序，可定位法规但会分词偏题 |
| **精确/模糊匹配** | 知道法规名用精确（`--exact`），宽泛主题用模糊（模糊结果会按标题相关度重排） |
| **时效性过滤** | 默认仅查"现行有效"，支持筛选已废止/已修改/尚未生效 |
| **日期范围过滤** | 支持按公布日期范围筛选（如"2024 年以来的政策"） |
| **排序** | 按相关度 / 按公布日期降序 |
| **分页** | 每页最多 100 条（默认 20），NPC API 也返回命中总量 |

### 2. 意图识别引擎

| 意图 | 触发信号 | 搜索策略 |
|------|---------|---------|
| 📖 **政策查询** | 多少、比例、最新、规定 | 标题精确 → 标题模糊 → chinatax |
| 📋 **申报指导** | 申报、汇算清缴、截止 | 标题搜法规 + chinatax 操作指南 |
| ⚠️ **合规风险** | 风险、金税、会被查 | 搜承载该风险的法规 + 指标框架 |
| 🎁 **资格判定** | 能不能享受、符合条件 | 精确搜索政策名 + chinatax 条件解读 |
| 🧾 **发票处理** | 开票、红冲、遗失、抵扣 | "发票管理办法"精确 + chinatax 补充 |

### 3. 智能引导面板（4 步向导）

```
Step 1: 选择身份  →  企业 / 个人 / 代理记账 / 其他
Step 2: 选择税种  →  12 个税种卡片（增值税、企业所得税、个税等）
Step 3: 选择意图  →  查政策 / 优惠资格 / 风险自查 / 申报 / 发票 / 计算
Step 4: 补充条件  →  纳税人类型(小规模/一般/小微/高新) + 时效 + 自定义关键词

点击"生成搜索" → 自动拼接精准关键词 + 设置筛选条件 → 一键搜索
```

### 4. 法规弹窗（4 Tab 式）

| Tab | 功能 | 数据来源 |
|-----|------|---------|
| 📖 **法规原文** | 全文展示 + 搜索关键词黄色高亮 + 章节/法条自动分类 | NPC API → DOCX 实时下载解析 |
| 🔍 **官方解读** | 多源搜索官方政策解读、答记者问、立法说明 | 360 站内检索 chinatax/mof/gov.cn |
| 🤖 **AI 解读** | 用通俗语言解读法规（适用主体/核心要点/注意事项）。默认关闭，需显式开启付费闸门 | 使用者自配的模型 CLI |
| 🌐 **相关网页** | 行业分析、学术评论、律师解读等补充视角 | 税屋 shui5.cn + 微信公众号 |

### 5. 金税四期风险指标框架

| 指标类别 | 数量 | 搜索关键词示例 |
|---------|------|--------------|
| 增值税风险 | ~32 项 | 虚开发票、骗取留抵退税、进销项不匹配 |
| 企业所得税风险 | ~28 项 | 两税收入差异、长亏不倒、税负率异常 |
| 个人所得税风险 | ~12 项 | 多处所得未合并、专项附加扣除异常 |
| 开票经济风险 | ~36 项 | 暴力虚开、票货分离、资金回流 |
| 资金流风险 | ~12 项 | 私户收款、账外经营、资金闭环 |
| 关联交易风险 | ~10 项 | 转让定价、资本弱化、同期资料 |
| 跨境税务风险 | ~12 项 | 境外所得申报、常设机构判定 |
| 特殊行业风险 | ~14 项 | 建筑挂靠、医药 CSO、电商刷单 |

> 以上均为搜索提示词，实际指标以实时搜索 五源（NPC / 税务总局 / 360 / 税屋 / 微信公众号）结果为准。

### 6. 安全约束与合规

- 每条回答包含实时查询时间戳和数据来源标注
- 禁止使用训练数据直接回答税收政策问题
- 禁止给出具体金额的申报建议
- 禁止预测稽查结果
- 禁止引用已废止法规而不标注
- 所有回答强制附加免责声明

---

## 四、API 接口一览

### 后端端点

| 方法 | 路径 | 功能 |
|------|------|------|
| `POST` | `/api/search` | 搜索税收法规（NPC API） |
| `GET` | `/api/text/<id>` | 获取法规全文（DOCX 解析） |
| `GET` | `/api/detail/<id>` | 获取法规元数据 |
| `GET` | `/api/interpretations/<id>?keyword=` | 搜索官方政策解读（360 多源） |
| `GET` | `/api/ai-interpret/<id>?keyword=` | AI 生成通俗解读（经付费闸门，默认 503） |
| `GET` | `/api/health` | 服务状态 + 付费闸门状态（`paid_llm.enabled`） |
| `GET` | `/api/quick-tax-types` | 获取 12 个快捷税种列表 |

### 搜索 API 请求示例

```json
POST /api/search
{
  "keyword": "增值税",
  "scope": "title",
  "exact": false,
  "status": 3,
  "sort": "relevance",
  "source": "npc",
  "date_from": null,
  "date_to": null,
  "aging": ""
}
```

### 搜索 API 响应示例

```json
{
  "keyword": "增值税",
  "intent": "policy_lookup",
  "intent_label": "政策查询",
  "tax_type": "增值税",
  "tax_type_aliases": ["增值税", "VAT", "进项税", ...],
  "result": {
    "total": 45,
    "keyword": "增值税",
    "scope": "title",
    "search_type": "fuzzy",
    "searched_at": "2026-06-29 15:30:00",
    "_from_cache": false,
    "results": [
      {
        "id": "ff808181927b083b...",
        "title": "中华人民共和国增值税法",
        "publish_date": "2024-12-25",
        "effective_date": "2026-01-01",
        "status_code": 3,
        "status": "现行有效",
        "issuing_authority": "全国人民代表大会常务委员会",
        "category": "法律"
      }
    ]
  }
}
```

### 法规全文 API 响应示例

```json
GET /api/text/ff808181927b083b0193fd65a0eb02cb

{
  "detail": {
    "title": "中华人民共和国增值税法",
    "publish_date": "2024-12-25",
    "effective_date": "2026-01-01",
    "status_code": 3,
    "status": "现行有效"
  },
  "total_paragraphs": 118,
  "article_count": 38,
  "sections": [
    {"type": "body", "text": "中华人民共和国增值税法"},
    {"type": "body", "text": "（2024年12月25日第十四届全国...）"},
    {"type": "chapter", "text": "第一章 总则"},
    {"type": "article", "text": "第一条 为了健全有利于高质量发展...", "chapter": "第一章 总则"},
    ...
  ]
}
```

---

## 五、数据源与时效性

| 数据源 | 覆盖范围 | 权威度 | 更新频率 | 访问方式 |
|--------|---------|--------|---------|---------|
| **NPC 国家法规库** | 法律、行政法规、地方法规、司法解释。不收国际税收专题与税收征管细则 | ⭐⭐⭐⭐⭐ | 实时同步 | `POST flk.npc.gov.cn` API |
| **www.chinatax.gov.cn** | 国家税务总局公告、政策解读、操作指南 | ⭐⭐⭐⭐ | 实时 | search5 JSON API |
| **mof.gov.cn** | 财政部公告、财税联合发文 | ⭐⭐⭐⭐ | 实时 | 360 `site:` 搜索 |
| **fgk.chinatax.gov.cn** | 税务总局法规库结构化内容。国际税收、转让定价、税务行政处罚的依据都在这里 | ⭐⭐⭐⭐ | 实时 | search5 JSON API 翻页（每页固定 10 条）+ 详情页 |
| **gov.cn** | 国务院政策发布、答记者问 | ⭐⭐⭐⭐⭐ | 实时 | 360 `site:` 搜索 |
| **税屋 shui5.cn** | 实务解读、专栏、答疑（正文可取） | ⭐⭐⭐ | 实时 | 360 检索 + 浏览器过 WAF 后连读 |
| **微信公众号** | 实务解读、申报实操 | ⭐⭐ | 实时 | 搜狗微信 + 移动 UA |
| **bbs.auditdog.cn**（子技能 chenyiwei-bbs） | 陈版主实务答疑、监管处罚案例、准则衍生问答，附带企业会计准则原文。补的是专业口径那一层，不答税法 | 专业口径 ⭐⭐⭐⭐；税收依据 无 | 上游每日更新（子技能文档记载，未实测） | 公开 REST API，`curl` 直连，无 key；操作手册在 `subskills/chenyiwei-bbs/` |
| **docs.maoyanqing.com**（子技能 maodocs） | 审计文库 MaoDocs：企业/政府/小企业会计准则、注协审计准则、企业内控规范、资产评估准则、证监会监管规则适用指引的**逐条原文全文**。补专业口径原文，不答税法 | 专业口径 ⭐⭐⭐⭐；税收依据 无 | 实时（sitemap lastmod 至 2026-09-28） | 纯标准库直连静态站，无 WAF、无浏览器、无 key；脚本与手册在 `subskills/maodocs/` |
| **www.szse.cn**（子技能 szse-lawrules） | 深交所「法律规则」栏目：法律/行政法规/司法解释/证监会规章·指引·规范性文件/废止公告＋十二类"本所业务规则"自律规则的**目录与原文定位**。补证券交易场所口径，不答税法 | 专业口径 ⭐⭐⭐；税收依据 无 | 实时（当日枚举 724 条入索引） | 先 `build` 建本地索引再离线 `query`；标准库直连为主，业务规则通道复用 `scripts/tax_browser`，无 key、不调模型；脚本与手册在 `subskills/szse-lawrules/` |
| **减免税政策代码目录**（子技能 tax-preference） | 总局《减免税政策代码目录》xlsx：现行有效/已失效两栏、8 位减免性质代码、收入种类·政策大类、文号、优惠条款。**税收优惠的权威封闭枚举**——按税种/大类列全某主体名下有哪些现行优惠，是五源关键词召回凑不出的 | 税收依据 ⭐⭐⭐⭐（带文号，回 ⑧ 定级） | 官方每月更新（国家税务总局公告 2015 年第 73 号）；本地 `sync` 抓直链比版本 | 先 `sync` 下载重建 `preference_index.json`，再离线 `query`/`list-types`；同步复用 `scripts/tax_sync.py`，plain HTTP、无 key、不调模型；脚本与手册在 `subskills/tax-preference/` |
| **研发费用加计扣除与高企认定合规**（子技能 rd-deduction-hitech） | 研发费用税务合规的**领域分析知识库**（非抓取源）：高企认定评分自检（八条一票否决 + 四项指标 71 分达标）、加计扣除归集与测算（六类费用/其他费用 10% 限额/委托研发 80%/境外 2/3/负面清单）、四套口径差异协同、2026 穿透式监管与稽查应对、内控制度/流程/表单模板与加计/高企/IPO 三套资料包。补五源给不了的**规则计算与模板产出**那一层 | 领域规则 ⭐⭐⭐⭐（以官方政策文件为基础，另含公开稽查案例）；税收依据 无（引用政策原文时仍回 ⑧ 定级） | 静态随技能提供，政策更新时人工维护 | 离线直接使用，**不连接任何业务系统、不读写数据库、不调模型**；数字一律来自用户提供或企业台账，缺失列待补不虚构；手册与规则在 `subskills/rd-deduction-hitech/` |
| **chinatax.gov.cn 政策文件库清单**（`scripts/tax_gov_list.py`） | 各栏目（税务规范性文件/财税文件/法律/行政法规/其他）的**封闭分页清单**，每条自带发文字号·效力等级·**时效性分类状态**·成文日期·税费类型·官方 url。补 search5 关键词召回给不了的"某栏目全部现行文件"横截面，也是 ⑧ 时效判定的官方时效性字段来源 | 税收依据 ⭐⭐⭐⭐（带官方 url，回 ⑧ 定级） | 集合 diff（`tax_sync.ListSynchronizer`：按 url+时效性+文号+标题算集合 SHA1，时效性翻转会检出重建） | `sync [--channel 名]` 抓全建 `data/sync/chinatax-list/`，`lookup`/`stats` 离线查，`seed-cache` 批量预热文号→官方链接缓存，`missing` 出待补链接工作清单；plain HTTP POST、无 cookie、不调模型 |

### 缓存策略

默认值按数据类型分：**清单 / 检索缓存默认关**（要新鲜度），**详情元数据默认开**（要速度）。
三类都**只缓存清单 / 元数据，正文永不缓存**（避免引用到已废止 / 被修订的旧条文）。

| 数据类型 | 缓存 | 说明 |
|---------|------|------|
| NPC 法规检索结果 | 可选，**5 分钟** TTL（`tax_search.py --cache`） | 政策随时更新，默认实时 |
| 税务总局法规库清单（fgk） | 可选，**1 小时** TTL（`tax_fgk.py --cache`） | 只缓存清单（标题/文号/日期/URL）；**正文每次现拉** |
| 详情元数据 | **默认开**，**1 小时** TTL（`tax_detail.py`） | 详情接口慢、元数据变动频率低；命令行与服务端共用同一份磁盘缓存 |
| DOCX / PDF 全文 | **不缓存**，每次现下 | 体积大，且必须保证是现行版本 |
| 服务端进程内缓存 | `tax_server.py` 的 `/api/text`、`/api/interpretations` | 仅在服务运行期间复用，重启即失效；与上面的磁盘缓存是两回事 |

三个模块共用 `~/.cache/tax-analysis-engine`，靠条目里的 `_ns` 字段区分命名空间，
因此各自的 `--cache-stats` / `--cache-clear` 只作用于自己那一份。

---

## 六、与同类项目的对比

| 维度 | tax-policy-knowledge | npc-law-db | **tax-analysis-engine** |
|------|---------------------|------------|----------------------|
| 架构 | 纯静态知识库 | API + 脚本引擎 | **API + 脚本引擎 + 前端 Demo** |
| 数据新鲜度 | 静态（依赖手动更新） | 实时 API | **实时 API + 多源聚合** |
| 用户意图理解 | 关键词匹配表 | 无（通用搜索） | **AI 意图分类 + 4 步引导面板** |
| 法规原文展示 | 无 | 命令输出 | **前端 Tab 弹窗 + 关键词高亮** |
| 政策解读 | 静态问答示例 | 无 | **360 全网搜官方解读 + AI 兜底** |
| 风险指引 | 内嵌指标表 | 无 | **搜索提示框架 + 实时验证** |
| 前端界面 | 无 | 无 | **完整 Web Demo** |
| 代码量 | 0（纯 Markdown） | 检索脚本为主 | **检索/分析层 + 单文件前端，逐文件职责见「代码构成」表** |

---

## 七、技术栈

| 层级 | 技术 | 用途 |
|------|------|------|
| **后端框架** | Flask 3.1 | REST API 服务 |
| **HTTP 客户端** | requests + urllib3 | NPC API、税务总局 search5、360、搜狗、Jina Reader |
| **文档解析** | Python stdlib (zipfile + ElementTree) | DOCX → 纯文本提取（零依赖） |
| **并发搜索** | concurrent.futures (ThreadPoolExecutor) | 多源并行查询 |
| **搜索引擎** | 360 (m.so.com) HTML 解析 | .gov.cn 与地方站点检索 |
| **前端** | 纯 HTML/CSS/JS (零框架) | 搜索界面 + 引导面板 + 法规弹窗 |
| **AI 解读** | 外部模型 CLI（subprocess，经付费闸门） | 法规通俗化解读生成；默认关闭，需显式设置 `TAX_ENABLE_PAID_LLM=1` 与 `TAX_LLM_CMD` |
| **测试** | Python unittest 模式 | 离线用例（拦截页判别、时效判档、去重键）+ 联网端到端用例 |
| **依赖** | requests, urllib3, flask | 仅 3 个 pip 包 |

---

## 八、快速开始

### 安装

```bash
# 1. 安装依赖
pip install flask requests urllib3

# 2. 启动服务（在项目根目录下执行）
python scripts/tax_server.py

# 3. 打开浏览器
# http://localhost:5080
```

### 外部模型调用（默认关闭）

「AI 解读」和评测的 blind 组都要问模型，而问模型花的是模型通道所属账号的额度，
不是本技能配置的通道。所以这两处默认不走：不探测本机装了哪个 CLI，只认环境变量。

```bash
# Windows（PowerShell）；其他系统把 $env: 换成 export
$env:TAX_ENABLE_PAID_LLM = "1"                 # 显式同意计费
$env:TAX_LLM_CMD = "C:\path\to\model.cmd"      # 模型 CLI 的绝对路径，必须存在
$env:TAX_LLM_TIMEOUT = "120"                   # 可选，单次调用超时秒数，默认 120
```

两个变量不设或只设其一，服务端 `/api/ai-interpret` 直接返回 503 并把原因写在响应里，
一次调用都不会发起。`/api/health` 的 `paid_llm` 字段随时可读当前开关状态。
批量评测还要在起跑前逐题算出付费调用次数并要求输入 `yes`（或传 `--yes`），
中途遇到额度类报错（HTTP 402、insufficient balance、余额不足等）立即停止整批并取消排队任务。

### 命令行搜索

```bash
# 政策查询
python scripts/tax_search.py "增值税" --status 3 --size 20

# 标题检索 + 按日期排序
python scripts/tax_search.py "加计扣除" --sort date

# 精确查找
python scripts/tax_search.py "中华人民共和国企业所得税法" --exact

# 获取法规详情
python scripts/tax_detail.py --info <法规ID>

# 多源聚合搜索
python scripts/tax_aggregator.py "小微企业优惠" --size 10

# 搜索官方解读
python scripts/tax_web_search.py "增值税" --size 10

# 把检索结果 JSON 格式化成 Markdown（stdin 收 JSON、stdout 出 markdown）
# 只给检索路用：主线（tax_answer.py --answer）自己打印四段式，不经这一步
python scripts/tax_formatter.py --intent policy_lookup < result.json
```

`tax_formatter.py` 的 stdin 需要一份带下列字段的 JSON：`keyword` / `total` 必填，
`scope` 与 `search_type` 会原样印在"搜索范围"那行，`results` 里每条至少要有
`title` 与 `id`（其余字段留空即不显示）：

```json
{
  "keyword": "增值税",
  "total": 1062,
  "scope": "全文模糊",
  "search_type": "现行有效",
  "results": [
    {
      "title": "中华人民共和国增值税法",
      "id": "abc123",
      "status_code": 3,
      "publish_date": "2024-12-25",
      "effective_date": "2026-01-01"
    }
  ]
}
```

加 `--mode aggregated` 则改吃多源结构：`keyword` + `npc` 对象 + `chinatax` / `so360` /
`shui5` / `wechat` 四个数组（后三个数组里的条目用 `_source` 标来源）。两种模式的输入输出
都实测跑通、退出码 0。

### 作为技能使用

把 `SKILL.md` 所在的目录放进技能目录即被自动发现。对话中直接提问：

```
"小微企业增值税有什么优惠？"
"研发费用加计扣除比例是多少？"
"金税四期有哪些风险指标？"
```

宿主会激活本技能，按 SKILL.md 的主线实时检索国家法规库与税务总局后再回答。

---

## 九、项目目录结构

```
tax-analysis-engine/
├── SKILL.md                        # 分析主线：判型 → 分轮检索 → 定级 → 比对 → 结论 → 输出
├── scripts/                        # 检索与分析模块（逐个职责见「代码构成」表）
│   ├── tax_answer.py               # 编排层：判型→分轮检索→定级→依据分层
│   ├── tax_analyze.py              # 问题类型判定、四根前提轴、会计口径缺口、立法阶段识别
│   ├── tax_terms.py                # 检索词整形：选项剥离 / 点名文件抽取 / 标题候选 / 标题同一性判定（纯字符串，不联网）
│   ├── tax_evidence.py             # 依据效力位阶与时效定级
│   ├── tax_search.py               # NPC 法规库检索 + 32 项税种专题映射 + NPC 串行闸
│   ├── tax_detail.py               # 详情元信息 + DOCX 全文解析（共用同一把串行闸）
│   ├── tax_web_search.py           # 税务总局 search5 JSON 检索
│   ├── tax_so360.py                # 360 site: 站内检索（含拦截页识别）
│   ├── tax_fgk.py                  # 税务总局法规库目录与正文（--body 取正文 / --cache 缓存 / 翻页自适应）
│   ├── tax_cache.py                # 缓存唯一实现（tax_search / tax_fgk / tax_detail 三处共用；清单默认关、详情默认开；正文不缓存）
│   ├── tax_http.py                 # 统一请求层：对外 HTTP 的唯一出口（verify 必填）
│   ├── tax_shui5.py                # 税屋检索与正文
│   ├── tax_wechat.py               # 微信公众号（搜狗微信）
│   ├── tax_browser.py              # 复用本机浏览器过 WAF、导 cookie
│   ├── tax_aggregator.py           # 五源并发聚合（源级失败上抛）
│   ├── tax_formatter.py            # 检索路的四段式 Markdown 渲染（stdin 收原始 JSON；主线输出在 tax_answer）
│   ├── tax_llm.py                  # 外部模型调用的唯一出口：付费闸门 + 额度异常分型
│   ├── tax_sync.py                 # 离线同步外壳：Synchronizer（单资源直链 diff）+ ListSynchronizer（分页列表集合 diff）
│   ├── tax_cited.py                # 文号→官方链接持久缓存 + is_official 官方域白名单（纯本地，不联网）
│   ├── tax_gov_list.py             # 政策文件库清单源（getFileListByCodeId 分页）：sync/lookup/stats/seed-cache/missing
│   └── tax_server.py               # Flask API + 前端托管
├── frontend/index.html             # 单文件界面：搜索栏 / 4 步向导 / 结果卡片 / 法规弹窗四标签页
├── subskills/
│   ├── chenyiwei-bbs/
│   │   ├── SKILL.md                # 陈版主实务答疑与准则原文（上游文件，与 https://bbs.auditdog.cn 字节一致）
│   │   └── NOTE.md                 # 收录来源、SHA-256、实测校正（docNo 不表版次、recent 可空、分页字段类型不一）
│   ├── maodocs/
│   │   ├── SKILL.md                # 审计文库 MaoDocs 原文检索：会计/审计/内控/评估/证券五域逐条原文
│   │   ├── NOTE.md                 # 来源、sitemap 分布、实测校正（文号不表版次、索引页混挂全局导航、Git Bash 路径改写）
│   │   └── maodocs.py              # 纯标准库取文脚本（categories/search/fetch，无 WAF、无浏览器、不调模型）
│   ├── rd-deduction-hitech/
│   │   ├── SKILL.md                # 研发费用加计扣除与高企认定合规分析：高企评分自检/加计归集测算/四套口径协同/2026监管与稽查应对/内控模板与三套资料包
│   │   ├── NOTE.md                 # 技能说明：目录结构、硬约束（无系统数据库、政策时效检查、模板路径基准）
│   │   └── references/             # 评分规则·扣除指南·稽查应对·政策文件库(30)·加计书(10)·IPO书(22)·内控模板(34)
│   ├── szse-lawrules/
│       ├── SKILL.md                # 深交所法律规则目录检索（查询式：build 建索引后离线 query）
│       ├── NOTE.md                 # 来源、两种渲染形态、实测校正（t 编号不表版次、业务规则需浏览器、多为 PDF 附件）
│       └── szse.py                 # categories/build/query/fetch；标准库直连为主，业务规则通道复用 scripts/tax_browser，不调模型
│   └── tax-preference/
│       ├── SKILL.md                # 减免税政策代码目录（查询式：sync 建索引后离线 query，是税收依据本体、进 ⑧ 定级）
│       ├── NOTE.md                 # 一次 sync 实测、双栏日期填充率、getFileListByCodeId 发现（未接入）、复用关系
│       └── preference.py           # sync/query/list-types；locate 多入口回退、query 标注缓存官方链接；同步复用 scripts/tax_sync.py，plain HTTP、不调模型
├── references/                     # tax_categories · search_strategies · tax_risk_framework
├── tests/
│   ├── run_all.py                  # 统一门禁入口（默认离线组；--online 加联网组）
│   ├── test_tax_search.py          # 检索与接口用例（离线组 + 联网组）
│   ├── test_tax_fgk.py             # fgk 离线用例：翻页/正文/缓存（全打桩，不联网）
│   ├── test_npc_gate.py            # NPC 串行闸用例（含跨进程互斥）
│   ├── test_detail_cache.py        # 详情缓存用例：键一致 / 命名空间不越界 / 原子写（全打桩）
│   ├── test_server_routes.py       # 服务端路由用例：9 个路由的状态码与分支、两条提示按原话判定、全网那趟在真实边界打桩（全打桩，不联网）
│   ├── test_http_layer.py          # 统一请求层守门：AST 扫全项目、禁止绕过 tax_http 的裸请求、错误说明整形
│   ├── test_frontend_province_ui.py # 前端省份控件契约：控件在哪 / 筛选组数 / 站点清单与后端一致
│   ├── test_frontend_aging_control.py # 前端时效控件值域契约：五档与后端一致 / option 两项齐全 / 赋值只走共享入口
│   ├── test_source_defects.py      # 源缺陷回归：深页标记 / 缺口说明 / 可靠性否决 / 搜狗闸与下载收口 / tuple 返回值契约
│   ├── test_eval_set.py            # 评测集规则的离线用例：时效判档、去重键、分类表同步
│   ├── test_routing_terms.py       # 判型与词表用例：选项干扰剥离 / 会计口径缺口 / 点名文件同一性 / 立法阶段 / 意图词表三处对齐
│   ├── probe_routing.py            # 路由探针（离线，不断言）：题面 → 归到哪个税种、装配出什么检索词，改表前后各跑一次 diff 改判条数
│   ├── probe_classify.py           # 判型探针（离线，不断言）：--stats 零信号兜底率 / --oracle 数值选项标尺 / --real 真实提问面板
│   ├── check_doc_cli.py            # 文档-代码契约：SKILL.md / README.md 里的参数 vs 代码 add_argument
│   ├── test_check_doc_cli.py       # 文档契约规则自检：坏片段必须被报、好片段必须放行
│   ├── build_eval_set.py           # 归并公开财税题库 → 统一评测集
│   ├── eval_answer.py              # 主指标：答题正确率（模型在环，双组对照）
│   ├── eval_analysis.py            # 诊断：分析质量四指标
│   ├── eval_retrieval.py           # 诊断：检索质量两级指标
│   └── analysis_labels.json        # 分析题目标注集
├── requirements.txt
├── start_local.bat                 # 启动本地服务（端口 5080）
└── run_tests.bat                   # 一键门禁（双击即跑，等价 python tests/run_all.py）
```

（`tests/results/` 是评测运行的输出目录：依据缓存、模型输出缓存与 `--out` 结果都落这里。目录在需要时**自动创建**，已进 `.gitignore`、不入库，所以仓库里看不到它。）

评测集原始数据不入仓库，放在 `../eval_data/`：FinanceIQ 与 FinEval 是 CC BY-NC-SA-4.0，
IDEAFinBench 上游没有 LICENSE 文件，再分发前得先找上游确认。构建命令从那里读、也写到那里：

```bash
python tests/build_eval_set.py --data-dir ../eval_data --report
```

---

## 十、设计决策记录

| # | 决策 | 选择 | 理由 |
|---|------|------|------|
| 1 | 主数据源 | NPC 国家法规库 API | 权威性最高，覆盖法律/行政法规/司法解释，实测 14 个税种全部可查且本体法排在首位 |
| 2 | 数据新鲜度策略 | 默认无缓存，每次实时查询 | 税收政策随时更新，缓存可能返回过时数据 |
| 3 | 搜索策略 | 标题精确 → 标题模糊（重排）→ 正文（重排）→ chinatax | NPC 标题与正文两种检索都按发布时间返回，必须本地重排 |
| 4 | 默认时效筛选 | 仅现行有效（sxx=3） | 用户 99% 不需要看已废止的法律 |
| 5 | 官方解读搜索 | 税务总局站内 search5 + 360 移动版 `site:` 检索 | 税务总局接口覆盖 fgk/chinatax；360 是实测能返回真实 URL 的发现通道，Bing 与百度均已弃用 |
| 6 | AI 解读引擎 | 使用者自配的外部模型 CLI，默认关闭 | 技能会被分发出去，任何"自动发现本机已装的模型命令"都会让接收方在不知情时产生调用费用；改为只认环境变量、不设默认，闸门未开时接口返回 503 |
| 7 | DOCX 解析 | Python stdlib (zipfile+ET) | 零 pip 依赖，不需要 python-docx |
| 8 | 前端技术 | 纯 HTML/CSS/JS 零框架 | 单文件部署，无构建步骤 |
| 9 | 技能格式 | SKILL.md + frontmatter | 宿主自动发现和激活，不需要额外注册步骤 |
| 10 | 安全约束 | 禁止行为清单 + 强制免责与时间戳 | 税收领域法律风险高，必须限制 AI 自由发挥；条目见 SKILL.md ⑦ |
| 11 | 模型调用计费 | 默认关闭，双环境变量显式开启 | 见「八、快速开始 → 外部模型调用」；判定集中在 `tax_llm.channel()`，两处调用点共用 |

---

## 十一、测试覆盖

```bash
python tests/run_all.py            # 离线门禁：全部离线用例，几秒出结果
python tests/run_all.py --list     # 列出当前有哪些用例（权威清单，别手抄进文档）
python tests/run_all.py --online   # 追加联网 e2e（慢，且受对方限流影响）
```

上面三条就是入口。**用例清单以 `--list` 的输出为准**，本节不再逐字重抄文件名——
文档里手抄的清单已经漂移过一次，机器生成的不会。

`test_tax_search.py` 把用例注册成一张名字表，逐条跑、逐条回显，按是否打真实站点分两类：

- **离线组**不联网，把外部输入喂成固定桩，因此结果确定。覆盖问题类型判定、前提轴识别、
  依据位阶与时效定级、编排轮次、检索词路由、聚合精确标记与跨源时间序、服务端全源路由、
  模型答案解析、付费闸门（默认关闭的三种回绝原因、额度类与本机故障类分型、缓存失效判定、
  花费预告文案）、批量评测的配对净贡献守卫，以及一道**分发扫描**：
  扫 `tax_server.py` 与 `eval_answer.py` 的源码，出现任何写死的用户目录或某个具体 CLI 文件名
  即失败，`tax_server.py` 的 AI 解读路由必须经过 `tax_llm.channel()`，`/api/health` 必须回报闸门状态。
- **联网组**打真实数据源，数量随政策库更新变动，所以只校验结构与判据（首条是否本体法、
  时效字段是否齐全、拦截页是否被识别为失败而非空结果），不断言条数。

`test_eval_set.py` 覆盖评测集的时效判档、跨库去重键、范围剔除规则与税种分类表同步。

---

## 十二、许可与免责

本项目仅供税务政策查询参考，**不构成税务建议**。所有税收政策信息以以下官方来源为准：

- 国家税务总局官网：https://www.chinatax.gov.cn
- 财政部官网：https://www.mof.gov.cn
- 国家法律法规数据库：https://flk.npc.gov.cn
- 纳税服务热线：12366

涉及具体税务处理和申报操作，请咨询专业税务人员或主管税务机关。
