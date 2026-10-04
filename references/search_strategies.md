# Search Strategies by Intent Type

Five intent types × two-source search strategy. Always search FIRST, answer SECOND.

**NPC 的 `--scope fulltext` 可用但别当主入口**：它按全文分词匹配，短词会被通用词带偏
（"研发费用加计扣除"会带到《诉讼费用交纳办法》）。要确定条文归属仍以标题检索为准。
需要"某词在正文里被哪部法规提到"，正文检索确实比标题检索宽，但要拿 `_reliability: medium`
的结果去核对。两个数据源分工：NPC 出法律本体，chinatax 出政策解读与操作口径。

**先看 `authority` 再决定查哪一源**。`resolve_tax_type()` 的返回值里带 `authority`：
`"npc"` 就按 `parent_law` 查 NPC；`"sta"` 说明这个专题（转让定价、税收协定、
非居民企业、税务行政处罚等 12 项）在 NPC 库里检索无效——搜"反避税"返回 0 条，
搜"转让定价"返回 10 条全是土地和矿产资源转让条例。见到 `"sta"` 就直接走
`tax_fgk.py`，不要浪费一轮 NPC 检索再发现结果全是无关法规。
`"overseas"`（全球最低税/支柱二）两个库都不查：法规库在文件类标签下按"支柱二"取到 0 条，
NPC 按本体法取回的是标题里含"企业"的无关法律，要查的是关掉文件类标签的总局全站层
（`python scripts/tax_web_search.py "支柱二" --all-labels`），并把该专题的 `note` 带进答案。
三项的条数随 `TAX_TYPE_KEYWORDS` 变，SKILL.md ⑨ 给了数它的命令。

---

## Intent → Search Strategy Matrix

### 1. policy_lookup (政策查询)

**Trigger**: General policy questions without specific filing/risk/eligibility signals.

```bash
# 先解析路由：authority=npc 走下面两步，authority=sta 直接跳到 Phase 3
python -c "import sys;sys.path.insert(0,'scripts');from tax_search import resolve_tax_type;print(resolve_tax_type('<问题>'))"

# Phase 1: Title search with keyword
python scripts/tax_search.py "<keyword>" --status 3 --size 20

# Phase 2 (if Phase 1 returns 0): chinatax.gov.cn, which really searches body text
python scripts/tax_web_search.py "<keyword>" --size 20

# Phase 3 (authority=sta): 总局法规库，自带翻页
python scripts/tax_fgk.py "<关键词>" --size 5

# When user knows exact regulation name: Exact match
python scripts/tax_search.py "<exact_name>" --exact --status 3
```

**Example queries**: "小规模纳税人增值税率多少", "研发费用加计扣除比例",
"关联申报表要准备什么资料"（→ 转让定价 → Phase 3）

---

### 2. filing_guide (申报指导)

**Trigger**: Keywords about filing, deadlines, procedures.

```bash
# Title-first search for filing-related regulations
python scripts/tax_search.py "<keyword> 申报" --status 3 --sort date

# Supplement: chinatax.gov.cn for operational guides
python scripts/tax_web_search.py "<keyword> 申报流程" --size 10
```

**Key data to extract from results**:
- Filing deadlines
- Required documents
- Filing channels (online/offline)
- Step-by-step procedure

---

### 3. risk_check (合规风险)

**Trigger**: Keywords about risk, compliance, audit, penalties.

```bash
# NPC has no body-text search. Risk indicators live in the 稽查/处罚 documents
# themselves, so look them up by the regulation that carries them:
python scripts/tax_search.py "税务行政处罚" --status 3 --size 20
python scripts/tax_search.py "税收违法" --status 3 --size 20

# Supplement: chinatax.gov.cn for latest enforcement notices
python scripts/tax_web_search.py "<keyword> 稽查 处罚" --size 10
```

**Risk keywords reference**:
- 虚开发票, 骗取留抵退税, 骗取出口退税
- 关联交易, 转让定价, 两税收入差异
- 长亏不倒, 税负率异常, 资金闭环回流

**Output format for risk checks**:
```
风险类型: [从法规提取]
触发条件: [从法规提取]
风险等级: 🟢低 / 🟡中 / 🔴高
法规依据: [文号 + 法条引用]
```

---

### 4. eligibility (资格判定)

**Trigger**: Keywords about whether a policy applies, qualifying conditions.

```bash
# Exact + fuzzy dual approach
python scripts/tax_search.py "<policy_name>" --status 3 --size 20
python scripts/tax_web_search.py "<policy_name> 条件" --size 20
```

**Key data to extract**:
- Eligibility criteria (industry, size, revenue, employee count)
- Required documentation
- Application procedure and deadlines
- Common exclusion conditions
- "Not applicable if..." clauses

**Always check effective dates**: Policies expire or change thresholds over time.

---

### 5. invoice (发票)

**Trigger**: Keywords about invoices, issuance, redaction, loss.

```bash
# Invoice management regulations — title-first
python scripts/tax_search.py "发票管理办法" --exact --status 3

# Specific invoice issues — chinatax.gov.cn carries the operational detail
python scripts/tax_web_search.py "<keyword> 发票" --size 15
```

**Key invoice topics**:
- 发票领购 / 发票开具 / 发票红冲
- 发票遗失处理 / 发票认证 / 发票抵扣
- 全电发票 / 数电票 / 电子发票
- 发票管理办法处罚条款

---

## Cross-Intent Rules

1. **Always include --status 3 by default**. Only show abolished/amended laws when explicitly requested.
2. **Search NPC by title first**. `--scope fulltext` is now relevance-sorted and is fine
   for locating candidate laws, but confirm the exact article via title or `--exact`.
3. **When uncertain about intent**: run both `policy_lookup` and `eligibility` strategies, let user pick.
4. **Always include `searched_at` timestamp** in output.
5. **Never use training data as the primary answer source**. Always cite specific API results with document IDs.
