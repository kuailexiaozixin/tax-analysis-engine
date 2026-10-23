# R&D Expenses 子技能说明

研发费用 / 研发支出 / 研发投入 / 高企认定研发费用实务的技能族（母入口 + 六条任务线 + 共享知识底座）。本说明只记录当下执行所需的结构与硬约束。

## 结构

```
R&D Expenses/
├── SKILL.md                 # 母入口：总则 + 六步通用 SOP + 任务分派 + 交付要求 + 交付前自检
├── cases/  policy/          # 共享知识底座（真实案例 / 政策原文与指引）
└── subskills/               # 六条任务线
    ├── rd-proj-application/   项目申报（含高企评分自检）
    ├── rd-tax-benefit/        优惠享受（含速算表）
    ├── rd-accounting-tax/     财税处理（含科目级口径基准、辅助账模板）
    ├── rd-internal-control/   内控建设与流程优化（含 29 份制度/流程/表单模板、方法底座）
    ├── rd-risk-diagnosis/     风险评估诊断
    └── rd-audit-response/     稽查审查应对
```

## 硬约束

- **无系统数据库**：不连接业务系统、不读写数据库、不调模型；数字一律来自用户提供或公开政策文件，缺失标「（待补）」，绝不虚构。
- **原文可溯源**：结论须附 `policy/` 条款或 `cases/` 编号；引用政策前核对其现行效力。
- **跨口径不得外推**：会计 / 高企认定 / 加计扣除 / R&D 统计 / IPO 口径各自独立，窄口径不满足不代表宽口径不满足，宽口径已计入也不代表窄口径可归集；科目级取值以 `subskills/R&D-Expenses/subskills/rd-accounting-tax/references/subject-caliber-baseline.md` 为唯一基准。
- **口径隔离**：对外任何口径结论都要写明适用口径与年度。
