---
name: chinatax
description: |
  国家税务总局两域（站点 www.chinatax.gov.cn 的 search5 检索、政策法规库 fgk.chinatax.gov.cn 的正文与清单）
  站点子技能。当需要**按税种或法名检总局公告与规范性文件、把命中收窄到某个时效／效力等级／税种／
  发文字号／成文日期区间、枚举某一栏的全部现行文件、读回某一份的正文与随文附件、判断这两域为什么回 0 条
  或条数为什么和界面不一样、手上只有一枚文号要回库里对、找同一文件的现行版与被废止版**，
  或需要复测 search5 的参数值域、分面字段、正文容器形态与两个域名之间的分工时使用。
  触发词：国家税务总局、总局、chinatax、法规库、fgk、search5、search5/search/s、getFileListByCodeId、
  govDoc、docNum、docType、docYear、docNo、cwrqStart、xxgkAging、xxgkEffectLevel、xxgkSonTaxPolicy、
  wordPlace、participleRule、orderBy、label、FILE_LABELS、column、GSFFK、FGK_MARKER、media_only、
  appendix、queryManuscriptAssociation、tax_gov_list、成文日期、时效性、翻页基准、0 条、附件。
  本子技能管"这两域怎么取"，取回来的东西在答案里算什么由分析层判；全程只发 HTTP 请求，
  **不调用任何模型、不消耗任何额度**。
---

# 国家税务总局站点与政策法规库（chinatax.gov.cn）站点子技能

两域合起来是总局文件的三个入口：www 域的 `search5` 按词召回（覆盖站点，也覆盖法规库条目的录入元数据）、
fgk 域的详情页给正文与注释、www 域的 `getFileListByCodeId` 给整栏封闭清单。都是纯 HTTP，
没有 WAF、不要凭据、不启浏览器。取数难点不在接入而在三处：**翻页与每页条数的基准和 NPC 那一站相反**
（`pageNum` 从 0 起、`pageSize` 卡在 10，传更大也只回 10 行）；**收窄条件给越界值不回错误码**
（时效与效力等级两维给域外值回 `total=0`，与"这一维确实没有内容"在响应里是同一个形态，
而这两维同时发必然归零）；**清单层与正文层各装一半**（附件只在清单层、且只有名与链，
表格与执行期限只在正文层）。判据写在本文件，逐项读数（含取样口径）记在 `NOTE.md`。

## 工作流

**第一步 定这一趟要什么**——下面几行的入口、耗时与前置各不同，先选对再动手：

| 要什么 | 走哪条 | 前置 |
|---|---|---|
| 总局公告、规范性文件、文字解读（按词召回） | `tax_web_search.search_chinatax(关键词)`，即 `GET www.chinatax.gov.cn/search5/search/s` | 无：开放接口，不要凭据、不启浏览器 |
| 规章与法规库条目（国际税收、转让定价、处罚依据在这一路） | `tax_fgk.search_fgk(关键词)`，同一枚 search5 接口逐页扫并挑出法规库条目 | 无；默认自适应早停，条数可疑时按第三步用 `--pages` |
| 在上面两路把命中收窄到某一维 | 同一批函数的 `filters=`（`tax_web_search.build_filters`），十枚开关：`--in-title`／`--precise`／`--tax-type`／`--doc-type`／`--doc-year`／`--doc-no`／`--cwrq-from`／`--cwrq-to`／`--aging`／`--effect-level`（见「命令」） | 值域先过第三步那串常量 |
| 某一栏的全部文件（横截面，不是按词召回） | `tax_gov_list.py`：先 `sync --channel 栏名` 建索引，再离线 `lookup`／`stats` | **必须先建索引**，没建过 `lookup` 报缺索引，那是前置不是失败 |
| 已知地址读正文与注释 | `tax_fgk.fetch_fgk_body`（命令行 `--body`） | 无 |
| 同一文件的现行版与被废止版、关联解读与问答 | `tax_fgk.fetch_associations`（命令行 `--assoc` 逐条现拉） | 详情页的 `articleId`（`article_id_from_url` 从 URL 末段取） |
| 手上只有文号，要回库里对 | 先用 `tax_gov_list.py lookup <文号>`（离线，零请求）；缺索引或要现拉时用 `docType`＋`docYear`＋`docNo` 那一组 | 文号里的文种、年份、数字三件都要有；只有数字时不能用（`docNo` 单发是宽松匹配） |
| 实务材料援引的文号回官方库核对存在与时效 | `tax_answer.check_practice_citations` | 走 `search_fgk` 那一路，不发新接口 |

界面那一路不必启：网页上「范围」「匹配」「日期」「时效」四个控件翻成的就是上面这几枚维度，
换算收在 `tax_server._ui_filters`（`scope`／`exact`／`date_from`／`date_to`／`aging` 五枚入参，
其中 `aging` 不兜默认值），走 search5 的三条路径都调这一个函数，命令行与界面是同一份判据。

**第二步 翻页基准与每页条数先按站点分清楚，再读条数。**

- search5 的 `pageNum` 从 **0** 起算。本项目对外的 `page` 从 1 起，换算只收在 `search_chinatax` 一处。
  基准抄错时的表现最骗人：结构正常、条数正常、翻页数正常，只是每一轮都少了相关度最高的那一屏。
  「企业重组」实测 `pageNum=0` 与 `pageNum=1` 各回 10 条、URL 交集为空，且 0 那一组才是相关度最高的
  一页；发 1 等于整轮检索永远丢掉首屏。命中不足 10 条时（如「企业重组业务所得税处理」共 3 条）
  首屏是唯一一页，丢掉它取回的是空清单，上层会读成"库里没有这份文件"。
  NPC 法规库那一路的 `pageNum` 从 1 起（`subskills/npc-flk/`），两边基准不同，不要照抄。
- `pageSize` **卡在 10**。同一发法把 `pageSize` 换成 10／20／60／100，`total` 一个不换、取回行数都是 10、
  首行都是同一条。所以 `search_chinatax` 里那枚 `max(size, 10)` 只在 `size < 10` 时起作用，
  `size > 10` 取回的还是 10 行，想要更多条只能翻页。flk 那一路相反——`pageSize` 照请求给行
  （「环境保护」发 100 就回满 100 行），两站这一维不能共用一个常数。
- `total` 是命中总数，与"这一页取到几行"是两件事。它挂在 `searchResultAll` 那一层，
  取回的行在同层的 `searchTotal` 数组里（按 `list`／`rows` 取会读到空，这个容器键名踩过一次）。
  NPC 那一路的 `total` 反而不可靠：同一枚「环境保护」在 `pageSize=5` 报 3,118、10／20／50／100 报 224，
  既不是命中总数也不能推页数——这条对照只用来提醒"两边的 `total` 不是一回事"，读数各记在自家 `NOTE.md`。
- 排序只在带检索词的请求上有效。`ORDER_VALUES` 四档都不改 `total`，只换首屏内容：
  `orderBy=1` 是成文日期倒序（本项目 `--order date_desc` 发的就是它），`orderBy=2` 排的是**类别**不是日期，
  `3` 是日期升序、`5` 是相关度。空词那一路四档回同一份首屏，接口自己的默认序恰好新在前——
  所以"整库最新文件"的依据是"空词首屏本身就新在前"这条实测，不是"发 `orderBy=1` 加空词"。

**第三步 收窄只按站点认的那一式提交，越界的回法是 0，不是回基线。**

- 值域四套常量：时效五档 `AGING_VALUES`（全文有效／已修改／全文失效／全文废止／尚未生效）、
  效力等级八档 `EFFECT_LEVEL_VALUES`（法律／行政法规／国务院文件／税务部门规章／税务规范性文件／
  财税文件／其他文件／工作通知）、排序四档 `ORDER_VALUES`、文件类标签十枚 `FILE_LABELS`。
  域外值在 `build_filters` 抛 `ValueError`，停在发出之前。为什么必须拦在这儿：把
  `xxgkEffectLevel` 发成「法规」、`xxgkAging` 发成「有效」，两次都回 `total=0`，同一次跑的基线是 1910——
  越界值不发错误码也不回基线，它和"这一维真的没内容"长得一模一样。
  界面与录入写的是「行政法规」，站点也收「行政法规」：`getSearch.js` 里那段把「行政法规」换成「法规」
  的分支整块是注释掉的，现行页面按原值发。
- **效力等级与时效两维同时发必然归零，不要拼。** 「财税文件」单发 637，加「全文有效」或加「已修改」
  都是 0；同一根时效轴配上税种那一维（`xxgkSonTaxPolicy=增值税` + `全文有效`）正常给 159。
  成因是这一族条目名下根本不录时效（`agingList` 只有空串与字符串 `"null"` 两桶），不是接口的 AND 语义坏了。
  程序拦不住谁去拼这条必然为零的查询，所以判据落在发之前：这两维要拼就只拼一维，另一维回清单或正文去读。
  读数表在 `NOTE.md` 第三节。
- **时效这一维不能拿来当默认**：文件类标签基数下五档之和只覆盖约四成条目，其余该栏为空
  （解读类与其他文件本来就不填）。默认收窄到「全文有效」会把没录时效的现行文件一起筛掉。
- 日期这一对是**成文日期**（`cwrqStart`／`cwrqEnd`），必须发成 `yyyy-mm-dd 00:00:00`／`23:59:59`。
  只给 `yyyy-mm-dd` 时**日期值根本不参与比较，换的是"这一维给了没有"**：全站基数下四种裸写法
  （2024 全年、只发起点、只发终点、换成 2025 上半年）都回同一个 2432，带整点时间戳才收到 207；
  文件类标签基数下这一过滤是恒等（1910 → 1910），看着像没生效，其实是那十类条目本来就全录了成文日期。
  `build_filters` 就是按站点那一式补齐的，命令行 `--cwrq-from`／`--cwrq-to` 拿到的永远是生效的那一式。
  另一族 `startTime`／`endTime` 在页面脚本里恒发空串，本层不接。
  **别把这三枚年份键混成一件事**：`docYear` 键在**发文字号里的年份**，`xxgkFormulatedYear` 键在
  录入的制定年份，`cwrq` 是成文日期。空词整档取数时 `docYear=2024` 回 82、`xxgkFormulatedYear=2024` 回 90，
  差额里能逐条指出的那一条是《中华人民共和国增值税法》——它文号空、只被后者收到。
  与 NPC 那一路的**公布日期**（`gbrq`）也不是同一件事，跨源比日期时别并成一栏。
- `docType` 的取值不带空格：页面上印的是"财政部 税务总局公告"，接口对带空格的写法是宽松误命中
  （配 `docYear=2023` 归 0，去空格才有 25 条）。`--doc-type` 自动去掉内部空白，命令行照页面写法给也能过。
- 行业那一维的键名是 `industrytypename`，不是 `xxgkIndustryType`：前者在「增值税」×文件类标签的基数下
  回 70（社会民生），后者回 0——`xxgkIndustryType` 只出现在响应的分面侧，不是请求参数。
  这一维仍未接进命令行，接的时候按 `NOTE.md` 那十二档读数给值。
- `label` 白名单 `FILE_LABELS` 那十枚与站点「综合」tab 提交的那一串是**同一集合**（双向差集为空），
  不是本仓库自创的收窄；代价是标在「视频政策解读」「图片政策解读」上的法规库条目会被漏掉，
  要连它们一起取才加 `--all-labels`。这一维换的是翻页窗口的成分（「转让定价」全站前 3 屏 0 条法规库条目、
  收窄后 18 条全部取回），所以 `tax_fgk` 的早停结果必须先确认发的是哪一份 `label`。
- `searchSiteName=GSFFK` 换不了库：实测只把 1910 收到 1893，它是页面带出来的一枚标签，不是索引开关。
  代码里 `FGK_MARKER` 判的是**条目地址的主机名**（含它的那条 `source_label` 记成"税务总局法规库"），
  跟这枚参数不是一回事，别拿它当换库的证据。
- 响应里八个 `*List` 分面挂在 `searchResultAll` 那一层、不在根上（按根节点取会读到 None，
  手工复测把 None 记成"站点没回分面"就是这么来的）。它们是**选维度的依据、不是命中数**：
  `columnList` 各桶之和比 `total` 大一套基数；`industrytypenameList` 回的是 JSON 字符串、要再解一次，
  且这一枚分面不随 `label` 白名单换基数。其中 12,924 条「新闻发布」就是深页读不出法规的成因——
  把 `column=政策法规` 或 `xxgkSonTaxPolicy=<税种>` 下推到检索侧才是把窗口对准法规文件。
  栏目与标签是两维、两套数：`label` 收的是条目自带的标签名（`labelList` 那 159 桶），
  发这一桶里没有的名字（「新闻」「稽查」「曝光台」）回 0，换成站点真实的那枚「新闻发布」
  就收窄得出内容（「增值税」2 条）；同一枚「新闻发布」在 `columnList` 是 12,924。
  所以案例那一路走 `column`（`tax_cases.CASE_COLUMN=5741`）、并把 `label` 发成空串，
  依据是栏目覆盖得全、案例条目不在文件类那十枚标签里，不是"标签只认那十枚"。

**第四步 正文层与清单层各装一半，说的时候别说成一半。**

- 详情页回 `Content-Type: text/html` 不带 charset，必须按 UTF-8 解，否则中文静默变乱码而 HTTP 仍是 200；
  正文容器 `div.arc_cont`（导航栏里也含这个类名，要取 `zscont` 之后的第一处），注释容器 `div.zscont`。
- 表只在正文层：容器里的 `<table>` 有两种页形（Word 粘贴的每格包 `<p>`、包图片的单格表），
  `tax_fgk._table_lines` 铺成 Markdown 行（colspan 补空列、rowspan 把值带到它盖住的每一行）。
  **清单层读不出执行期限，正文层读得出**（`tax_evidence.expiry_of`／`period_start_of`），
  所以"这份没写期限"要先说清是在哪一层读的。
- 附件只在清单层，且只有名与链：接口 `appendix` 每项四个键里 `appendixContent` 取样全为空串，
  随文的申报表、税率表**只在附件里**，正文容器取不到那张表。正文没表不等于没有表。
  `tax_web_search._attachments` 只收名与链都在的项——只给个文件名点不开，等于没给。
- 正文是视频或图片的条目带 `media_only: true` 与 `media_urls`（`tax_fgk._media_urls` 把相对、
  站根、协议相对三种地址都拼成可点开的绝对链接，跳过 `data:` 占位图）——这不是取失败，重试也没用，
  答案给不出条文时就该给出那几个素材地址。只有真正取失败才进 `body_error`，
  取失败只报原因，不得据标题推测条文。`media_urls` 是正文层字段，只有 `--body` 那一轮才有，不进清单缓存。
- 外部资料记着的那个取正文接口 `GET /jee2/download/query.jsp` 已下线（www 与 fgk 两个域名、
  两种 id 取值一律 404），正文仍走详情页那一路。

**第五步 两个域名的分工，混着发就 404。**

- 检索与关联接口在 **www 域**：`search5/search/s` 是唯一的检索落点，法规库结果页的 AJAX 打的也是它
  （`getSearch.js` 里 fgk 域那一行写在 www 行的下一行、整行注释掉，从没发出去过）。
- `POST /queryManuscriptAssociation`（表单参数 `id=` 详情页 `articleId`）只在 www 域回 200，
  同一 id 打 fgk 域 404；返回里分 `policyDocument`、`policyInterpretation`、`policyGuidance`、`policyQA`
  几组，其中 `policyDocument` 每条带时效，据此能看出关联到的是全文有效还是已废止的旧版。
  静态详情页 HTML 里这几组是空的，正文中的 `<a href>` 只能给出零星线索，所以关联走接口。
- 反过来，关联结果里的 `/zcfgk/…` 相对链接要拼 **fgk 域**才取到正文（拼 www 回 404）。
- 正文页与清单页两域各有可达形态，栏目页直链写在 `tax_gov_list.CHANNEL_PAGES`：那张表是逐个 GET 量出来的
  数据不是推导——七栏里只有「税务部门规章」是 `list.html`，其余六栏是 `listflfg.html`。

**第六步 整栏横截面走清单源，它是封闭集合。**

- `POST www.chinatax.gov.cn/getFileListByCodeId`，按 `channelId` 分栏目、每页 100 条，
  每条带 title／url／发布时间，另有六枚政策元数据（`META_KEY_FIELDS`：发文字号、效力等级、
  时效性、成文日期、税费类型、发文单位——按稳定的 key 取，`resultList` 里顺序不固定）。
  无需 Cookie、无需 UA、实测 20 次连发不限流。
- 七栏与整栏条数：税务规范性文件 1,925、财税文件 1,532、其他文件 488、法律 75、行政法规 65、
  国务院文件 35、税务部门规章 86；默认栏目「税务规范性文件」。
- 这一路补的是检索源给不了的东西：**一栏之下全部现行文件的封闭清单**，每条自带官方时效性分类状态。
  实测出现的时效取值只有五种，与 `AGING_VALUES` 逐项相同；「财税文件」「其他文件」两栏这一列恒空是
  官方本就不填（整栏翻到底 0 条真值），只如实记录、不报警，**文号列变空才是改版预警**。
- 建索引走 `tax_sync.ListSynchronizer`（分页爬全 → 集合 SHA1 diff → 变了才重建），SHA1 覆盖
  url＋时效性＋发文字号＋title，所以同一份文件时效翻转也会检出并触发重建。`lookup`／`stats`／
  `seed-cache`／`missing` 都是离线子命令，不再发请求。
- **它不在多源聚合的检索源里**（`tax_aggregator.DEFAULT_SOURCES` 没有这一路）：检索源回答"跟这个词相关的文件"，
  这一路回答"这一栏全部的文件"，两者不互相顶替。连发限流的记录在主技能源缺陷登记的
  「限流记录的实测台账」那一处单点维护，本文件不另立一份。

**第七步 这一层只负责取。** 三站给的都是**录入值**，字段名映射见下面「站点底座」那张表最后一行：
定级层按 `status`／`effect_level` 归档，比按标题形态猜准。两处别读反：
`xxgkAging` 那一栏约六成条目为空，空读成"站点没录"，不读成"这份没有时效"；
`--aging`／`--effect-level` 是**筛这一维**，不是**读这一维**，筛之前先确认这一族条目填不填（第三步那两条）。
文号回库核对（`check_practice_citations`）打的也是这一层，`not_in_library` 说的是
"我们查的那份库里没对上"，不是那份文件不存在。

## 命令

命令行与函数同一份判据；每个开关打到哪一枚接口参数，写在 `tax_web_search.py` 与 `tax_fgk.py` 的
`--help` 回显里，逐项读数写在 `NOTE.md`。全部只发 HTTP，零模型调用。

```bash
# 总局站点检索（覆盖站点与法规库条目的录入元数据）
python scripts/tax_web_search.py "增值税" --size 10
python scripts/tax_web_search.py "增值税" --in-title --precise --tax-type 增值税
python scripts/tax_web_search.py "研发费用" --doc-type "财政部 税务总局公告" --doc-year 2023 --doc-no 7
python scripts/tax_web_search.py "增值税" --cwrq-from 2024-01-01 --cwrq-to 2024-12-31
python scripts/tax_web_search.py "增值税" --aging 全文有效 --size 8      # 这一枚与下一枚不要同发
python scripts/tax_web_search.py "增值税" --effect-level 财税文件 --size 8
python scripts/tax_web_search.py "小微企业" --all-labels                 # 关文件类标签白名单
python scripts/tax_web_search.py "增值税" --order date_desc --size 8     # 成文日期最新在前（orderBy=1）

# 法规库条目（规章、公告；翻页自适应）
python scripts/tax_fgk.py "增值税" --size 10
python scripts/tax_fgk.py "增值税" --size 3 --body                        # 连正文一起取（每条多一次请求）
python scripts/tax_fgk.py "转让定价" --size 3 --pages 8                   # 关自适应，严格翻满
python scripts/tax_fgk.py "研发费用" --size 1 --assoc                     # 逐条查关联文件/解读/问答
python scripts/tax_fgk.py "增值税" --size 5 --cache                       # 清单缓存 TTL 1 小时，正文仍现拉

# 整栏横截面（清单源；先建索引，之后离线查）
python scripts/tax_gov_list.py sync --channel 财税文件                    # 爬全并重建索引
python scripts/tax_gov_list.py sync --check                              # 只比 total，不爬全
python scripts/tax_gov_list.py lookup 国家税务总局公告2026年第18号         # 按文号定位（离线）
python scripts/tax_gov_list.py lookup 增值税 --aging 全文有效 -n 20        # --aging 只认 AGING_VALUES 五档
python scripts/tax_gov_list.py stats                                     # 栏目/版本/条目/覆盖率/时效分布
python scripts/tax_gov_list.py missing                                   # 有官方链接却没进缓存的条目

# 值域常量回显（零请求）
python -c "import sys;sys.path.insert(0,'scripts');import tax_web_search as W;print(W.AGING_VALUES);print(W.EFFECT_LEVEL_VALUES);print(W.ORDER_VALUES);print(W.FILE_LABELS)"
```

`--aging` 与 `--effect-level` 同发的结果是 0 条而不是交集，`_filter_note` 会写明分不清是拼窄还是没有；
放宽一维重取才有结论。维度拼到 0 条时不要写"库里没有这份文件"——0 条的四种成因见下面「边界」第 1 条。

## 站点底座

| 项 | 读数 | 依据 |
|---|---|---|
| 谁在发请求 | 两域都是纯 HTTP：`requests` 直连，无凭据、无 Cookie、不启浏览器 | 本层取数走 `tax_web_search`／`tax_fgk`／`tax_gov_list`，实测不带登录态可取 |
| 检索接口 | `GET https://www.chinatax.gov.cn/search5/search/s`，`siteCode=bm29000002`；`pageNum` 从 0 起、`pageSize` 恒 10 | 参数在 `tax_web_search.SEARCH_URL`/`search_chinatax`，基准换算只收在这一处 |
| 清单接口 | `POST https://www.chinatax.gov.cn/getFileListByCodeId`，`channelId`＋`page`＋`size=100` | `tax_gov_list.API`/`CHANNELS`；七栏条数见第六步 |
| 正文与关联 | 正文在详情页 HTML（`fetch_fgk_body`）；关联走 `POST /queryManuscriptAssociation`，只在 www 域 | 见第四、五步；`query.jsp` 那一路已下线 |
| 值域的来源 | 时效／效力等级／排序／标签四套值域在页面 JS 的下拉框里，本仓库转写成常量 | 依据是能再取回的 `getSearch.js` 与 `public.js`，逐行读数见 `NOTE.md` 第四节 |
| 每页条数 | search5 恒 10 条；清单源每页 100 条；NPC 那一路照请求给行 | 见第二步，两站基准不同 |
| 编码 | 接口回 JSON 带 charset；详情页 HTML 不带 charset，须按 UTF-8 解 | 见第四步 |
| 域名分工 | 检索与关联在 www 域，正文页与清单页两域各有可达形态；`FGK_MARKER` 按条目主机名归类 | 见第五步 |
| 录入项 → 字段 | `govDoc.docNum`→`document_number`（空时回落到标题／摘要正则抠号）、`xxgk_aging`→`status`＋`status_from`、`xxgk_effectLevel`→`effect_level`、`cwrq`→`publish_date`（成文日期）、`pubDate`→`date`、`appendix`→`attachments` | 都在 `tax_web_search.search_chinatax` 的组装段里；`status_from` 存在的意义就是让下游知道这一格是录入值不是推断值 |

## 边界：这一层不给什么

1. **不给"0 条就是没有"的结论**。0 条至少有四种成因：检索词真的没命中、维度拼窄了（效力等级×时效
   同发必 0）、值不在站点认的域内（这一种也回 0，不发错误码）、日期或文种发成了不参与比较的形态。
   前三条分别靠放宽一维、按值域拦停、补整点时间戳来处置；判不动时 `_filter_note` 那句要照抄进答案。
2. **不给整库全量，也不给整栏横截面**。search5 是按词召回的开放集合，每页 10 条、`tax_fgk`
   自适应最多 20 页；"现行有效的规范性文件都有哪些"这类横截面走 `tax_gov_list.py`，
   它要先建索引，且不在多源聚合的检索源里。
3. **不给地方规定**。这三路取的都是国家税务总局那两个域面上的条目（`siteCode=bm29000002` 的站点检索、
   政策法规库正文与详情页、政策文件库那七栏），本层没有地方文件的入口；地方口径要另走地方局站点那一路。
4. **不给执行口径与实务认定**。税屋、公众号、中国税网给的那一层是"口径怎么执行"，
   这一层给的是文件本身；实务材料里援引的文号要回这一层核对，核对结果的含义见第七步末那句。
5. **不给时效判定**。给的都是录入值，`xxgk_aging` 六成为空、`--aging` 是筛不是读；
   判定在 `tax_evidence.judge_validity`，观察时点要显式 `--at` 传入（`tax_answer.py` 与
   `tax_evidence.py` 两个命令行都有这一枚）。
6. **不给"正文取不到就是这份没内容"**。`media_only` 的正文本来就是视频或图片，`body_error`
   才是取失败；取失败只报原因，不得据标题推测条文。
7. **不给规范层级与主依据挑选**。排序与分桶只到"取回哪些条"为止，`effect_level` 与
   `document_number` 原样带回去；层级归 `tax_evidence.rank_of`、主依据归 `tax_evidence.pick_primary`。
8. **不给未接的维度**。行业（`industrytypename`）、`xxgkTaxPolicy`、`xxgkFormulatedYear`、
   `likeDoc`、`startTime` 那族日期都没接进命令行；要用的话按 `NOTE.md` 对应小节那几行的读数给值，
   别凭记忆加参数。

## 命令依赖

上面的命令调 `scripts/tax_web_search.py`、`scripts/tax_fgk.py`、`scripts/tax_gov_list.py` 三个模块。
它们彼此有内部次序（`tax_fgk` 与 `tax_gov_list` 都从 `tax_web_search` 取 `FGK_MARKER`、
`AGING_VALUES`、`aging_of` 这些常量与口径，值域只有一套），并共用主技能 `scripts/` 下的
`tax_http`（统一发请求与短错误行）、`tax_cache`（清单缓存）、`tax_sync`（清单源的爬全与集合 diff）、
`tax_cited`／`tax_terms`（`seed-cache`／`missing` 那两个离线子命令用）。这些模块不只服务这两域：
多源聚合、命令行与网页两路、案例检索都从这里取数，搬进子技能就要在两边各维护一份，
所以留在主技能 `scripts/` 下。**除这几个模块之外，本子技能不依赖主技能的任何文档**：
判据、读数、命令、值域、复测入口都在本目录这两份文件里；只有第六步提到的那份限流台账是例外，
它在主技能登记是因为 `tests/test_rate_limit_ledger.py` 逐格解析那一份表，两处各记一份必漂。
