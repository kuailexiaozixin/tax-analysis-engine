# rd-deduction-hitech 子技能：来源、整合原则与边界

本目录是**自建子技能**（对标 `szse-lawrules/`、`tax-preference/` 模式），整合三个源技能的
领域内容并去除研发费用管理系统依赖。三个源技能一律未改动，本目录为独立副本，
后续维护只在本目录进行。

## 收录来源（三个源技能，均为只读对象）

| 来源 | 路径 | 取用内容 |
|---|---|---|
| rd-governor | `D:\WPS灵犀过程文件\20260629-20-36-23-270\rd-expense-system - Marvis\连接系统\skills\rd-governor` | 政策库（policy-docs 30 / book-rd-deduction-hightech 10 / book-ipo-rd-guide 22）、模板 34、MOC 4、方法论、regulatory-basis、routing-table、templates-index、workflow-*、enterprise-practice-index |
| hitech-qualification-audit | `C:\Users\贺新\.workbuddy\skills\hitech-qualification-audit` | 高企评分自检规则（内化于 `references/hitech-scoring.md`） |
| tax-high-tech-deduction | `C:\Users\贺新\.workbuddy\skills\tax-high-tech-deduction` | 加计归集、四套口径、2026 监管、稽查案例、资格维持（内化于 `references/deduction-guide.md`、`references/audit-response.md`） |

## 整合原则

1. **只整合"领域知识/规则/流程"内容**：评分规则、归集口径、限额公式、监管要点、模板、政策原文。
2. **不带入外部服务依赖**：付费核算通道（paid-call / weixinpay_ / setup-guide）、
   云端知识库 / MCP 服务（config/、offline_workflows/、api-endpoints）、web 交互页链接
   一律不带入——本子技能由 Agent 按规则直接核算，不消费模型额度以外的任何服务。
3. **不携带研发费用管理系统依赖**：不复制系统专属文件（schema / entity-operations /
   integration / generate_package.py / output/ / INDEX.md）；任何数据库表名、系统工具名、
   证据链模块路径均不出现于技能执行内容中。技能内对数据的表述统一为
   "企业数据台账 / 佐证编号 / 证据台账"。

## 结构

```
rd-deduction-hitech/
├── SKILL.md                 # 入口：触发、能力线、路由、红线
├── NOTE.md                  # 本说明
└── references/
    ├── hitech-scoring.md    # 高企评分自检规则
    ├── deduction-guide.md   # 加计归集与四套口径
    ├── audit-response.md    # 2026 监管、稽查、资格维持
    ├── workflow-governance.md   # 工作流 A（制度侧）
    ├── workflow-evidence.md     # 工作流 B（证据/资料包）
    ├── rd-mgmt-methodology.md  # 研发费用管理方法论底座
    ├── regulatory-basis.md      # 政策口径索引
    ├── routing-table.md         # 53 条意图→文件路由
    ├── templates-index.md       # 34 模板清单
    ├── moc-*.md                 # 4 个内容块索引
    ├── enterprise-practice-index.md
    ├── policy-docs/             # 30 政策原文
    ├── book-rd-deduction-hightech/  # 10 实务手册拆分
    ├── book-ipo-rd-guide/           # 22 IPO 指引拆分
    └── templates/              # 34 模板（制度/流程/表单/实操工具）
```

## 硬约束

- **源技能不可改动**：三个源目录保持原状；本目录为独立副本，后续维护只在本目录进行。
- **无系统数据库**：任何产出数据须来自用户提供或公开政策文件；缺失标「（待补）」。
- **政策时效**：任何政策引用先查 `book-rd-deduction-hightech/policy-updates-2024-2026.md`。
