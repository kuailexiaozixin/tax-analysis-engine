# 财税政策实时搜索引擎 — 项目介绍

> **Tax Policy Real-Time Search** — 通过多源实时检索，为企业和个人提供权威、准确、最新的中国税收政策法规查询与解读服务。

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
        tax.shui5.cn          税屋：实务解读、专栏、答疑（正文可取）
        mp.weixin.qq.com      微信公众号（经搜狗微信检索）
```

### 代码规模

| 层级 | 文件 | 行数 | 职责 |
|------|------|------|------|
| **分析层** | `tax_analyze.py` | 402 | 9 类问题判定 + 四根前提轴识别（不联网） |
| **分析层** | `tax_evidence.py` | 315 | 依据效力位阶与时效定级、主依据挑选 |
| **分析层** | `tax_answer.py` | 467 | 判型→分轮检索→定级→依据分层的编排层 |
| **核心搜索** | `tax_search.py` | 826 | 30 项税种与专题映射（含依据源路由）、NPC API 调用、意图识别、缓存 |
| **浏览器** | `tax_browser.py` | 275 | 本机已装浏览器探测 + 过 WAF + 导 cookie（不装内核） |
| **法规详情** | `tax_detail.py` | 331 | NPC 详情 API、DOCX 下载、全文提取、章节解析 |
| **Web 搜索** | `tax_web_search.py` | 190 | 税务总局 search5 JSON 检索（含 fgk 法规库） |
| **站内搜索** | `tax_so360.py` | 203 | 360 `site:` 检索，定位省局与地方文件；识别"访问异常出错"拦截页并回报 |
| **法规库目录与正文** | `tax_fgk.py` | 255 | 税务总局法规目录，`--body` 可取正文 |
| **实务解读** | `tax_shui5.py` | 399 | 税屋：360 检索 + 浏览器过 WAF 后 HTTP 连读 |
| **实务解读** | `tax_wechat.py` | 276 | 微信公众号：搜狗微信 + 移动 UA |
| **多源聚合** | `tax_aggregator.py` | 290 | 五源并发、Jaccard 去重、权威度排序、源级失败上抛、跨源时间序 |
| **输出格式化** | `tax_formatter.py` | 231 | 四段式 Markdown、多源聚合输出 |
| **后端 API** | `tax_server.py` | 682 | Flask 路由、法规原文提取、解读搜索、AI 解读、取数失败与空结果分离 |
| **辅助** | `generate_manual.py` | 589 | 手册生成 |
| **辅助** | `tunnel_daemon.py` | 69 | 隧道守护 |
| **测试** | `test_tax_search.py` | 948 | 端到端 + 离线用例：parent_law 真实存在、聚合路由与跨源时间序、拦截页判别、sort=date |
| **测试** | `test_eval_set.py` | 173 | 评测集规则的离线用例：时效判档、去重键、分类表同步 |
| **评测集** | `build_eval_set.py` | 260 | 归并公开财税题库为带出处与时效标记的统一评测集 |
| **评测** | `eval_retrieval.py` | 337 | 检索质量：路由覆盖 + 题库覆盖两级指标 |
| **评测** | `eval_analysis.py` | 290 | 分析质量四指标评测 |
| **技能定义** | `SKILL.md` | 998 | AI Agent 操作手册 |
| **参考文档** | `references/*.md` | 415 | 税种映射、搜索策略、风险框架 |
| **前端** | `frontend/index.html` | 823 | 搜索界面 + 4 步向导 + 法规弹窗四标签页 |
| **总计** | **26 文件** | **10,044 行** | |

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
| **分页** | 每页 20-100 条，NPC API 支持总量查询 |

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
| 🤖 **AI 解读** | Claude 用通俗语言解读法规（适用主体/核心要点/注意事项） | Claude Code CLI |
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
| `GET` | `/api/ai-interpret/<id>?keyword=` | AI 生成通俗解读（Claude Code CLI） |
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

| 数据类型 | 缓存时间 | 说明 |
|---------|---------|------|
| 搜索结果 | **5 分钟** | 政策随时更新 |
| 详情元数据 | **1 小时** | 变动频率低 |
| DOCX 文件 | **24 小时** | 法规全文极少变动 |
| 默认行为 | **不使用缓存** | 每次必须实时查询 |

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
| 代码量 | 0（纯 Markdown） | ~1,200 行 Python | **Python 检索/分析层 + 单文件前端，规模见「代码规模」表** |

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
| **AI 解读** | Claude Code CLI (subprocess) | 法规通俗化解读生成 |
| **测试** | Python unittest 模式 | 离线用例（拦截页判别、时效判档、去重键）+ 联网端到端用例 |
| **依赖** | requests, urllib3, flask | 仅 3 个 pip 包 |

---

## 八、快速开始

### 安装

```bash
# 1. 安装依赖
pip install flask requests urllib3

# 2. 启动服务
cd ~/.claude/skills/tax-policy-search
python scripts/tax_server.py

# 3. 打开浏览器
# http://localhost:5080
```

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
```

### 作为 Claude Code Skill 使用

Skill 已自动注册。在 Claude Code 对话中直接提问：

```
"小微企业增值税有什么优惠？"
"研发费用加计扣除比例是多少？"
"金税四期有哪些风险指标？"
```

Claude 将自动激活此 Skill，实时搜索 NPC 法规库后回答。

---

## 九、项目目录结构

```
tax-policy-search/
├── SKILL.md                        # 分析主线：判型 → 分轮检索 → 定级 → 比对 → 结论 → 输出
├── scripts/                        # 检索与分析模块（逐个职责见「代码规模」表）
│   ├── tax_answer.py               # 编排层：判型→分轮检索→定级→依据分层
│   ├── tax_analyze.py              # 问题类型判定、四根前提轴
│   ├── tax_evidence.py             # 依据效力位阶与时效定级
│   ├── tax_search.py               # NPC 法规库检索 + 30 项税种专题映射
│   ├── tax_detail.py               # 详情元信息 + DOCX 全文解析
│   ├── tax_web_search.py           # 税务总局 search5 JSON 检索
│   ├── tax_so360.py                # 360 site: 站内检索（含拦截页识别）
│   ├── tax_fgk.py                  # 税务总局法规库目录与正文
│   ├── tax_shui5.py                # 税屋检索与正文
│   ├── tax_wechat.py               # 微信公众号（搜狗微信）
│   ├── tax_browser.py              # 复用本机浏览器过 WAF、导 cookie
│   ├── tax_aggregator.py           # 五源并发聚合（源级失败上抛）
│   ├── tax_formatter.py            # 四段式 Markdown 输出
│   ├── tax_server.py               # Flask API + 前端托管
│   ├── generate_manual.py          # 手册生成
│   └── tunnel_daemon.py            # 隧道守护
├── frontend/index.html             # 单文件界面：搜索栏 / 4 步向导 / 结果卡片 / 法规弹窗四标签页
├── references/                     # tax_categories · search_strategies · tax_risk_framework
├── tests/
│   ├── test_tax_search.py          # 检索与接口用例（离线组 + 联网组）
│   ├── test_eval_set.py            # 评测集规则的离线用例：时效判档、去重键、分类表同步
│   ├── build_eval_set.py           # 归并公开财税题库 → 统一评测集
│   ├── eval_retrieval.py           # 检索质量：路由覆盖 + 题库覆盖两级指标
│   ├── eval_analysis.py            # 分析质量四指标
│   └── analysis_labels.json        # 分析题目标注集
├── docs/                           # GitHub Pages 静态快照（预烘焙 JSON，不参与运行时）
├── data_source_analysis.json       # 数据源调研留档
├── requirements.txt
├── start_local.bat
└── *.md                            # 各阶段可行性与架构分析留档
```

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
| 6 | AI 解读引擎 | Claude Code CLI 子进程 | 零新依赖，当前环境已可用 |
| 7 | DOCX 解析 | Python stdlib (zipfile+ET) | 零 pip 依赖，不需要 python-docx |
| 8 | 前端技术 | 纯 HTML/CSS/JS 零框架 | 单文件部署，无构建步骤 |
| 9 | 技能格式 | Anthropic SKILL.md 规范 | Claude Code 自动发现和激活 |
| 10 | 安全约束 | 禁止行为清单 + 强制免责与时间戳 | 税收领域法律风险高，必须限制 AI 自由发挥；条目见 SKILL.md ⑦ |

---

## 十一、测试覆盖

```
$ python tests/test_tax_search.py

============================================================
tax-policy-search: End-to-End Tests
============================================================
> Question Type Classification   [9/9] ✅
> Context Axis Detection        ✅
> Evidence Authority Ranking    [7/7] ✅
> Evidence Validity and Primary  ✅  未废止/未解读类占据主依据
> Analysis Orchestration Plan   ✅  轮次随题型变化
> Search Term Routing           ✅  sta / npc 两类专题均正确
> Installed Browser Detection   ✅  ['edge']，均为已装路径
> shui5 Batch Body Read         ✅  3/3 篇取到正文
> Intent Detection              [7/7] ✅
> Tax Type Resolution           [6/6] ✅
> Title Search (NPC API)        ✅ 45 results
> Fulltext Search               ✅ 3829 results
> Exact Search                  ✅ 2 matches
> Date Range Filter             ✅ 3847 results in 2024-2026
> Cache                         ✅ 两次检索命中同一缓存
> Fetch Detail                  ✅ 增值税法
> chinatax.gov.cn Search5        ✅ Total: 2955
> fgk Regulation Library         ✅ 4 entries
> fgk Article Body               ✅ 5107 chars
> 360 Site Search               ✅ 5 hits
> shui5.cn Search               ✅ 2 hits
> shui5 Article Body             ✅ 6703 chars
> WeChat (Sogou) Search          ✅ 3 hits
> NPC Reliability Marker        ✅
> fgk Paging                    ✅ 5 页取到 3 份检索条件
> sta Topics Reachable          [13/13] ✅
> parent_law Authenticity       [17/17] ✅
> Ranking Size Insensitivity    ✅  size=[1,3,20] 首条一致
============================================================
Results: 46/46 passed
============================================================
```

---

## 十二、许可与免责

本项目仅供税务政策查询参考，**不构成税务建议**。所有税收政策信息以以下官方来源为准：

- 国家税务总局官网：https://www.chinatax.gov.cn
- 财政部官网：https://www.mof.gov.cn
- 国家法律法规数据库：https://flk.npc.gov.cn
- 纳税服务热线：12366

涉及具体税务处理和申报操作，请咨询专业税务人员或主管税务机关。
