# tax-preference 源实测记录

本文件只记《减免税政策代码目录》与配套清单源的**本机实测读数与源侧结构**，工作流与命令见
`SKILL.md`。所有联网动作均为 plain HTTP，不调用任何模型、不消耗额度。

## 目录 xlsx：一次同步的读数

`python preference.py sync --force` 的实测结果（本机 2026-09-30）：

```
远端链接  https://www.chinatax.gov.cn/chinatax//n810346/c102373/c5252158/5252158/files/
          d4e88b62903447dbb143853a1c8ddfeb.xlsx
字节数    288322   SHA1 0b8a09ea70b752a44d62703788c08b122ade8ba5   版本日期 2026-09-03
建索引    有效 912 / 失效 689   文号抽取失败 27 / 总 1601
```

栏目页 29759 字节，SSR 把 xlsx 直链内联在 HTML 里，没有 JSON 接口。`locate()` 按锚文字
「减免税政策代码目录」命中 `.xlsx` 的 href；入口按 `(栏目页, 首页)` 顺序尝试，两处都取不到
才报错，让同步器保留已有索引。路径里的版本节点 `c5252158` 每次发布都变，所以不硬编码。

`文号抽取失败 27` 逐条核对过：全是税收协定与国际公约（按国别而非文号标识）、地方减免类目
与"其他"兜底行——本就不该有文号。基线 1.7% 是合理值，不是解析缺口；这一格跳增才说明目录
文号列改版。

## 双栏结构与日期填充率（决定"有效栏读不出到期日"）

对同一份 xlsx 的两栏实测（本机 2026-10-04 复算 `preference_index.json`）：

| 栏 | 条数 | 有效期起非空 | 有效期止非空 |
|---|---|---|---|
| 有效 | 912 | 0 (0.0%) | 0 (0.0%) |
| 失效 | 689 | 651 (94.5%) | 685 (99.4%) |

条目的在效/失效**由所在栏决定**，有效栏根本不录日期。所以"有效期止为空"不能读成
"永不过期"，也不能从有效条目里抠到期日——要到期日就取回正文读自载期限。

## 配套清单源 `scripts/tax_gov_list.py`

`POST https://www.chinatax.gov.cn/getFileListByCodeId`，参数 `codeId=&channelId=<栏目ID>&page=<1起>&size=<条/页>`。
plain HTTP、返回 JSON、分页、无需 UA/cookie；本机连发 20 次未见限流。返回结构
`results.data.{total,page,rows,channelId,results[]}`，每条带 `title`、`url`、
`publishedTime`/`publishedTimeStr`、`channelName`，加上 `domainMetaList[].resultList[]`
里的 `{name,value,key}`。**取值按稳定的 `key` 取，不按 `name` 也不按下标**——分组顺序与
中文名都可能改。关心的 key：`aging`（时效性）、`writtentext`（发文字号）、`effectlevel`
（效力等级）、`writtendate`（成文日期）、`taxpolicy`（税费类型）、`writtendepartment`（制定机关）。

栏目 channelId 与整栏条数（本机 2026-10-04 爬全后的读数）：

| 栏目 | channelId | 条数 |
|---|---|---|
| 财税文件 c102416 | `2cb303fdee614232b79552d52bb057d6` | 1532（时效性整栏 0 条真值） |
| 税务规范性文件 c100012 | `470b437b304f434396500a1e2edc7f28` | 1925 |
| 其他文件 c100013 | `4c1a5be62f6d44d48f386f630dcebbc5` | 488（时效性整栏 0 条真值） |
| 税务部门规章 c100011 | `0ac34e96afbb4be28844f18eef412421` | 86 |
| 法律 c100009 | `d34fa7ad03f84f4caed12f5c2beae099` | 75 |
| 行政法规 c100010 | `e1cd1569d1ea4a25a11041248925a081` | 65 |
| 国务院文件 c102440 | `fa1726b47078490fa0a4522194185e8d` | 35 |

栏目标签与条数都记在 `tax_gov_list.CHANNELS` 的行尾注释里；栏目页直链逐条记在
`tax_gov_list.CHANNEL_PAGES`——页面名不统一，税务部门规章是 `c100011/list.html`，其余六栏是
`listflfg.html`，按模板拼串会回 404。

税务规范性文件那一栏的 `时效性` 分布实测为：**全文有效 808 / 全文废止 745 / 已修改 347 /
全文失效 23 / 尚未生效 2**，合起来正好是 `tax_web_search.AGING_VALUES` 那五种。这套词与
NPC 法规库那套含"部分失效/部分废止"的状态串**不是同一套词**，别互相套用；`judge_validity`
按整串枚举判，"部分失效"含子串"失效"，按子串判会被压成全文废止。

清单每行的官方 url 与发文字号可以批量种进文号缓存（`seed-cache`，文号按
`tax_terms.doc_number_of` 归一、url 过 `tax_cited.is_official`），让 `locate_cited_document`
离线命中、零网络；`missing` 反向列出清单里有官方 url、文号却尚未进缓存的待补条目。
读写两端共用 `doc_number_of` 归一，去掉任一端就取不到。

## 取不到的东西（实测，不编造）

- **"废止日期"在列表里恒空**：`xxgk_abolishDate` 按三种时效取值各取 10 条，30 条里只有空串
  与字符串 `"null"`，一个真实日期都没给出过；详情页 http→https 302 且部分 404。具体失效
  日期无源，判某日是否已废止只能读正文或走现行/废止版配对（`tax_fgk.fetch_associations`）。
- 全站检索 `search5/search/s` 用 plain HTTP 恒回「接口无数据/内容包含敏感信息」，需要浏览器态。
- 政策解读 c100015 只走那个 search，没有列表接口。
- RSS 全 403/404。
- 增值税税率表、出口退税率库等未见稳定 xlsx/csv 直链。

全量翻 c100012 约 193 次请求，所以按需分栏目 `sync`，不做全量常驻。

## 硬约束

- **不内置税率算"能省多少钱"**。目录与清单给的是政策名、减免性质代码、文号与效力状态，
  不含税率常量；优惠比例与计算公式一律取回文件正文按 ⑧ 定级后引用，提示也指向目录/正文源。
- 下载与链接解析只允许 `chinatax.gov.cn` 及其子域（`scripts/tax_cited.py:is_official`）。

## 复用关系

- `scripts/tax_sync.py`：`Synchronizer`（单资源直链 diff，目录 xlsx 用这条）与
  `ListSynchronizer`（分页列表集合 diff，清单源用这条，`sig_fields=("url","时效性","发文字号","title")`）；
  下载守卫 + 内容 SHA1 复用 + 构建回采纳版本号。
- `scripts/tax_http.py`：唯一网络出口（受 `tests/test_http_layer.py` bare-requests 门禁）。
- `scripts/tax_cited.py`：`is_official()` 官方域白名单 + 文号→链接缓存。
- `scripts/tax_gov_list.py`：清单源；`seed_cited_cache()` 预热、`missing_cited()` 出待补清单，
  两者都依赖 `tax_cited` + `tax_terms`（共键：都走 `doc_number_of` 归一）。
- 用例：`tests/test_preference.py`（目录侧）、`tests/test_gov_list.py`（清单侧）。
