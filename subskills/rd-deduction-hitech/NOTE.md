# rd-deduction-hitech 子技能说明

研发费用加计扣除与高新技术企业认定合规分析子技能。本说明只记录当下执行所需的结构与硬约束；不记录任何来源、变更或迁移过程。

## 结构

```
rd-deduction-hitech/
├── SKILL.md                 # 入口：触发、能力线、路由、红线
├── NOTE.md                  # 本说明
└── references/
    ├── hitech-scoring.md    # 高企评分自检规则
    ├── deduction-guide.md   # 加计归集与扩展口径（R&D 统计 / IPO）
    ├── audit-response.md    # 2026 监管、稽查、资格维持
    ├── workflow-governance.md   # 工作流 A（制度侧）
    ├── workflow-evidence.md     # 工作流 B（证据/资料包）
    ├── rd-mgmt-methodology.md  # 研发费用管理方法论底座（第 4.1 节＝三套口径科目级唯一基准）
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

- **无系统数据库**：本技能不连接任何业务系统、不读写数据库、不调模型；任何产出数据须来自用户提供或公开政策文件；缺失标「（待补）」。
- **政策时效**：任何政策引用先查 `book-rd-deduction-hightech/policy-updates-2024-2026.md` 时效性标注；政策原文以最新有效版本为准。
- **模板路径基准**：模板位于 `references/templates/`（相对本技能目录），即与 `SKILL.md` 同目录的 `references/templates/`；路径基准以 `templates-index.md` 为准。
