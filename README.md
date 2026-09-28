# 财税政策实时搜索引擎 — 项目介绍

> **Tax Policy Real-Time Search** — 通过多源实时检索，为企业和个人提供权威、准确、最新的中国税收政策法规查询与解读服务。

> **文档分工（单一事实来源）**：本 README 是**项目总览**（面向人：定位 / 架构 / 用法 / 目录）。
> Agent 的操作手册在 `SKILL.md`（面向 AI 的检索与回答规范），二者不重复描述同一细节。
> **版本号以 `SKILL.md` frontmatter 的 `version` 为唯一来源**（当前 `3.0.0`）；其余 `*.md` 为历史留档，不代表当前版本。

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
  搜索栏 · 高级筛选（范围/类型/时效/排序/来源/省份/日期区间）
  智能引导 4 步向导 · 结果卡片 · 法规弹窗 4 标签页（原文｜官方解读｜AI 解读｜相关网页）
        │ HTTP
        ▼
接口层  scripts/tax_server.py（Flask）
  /api/search · /api/text · /api/detail · /api/interpretations
  /api/ai-interpret · /api/web-related · /api/quick-tax-types（另含 / 与 /api/health）
        │
        ▼
编排层  tax_answer.py（判型 → 分轮检索 → 定级 → 依据分层）
        依赖 tax_analyze.py（问题类型与四根前提轴）、tax_evidence.py（位阶 × 时效评分）
        │
        ▼
检索层  tax_search.py（NPC）· tax_detail.py（详情与 DOCX 正文）
        tax_web_search.py（总局 search5）· tax_fgk.py（法规库目录与正文）
        tax_so360.py（360 site: 站内）· tax_shui5.py（税屋）· tax_wechat.py（公众号）
        tax_browser.py（复用本机浏览器过 WAF）
        tax_aggregator.py（五源并发聚合）· tax_formatter.py（四段式输出）
        │
        ▼
数据源  flk.npc.gov.cn        法律 · 行政法规 · 司法解释（官方 API）
        www.chinatax.gov.cn   总局公告与法规库 fgk.chinatax.gov.cn（search5 JSON）
        m.so.com              site: 检索，定位省局、财政部、国务院等站点
        www.shui5.cn          税屋：实务解读、专栏、答疑（正文可取）
        mp.weixin.qq.com      微信公众号（经搜狗微信检索）
```

### 代码构成

| 层级 | 文件 | 职责 |
|------|------|------|
| **分析层** | `tax_analyze.py` | 9 类问题判定 + 四根前提轴识别（不联网） |
| **分析层** | `tax_evidence.py` | 依据效力位阶与时效定级、主依据挑选 |
| **分析层** | `tax_answer.py` | 判型→分轮检索→定级→依据分层的编排层 |
| **核心搜索** | `tax_search.py` | 30 项税种与专题映射（含依据源路由）、NPC API 调用、意图识别、缓存、**NPC 串行闸**（跨进程文件锁，`NpcSerialGate`） |
| **浏览器** | `tax_browser.py` | 本机已装浏览器探测 + 过 WAF + 导 cookie（不装内核） |
| **法规详情** | `tax_detail.py` | NPC 详情 API、DOCX 下载、全文提取、章节解析；详情元数据磁盘缓存（**默认开**、TTL 1h、命名空间 `detail`）；与 `tax_search` **共用同一把** NPC 串行闸 |
| **Web 搜索** | `tax_web_search.py` | 税务总局 search5 JSON 检索（含 fgk 法规库） |
| **站内搜索** | `tax_so360.py` | 360 `site:` 检索，定位省局与地方文件；识别"访问异常出错"拦截页并回报 |
| **法规库目录与正文** | `tax_fgk.py` | 税务总局法规目录，`--body` 取正文、`--cache` 缓存清单（TTL 1h、正文不缓存）、翻页按需自适应（`--pages` 显式指定则关自适应） |
| **统一请求层** | `tax_http.py` | 全项目对外 HTTP 请求的**唯一出口**；`verify` 设计成**必填参数**（`tax_detail`/`tax_search` 用默认关闭的 `VERIFY_SSL`，`tax_fgk` 用 requests 默认的 `True`，给默认值等于悄悄改行为）。各自维护 Session / WAF 应对的 `tax_shui5`、`tax_so360`、`tax_wechat`、`tax_web_search` 在白名单内，其余脚本直接发请求会被 `test_http_layer.py` 拦下 |
| **共享缓存** | `tax_cache.py` | 缓存逻辑的**唯一实现**（`tax_search` / `tax_fgk` / `tax_detail` 三处共用）；按条目里的 `_ns` 字段分命名空间，各自的 `--cache-clear` 互不越界；只缓存清单 / 元数据，**正文永不缓存** |
| **实务解读** | `tax_shui5.py` | 税屋：360 检索 + 浏览器过 WAF 后 HTTP 连读 |
| **实务解读** | `tax_wechat.py` | 微信公众号：搜狗微信 + 移动 UA |
| **多源聚合** | `tax_aggregator.py` | 五源并发、Jaccard 去重、权威度排序、源级失败上抛、跨源时间序 |
| **输出格式化** | `tax_formatter.py` | 四段式 Markdown、多源聚合输出；本身也是个命令行工具：`python scripts/tax_formatter.py --intent policy_lookup < result.json`（stdin 收 JSON、stdout 出 markdown，加 `--mode aggregated` 走多源归并；stdin 的字段要求见「命令行搜索」） |
| **模型通道** | `tax_llm.py` | 外部模型调用的唯一出口：付费闸门只认环境变量、不探测本机 CLI、额度类错误分型上抛 |
| **后端 API** | `tax_server.py` | Flask 路由、法规原文提取、解读搜索、AI 解读（走付费闸门）、取数失败与空结果分离 |
| **辅助（可选）** | `tunnel_daemon.py` | 把本地 5080 经 serveo.net 暴露到公网，供外网/手机访问；依赖免费第三方隧道、稳定性无保证，**非主链路**（SKILL.md 不引用） |
| **测试** | `test_tax_search.py` | 端到端 + 离线用例：parent_law 真实存在、聚合路由与跨源时间序、拦截页判别、sort=date、答题判分与解析、分层抽样、主依据健康度分档、净贡献配对覆盖、付费闸门与分发扫描 |
| **测试** | `test_eval_set.py` | 评测集规则的离线用例：时效判档、去重键、分类表同步 |
| **测试** | `test_tax_fgk.py` | fgk 离线用例：翻页上限与自适应收尾、文字/视频/空容器正文、缓存不含正文、命中标记与防污染（全打桩，不联网） |
| **测试** | `test_npc_gate.py` | NPC 串行闸用例：跨进程互斥、同进程多线程排队、超时后不锁死、接线检查 |
| **测试** | `test_detail_cache.py` | 详情缓存用例：键算法与历史缓存逐字节一致、命名空间不越界、命中留痕、老条目读时转正、缓存不含条文正文（红线）、原子写不留半截文件 |
| **测试** | `test_server_routes.py` | 服务端 9 路由用例：状态码与错误码、分支路由（npc/chinatax/fgk/aggregated）、付费闸门关着时不许碰模型、上游报错如实透出不吞成空结果（全打桩，不联网） |
| **测试** | `test_http_layer.py` | 统一请求层守门：AST 扫全项目、除白名单外禁止绕过 `tax_http` 的裸请求（含别名写法）、参数只透传不补全、`verify` 必填 |
| **门禁** | `check_doc_cli.py` | 文档-代码契约，两类检查：**命令级**（同行里点了脚本名的命令行，逐参数比对代码的参数表）+ **片段级**（孤立的 `` `--a --b` `` 必须能落在某一个脚本上），挡住"文档写了、代码没有"与"参数挂错脚本" |
| **测试** | `test_check_doc_cli.py` | 文档契约规则自检：构造样例钉住片段级检查既能报出坏片段、也不误报好片段。规则一旦改对就永远绿，没有自检就无法证明它还在工作 |
| **门禁入口** | `run_all.py` | 统一测试入口：默认跑离线组，`--online` 加联网组；退出码可直接接 CI（根目录 `run_tests.bat` 双击即跑） |
| **已废弃** | `generate_manual.py` | 原先生成硬编码 Word 手册，因与 README/SKILL.md 重复且已漂移而停用；现在运行只打印废弃说明并退出码 1 |
| **评测集** | `build_eval_set.py` | 归并公开财税题库为带出处与时效标记的统一评测集，按 SCOPE_EXCLUDE 剔除范围外题目 |
| **评测（主指标）** | `eval_answer.py` | 答题正确率：模型在环逐题判分，evidence/blind 双组对照；计费预告 + yes 确认 + 额度耗尽停批 |
| **评测（诊断）** | `eval_analysis.py` | 分析质量四指标 |
| **评测（诊断）** | `eval_retrieval.py` | 检索质量：路由覆盖 + 题库覆盖两级指标 |
| **技能定义** | `SKILL.md` | 分析主线与禁止行为清单 |
| **参考文档** | `references/*.md` | 税种映射、搜索策略、风险框架 |
| **前端** | `frontend/index.html` | 搜索界面 + 4 步向导 + 法规弹窗四标签页 |

---

## 三、核心功能模块

### 1. 实时法规检索

```
用户输入问题 → 9 类题型判定 → 四根前提轴识别 → 30 项税种与专题路由（按 authority 分派到 NPC 或总局）→ 依据定级与分层 → 分段作答
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
  "date_to": null
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

三个模块共用 `~/.cache/tax-policy-search`，靠条目里的 `_ns` 字段区分命名空间，
因此各自的 `--cache-stats` / `--cache-clear` 只作用于自己那一份。

---

## 六、与同类项目的对比

| 维度 | tax-policy-knowledge | npc-law-db | **tax-policy-search** |
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
tax-policy-search/
├── SKILL.md                        # 分析主线：判型 → 分轮检索 → 定级 → 比对 → 结论 → 输出
├── scripts/                        # 检索与分析模块（逐个职责见「代码构成」表）
│   ├── tax_answer.py               # 编排层：判型→分轮检索→定级→依据分层
│   ├── tax_analyze.py              # 问题类型判定、四根前提轴
│   ├── tax_evidence.py             # 依据效力位阶与时效定级
│   ├── tax_search.py               # NPC 法规库检索 + 30 项税种专题映射 + NPC 串行闸
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
│   ├── tax_formatter.py            # 四段式 Markdown 输出
│   ├── tax_llm.py                  # 外部模型调用的唯一出口：付费闸门 + 额度异常分型
│   ├── tax_server.py               # Flask API + 前端托管
│   ├── tunnel_daemon.py            # 隧道守护（可选：经 serveo.net 暴露本地 5080，非主链路）
│   └── generate_manual.py          # 已废弃：不再生成 Word 手册，运行只打印提示并退出 1
├── frontend/index.html             # 单文件界面：搜索栏 / 4 步向导 / 结果卡片 / 法规弹窗四标签页
├── references/                     # tax_categories · search_strategies · tax_risk_framework
├── tests/
│   ├── run_all.py                  # 统一门禁入口（默认离线组；--online 加联网组）
│   ├── test_tax_search.py          # 检索与接口用例（离线组 + 联网组）
│   ├── test_tax_fgk.py             # fgk 离线用例：翻页/正文/缓存（全打桩，不联网）
│   ├── test_npc_gate.py            # NPC 串行闸用例（含跨进程互斥）
│   ├── test_detail_cache.py        # 详情缓存用例：键一致 / 命名空间不越界 / 原子写（全打桩）
│   ├── test_server_routes.py       # 服务端路由用例：9 个路由的状态码与分支（全打桩，不联网）
│   ├── test_http_layer.py          # 统一请求层守门：AST 扫全项目、禁止绕过 tax_http 的裸请求
│   ├── test_eval_set.py            # 评测集规则的离线用例：时效判档、去重键、分类表同步
│   ├── check_doc_cli.py            # 文档-代码契约：SKILL.md / README.md 里的参数 vs 代码 add_argument
│   ├── test_check_doc_cli.py       # 文档契约规则自检：坏片段必须被报、好片段必须放行
│   ├── build_eval_set.py           # 归并公开财税题库 → 统一评测集
│   ├── eval_answer.py              # 主指标：答题正确率（模型在环，双组对照）
│   ├── eval_analysis.py            # 诊断：分析质量四指标
│   ├── eval_retrieval.py           # 诊断：检索质量两级指标
│   └── analysis_labels.json        # 分析题目标注集
├── archive/                        # 立项阶段研究资料（清单见 archive/README.md，7 文档 + 1 数据）
├── requirements.txt
├── start_local.bat                 # 启动本地服务（端口 5080）
└── run_tests.bat                   # 一键门禁（双击即跑，等价 python tests/run_all.py）
```

（`tests/results/` 是评测运行的输出目录，已进 `.gitignore`，不入库。）

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
