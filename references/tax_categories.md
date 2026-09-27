# Tax Categories & Search Parameter Reference

## 30 项税种与专题 — Keyword → 检索策略映射

When the user's query matches a tax type, use the corresponding search keywords and strategies.

### Category A: 流转税 (Turnover Tax)

| Tax Type | Search Keywords | Parent Law | Priority |
|---------|----------------|------------|----------|
| 增值税 | 增值税, VAT, 进项税, 销项税, 留抵退税 | 中华人民共和国增值税法 | 1 |
| 消费税 | 消费税, 卷烟, 成品油 | 中华人民共和国消费税暂行条例 | 2 |
| 关税 | 关税, 进出口税, 保税, 海关 | 中华人民共和国关税法 | 3 |

### Category B: 所得税 (Income Tax)

| Tax Type | Search Keywords | Parent Law | Priority |
|---------|----------------|------------|----------|
| 企业所得税 | 企业所得税, 应税所得, 税前扣除, 加计扣除, 高新技术企业, 小微企业 | 中华人民共和国企业所得税法 | 1 |
| 个人所得税 | 个人所得税, 综合所得, 专项附加扣除, 年度汇算 | 中华人民共和国个人所得税法 | 1 |

### Category C: 财产行为税 (Property & Behavioral Tax)

| Tax Type | Search Keywords | Priority |
|---------|----------------|----------|
| 房产税 | 房产税, 房地产税 | 4 |
| 土地增值税 | 土地增值税, 土增税, 清算 | 4 |
| 契税 | 契税, 不动产登记 | 4 |
| 城镇土地使用税 | 城镇土地使用税 | 4 |
| 车船税 | 车船税, 车辆购置税 | 5 |
| 印花税 | 印花税, 合同印花税 | 4 |
| 城市维护建设税 | 城建税, 教育费附加, 地方教育附加 | 4 |

### Category D: 资源环境税 (Resource & Environmental Tax)

| Tax Type | Search Keywords | Priority |
|---------|----------------|----------|
| 资源税 | 资源税, 水资源税, 矿产资源税 | 4 |
| 环境保护税 | 环境保护税, 环保税 | 4 |

### Category E: 征管与优惠 (Administration & Preferences)

| Category | Search Keywords | Parent Law |
|----------|----------------|------------|
| 税收征管 | 税收征收管理, 税务登记, 纳税申报, 发票管理, 税务稽查, 金税四期 | 中华人民共和国税收征收管理法 |
| 烟叶税 | 烟叶税, 烟叶 | 中华人民共和国烟叶税法 |

### Category F: 总局专题（authority="sta"，检索词非分类名）

这 12 项**在 NPC 库里检索无效**，必须用 `tax_fgk.py` 查总局。"检索词"是实测
能翻出依据的词，与"Tax Type"不一定相同。

| Tax Type | Search Keywords | 检索词 |
|---------|----------------|--------|
| 转让定价 | 转让定价, 关联交易, 同期资料, 预约定价, 资本弱化, 成本分摊, 国别报告 | 转让定价 |
| 反避税 | 反避税, 一般反避税, 特别纳税调整, BEPS, 税基侵蚀 | 预约定价安排 |
| 税收协定 | 税收协定, 双重征税, 税收居民身份, 税收条约 | 税收协定 |
| 常设机构 | 常设机构, 营业场所, 固定场所, 工程场所 | 常设机构 |
| 非居民企业 | 非居民企业, 非居民, 源泉扣缴, 预提所得税, 支付所得 | 非居民企业 |
| 境外所得 | 境外所得, 境外投资, 境外股息, 递延纳税 | 境外所得 |
| 税收抵免 | 税收抵免, 抵免限额, 分国不分项, 国别抵免 | 税收抵免 |
| 受控外国企业 | 受控外国企业, 外国企业股息, 视同股息分配 | 境外所得 |
| 税务行政处罚 | 税务行政处罚, 行政处罚, 听证, 裁量权, 罚款, 滞纳金 | 行政处罚 |
| 税收争议救济 | 行政复议, 行政诉讼, 纳税争议, 起诉期限 | 行政复议 |
| 纳税担保与信用 | 纳税担保, 纳税保证人, 纳税信用, 失信主体 | 纳税信用评价与修复 |
| 税收优惠 | 税收优惠, 减免税, 退税, 即征即退, 先征后退, 免税 | 税收优惠 |

---

## NPC API Status Codes (sxx)

| Code | Status | Meaning | Badge |
|------|--------|---------|-------|
| 1 | 已废止 | No longer in force | 🔴 |
| 2 | 已修改 | Amended (older version) | 🟡 |
| 3 | 现行有效 | Currently effective | 🟢 |
| 4 | 尚未生效 | Published but not yet effective | 🔵 |

**Default filter: status=3 (effective only).**
User can request "all" or "include abolished" to remove the filter.

---

## Category Codes (flfgCodeId)

| Category | Scope |
|----------|-------|
| 宪法 | Constitutional law |
| 法律 | National laws (passed by NPC/NPCSC) |
| 行政法规 | Administrative regulations (State Council) |
| 监察法规 | Supervisory regulations |
| 地方法规 | Local regulations |
| 司法解释 | Judicial interpretations |

Tax laws are typically under "法律" (national laws) or "行政法规" (administrative regulations).

---

## Sort Options

| Value | Meaning |
|-------|---------|
| relevance (default) | By search relevance score |
| date (sort=gbrq, order=-1) | By publish date, newest first |

---

## Issuing Authorities (zdjgCodeId) — Tax-Relevant

| Code | Authority |
|------|-----------|
| 110 | 全国人民代表大会 / 全国人大常委会 |
| 120 | 国务院 |
| 130 | 最高人民法院 / 最高人民检察院 |
| *(local authorities vary by region)* | *(use region_classifier.py for post-processing)* |

---

## Rate Limiting

| Mode | Use Case |
|------|----------|
| auto (default) | Small tasks unlimited, medium 5rps, large adaptive |
| fixed | For programs: steady 5 req/s |
| adaptive | For collections >100 items: auto-adjusts 1-8 req/s |

---

## 新增于分析层的两项判定

分类表解决"归到哪个税种"，另两个问题由 `tax_analyze.py` 解决：

- **问题类型**：同样问企业所得税，`lookup` 一轮标题检索就够，
  `option_judge` 要那部法全文逐条比对，`entitlement` 要本体法 + 总局文件 +
  实操口径三轮。走错取证方式，检索命中再高也答不对。
- **前提缺口**：时点、主体、地区、金额四根轴。题面没交代就下具体结论，
  等于把猜测写成答案。

两项都不在分类表里，因为它们量的不是"哪个税种"，而是"要什么形式的答案"
和"还缺什么信息"。
