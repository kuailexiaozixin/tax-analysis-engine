# tax-preference 构建与实测记录

本机 2026-09-30 实测。所有联网动作均为 plain HTTP，未调用任何模型、未消耗额度。

## 一次 sync 的实测结果

```
python preference.py sync --force --json
  动作=已更新  远端链接=.../c102373/c5252158/5252158/files/d4e88b62903447dbb143853a1c8ddfeb.xlsx
  字节数=288322  SHA1=0b8a09ea70b752a44d62703788c08b122ade8ba5  新版本=2026-09-03
建索引：有效 912 / 失效 689  （与官方目录一致）
```

栏目页 29759 字节，SSR 内联 xlsx 直链，无 JSON 接口。`locate()` 用锚文字
「减免税政策代码目录」命中 `.xlsx` href；版本节点 `c5252158` 每次发布都变，故不硬编码。

## 双栏结构与日期填充率（决定 C 的结论）

对目录 `catalog.json` 实测（同一 xlsx 的两栏）：

| 栏 | 条数 | 有效期起非空 | 有效期止非空 |
|---|---|---|---|
| 有效 | 912 | 0 (0.0%) | 0 (0.0%) |
| 失效 | 689 | 651 (94.5%) | 685 (99.4%) |

**结论**：条目的在效/失效**由所在栏决定**，有效栏根本不录日期。`judge_validity`
里"读有效期止判自然到期"这一路，从本目录拿不到数据——不能把空 `有效期止` 当"永不过期"。

## 重大发现 → 已接入（2026-09-30）：`POST /getFileListByCodeId`

plain HTTP、返回 JSON、分页、无需 UA/cookie、连发 20 次无限流。现已由
`scripts/tax_gov_list.py` 接入为第 6 个数据源，同步外壳是 `tax_sync.ListSynchronizer`
（分页爬全 → 集合 SHA1 diff → 变了才重建）。实测确认：

- 出口：`POST https://www.chinatax.gov.cn/getFileListByCodeId`
  参数 `codeId=&channelId=<栏目ID>&page=<1起>&size=<条/页>`
- 返回结构：`results.data.{total,page,rows,channelId,results[]}`
- 每条字段：`title,url,publishedTime/publishedTimeStr,channelName` +
  `domainMetaList[].resultList[]`，元素是 `{name,value,key}`
  （**取值按稳定的 `key`，不按 `name` 也不按下标**——分组顺序与中文名都可能改）。
  关心的 key：`aging`(时效性)/`writtentext`(发文字号)/`effectlevel`(效力等级)/
  `writtendate`(成文日期)/`taxpolicy`(税费类型)。
- `时效性` 取值实测：`全文有效 / 全文废止 / 已修改 / 部分失效 / 尚未生效`。
  这份分类状态已接进 `judge_validity`（见下），是该判据的官方输入之一。
- 栏目 channelId（实测 total）：
  - 财税文件 c102416 `2cb303fdee614232b79552d52bb057d6`（total 1532，**时效性恒空**）
  - 税务规范性文件 c100012 `470b437b304f434396500a1e2edc7f28`（total 1924，**时效性已填充**）
  - 其他文件 c100013 `4c1a5be62f6d44d48f386f630dcebbc5`（total 488）
  - 法律 c100009 `d34fa7ad03f84f4caed12f5c2beae099`（total 75）
  - 行政法规 c100010 `e1cd1569d1ea4a25a11041248925a081`（total 65，2026-09-30 实测爬全建索引）
- 与文号缓存的闭环：`tax_gov_list.py seed-cache` 把清单每行的发文字号（按
  `tax_terms.doc_number_of` 归一）→ 官方 url（过 `tax_cited.is_official`）批量种进
  `cited_links.json`，让 `locate_cited_document` 离线命中、零网络。

**仍拿不到**（实测，不编造）：**"废止日期"在列表里恒空、详情页 http→https 302 且部分
404——具体失效日期仍无源**，所以 judge_validity 不接日期区间支路（见"结论"一节），
只有 `时效性` 分类状态可用。全量翻 c100012≈193 次请求，按需分栏目 `sync`，不做全量常驻。

**不可靠 / 抓不到**（实测，不编造）：全站检索 `search5/search/s` plain HTTP 恒回
`接口无数据/内容包含敏感信息`、需浏览器态；政策解读 c100015 只走该 search、无列表接口；
RSS 全 403/404；增值税税率表/出口退税率库等未证实存在稳定 xlsx/csv 直链。

## #2 时效性分类状态接入 judge_validity（2026-09-30）

`judge_validity` 原来靠子串判状态，`"失效" in "部分失效"` 会把**部分失效**误判成
全文废止（repealed），把仍在使用的文件压到 0.35 系数。改成先按整串枚举优先级判：
`部分失效/部分废止/部分无效` 前置为 effective 并在 note 标"仅部分条款已失效，引用前须
核对具体条款"；`全文有效/全文废止/已修改/尚未生效` 各自归位。用例 `tests/test_judge_validity.py`
钉住，变异（删前置支/翻成 repealed）即转红。

## 复用关系

- `scripts/tax_sync.py`：两种同步外壳——`Synchronizer`（单资源直链 diff）与
  `ListSynchronizer`（分页列表集合 diff）；下载守卫 + 内容 SHA1 复用 + 构建回采纳版本号。
- `scripts/tax_http.py`：唯一网络出口（受 `tests/test_http_layer.py` bare-requests 门禁）。
- `scripts/tax_cited.py`：`is_official()` 官方域白名单 + 文号→链接缓存。
- `scripts/tax_gov_list.py`：清单源；`seed_cited_cache()` 依赖 `tax_cited` + `tax_terms`。
