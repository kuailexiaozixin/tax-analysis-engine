# 各源命令、参数与失败表现

> 本文件是 `SKILL.md` ③⑤ 的命令细节下沉版：SKILL.md 只留工作流主线与判据，
> 具体到某个源怎么调、参数怎么取、失败了怎么办，查这里。
> 文中每条命令都受 `tests/check_doc_cli.py` 校验：出现的脚本名与其参数必须在代码里存在。

## 目录

- [L1 NPC 法规库](#l1-npc-法规库上位法)
- [L2 总局法规库与站点](#l2-总局法规库与站点规章与公告)
- [L3 360 站内搜索](#l3-360-站内搜索地方口径)
- [L4 税屋与公众号](#l4-税屋与公众号实务案例与解读)
- [法规详情与全文下载](#法规详情与全文下载)
- [检索词归类](#检索词归类)
- [缓存控制](#缓存控制)
- [命令一览](#命令一览)
- [NPC 限流：已由代码强制串行](#npc-限流已由代码强制串行)
- [各源失败表现与处理](#各源失败表现与处理)

---

## L1：NPC 法规库（上位法）

```bash
# 政策查询 — 标题检索
python scripts/tax_search.py "<关键词>" --status 3 --size 20

# 已知法规名 — 精确匹配，本体法名已知时必须用它
python scripts/tax_search.py "<完整法规名>" --exact --status 3

# 最新政策 — 按公布日期排序
python scripts/tax_search.py "<关键词>" --sort date --size 20

# 特定时间段政策
python scripts/tax_search.py "<关键词>" --from 2024-01-01 --sort date

# 看已废止版本（用户明确要求"含已废止/看历史版本"时才用）
python scripts/tax_search.py "<关键词>" --status 1 --size 20
# 已修改 / 尚未生效同理：--status 2、--status 4
```

`--status` 的取值与默认值：`1` 已废止、`2` 已修改、`3` 现行有效（**默认**）、`4` 尚未生效。
**不带 `--status` 等于 `3`，不等于"全部状态"**——命令行一次只能要一种状态，"全部时效"
在 CLI 上表达不出来。要一次覆盖多种时效，只能按需要的状态各查一次；网页端可以做到
（时效选"全部"时前端传 `"status": null`，服务端据此不加时效过滤）。

**取 L1 时的两条硬规则**，违反其一就会取错依据：

1. **本体法名已知时必须走精确检索**（`search_type=1` / `--exact`）。模糊检索
   按发布时间而非相关度排，"企业所得税"模糊检索首条是企业破产法，
   "中华人民共和国企业所得税法"首条是《中华人民共和国宪法》，主依据会被选错。
   程序已在返回前按检索词在标题中的位置重排（`_title_match_rank`）把本体法置顶；
   重排只作用于单页，程序会先多要一页再排，所以 `--size` 不要调太小。
2. **不要拿用户原话当检索词**。整句丢进标题检索取回的是含通用字的无关法规
   （"研发费用加计扣除"会带到《诉讼费用交纳办法》，命中的是"费用"），
   必须先用 `resolve_tax_type` 归到具体税种/专题，再按税种映射表选词。

`--sort date` 的行为要说清两点，否则会把"最新"读错范围：

- 排序在本地做。NPC 收到 `orderByParam={order:-1,sort:gbrq}` 并不真按发文时间排
  （实测"增值税"回 2024-12-25、2011-01-08、1994-02-22、2025-12-25，明显被打散）；
  不传排序参数时是"接口自己钉住首条，其后大致降序"，也不可信。所以程序取回窗口后
  自己按公布日期降序排一次。
- 排的是**取回的那个窗口**，不是全库。要框定"最新"就配 `--from` 用日期区间收口，
  别只靠排序。
- 给了 `--sort date` 就不再按标题命中重排，本体法不会自动置顶。既要本体法又要
  最新口径时，分两次查：一次 `--exact` 取主依据，一次 `--sort date --from <年份>` 取新文件。
- 多源聚合同样吃这个参数（`tax_aggregator.py --sort date`，网页上是"排序=公布时间"）。
  时间序下**不再按权威度分层**：跨源比时间时各源的日期字段统一到 `publish_date`，
  缺日期的条目排在末尾。

`--from`／`--to` 这一对是 **公布日期**（`gbrq`）区间，四条行为要先知道，否则会把
"收窄"读成别的：

- 格式必须是补零的 `YYYY-MM-DD`。`tax_search.check_iso_date` 在**发请求之前**校验，
  `2024-1-1`、`2024-13-01`、`20240101` 都直接报错退出（CLI 退 2，`/api/search` 报 400），
  不会静默当成"这一维没填"再回一份基线命中。
- 只给一端就按 `DATE_CEIL`（缺上界）／`DATE_FLOOR`（缺下界，`0001-01-01`）补另一界。
  只给上界不补下界就是空转：2026-10-03 实测「增值税」`--to 2020-12-31` 与不带日期都是
  45 条、首条 2025-12-25，补界后 38 条、区间内最大 2020-08-04。下界取 `0001-01-01`
  而不是 1949-10-01 是量过的：两个下界同为 38 条，取更早的不额外排除任何一条。
- **精确检索（`--exact`）带上日期时，接口会丢掉检索词**，只按区间回一叠法律清单
  （实测「中华人民共和国增值税法」`--from 2026-01-01` 回 88 条、首条
  《民族团结进步促进法》，88 条里没有一条含"增值税"，同词不带日期是 2 条）。
  程序不拒绝这个组合，改成取回后按「标题是否含检索词」复核：输出的条数是复核后的，
  接口原报的条数留在 `source_total`，并打一句 `_date_note` 说明成因。真要按日期在全库
  收窄，改用税务总局／法规库那两路（`tax_web_search` 的 `cwrqStart`/`cwrqEnd`，
  语义是**成文日期**，与 NPC 的公布日期不是同一件事）。
- 多源聚合的同一对参数走 `tax_aggregator.py --from/--to`：NPC 与总局由接口自己收窄，
  360／税屋／微信公众号没有日期参数，是在**本轮取回的条目窗口内**按条目自带日期补筛，
  命令行会为每个补筛源打一行「取回／区间内／区间外剔除／无日期保留」计数，
  JSON 里对应 `_date_filter.local_window`。窗口外该源仍可能有区间内的文件，
  补筛到 0 条不能读成"该源在这个区间里没有内容"。

`--scope fulltext`（NPC 正文检索）可用它定位相关法规：程序给接口加了
`sort=score` 降序（接口每条都带 `score` 字段却不用），再按"检索词整段出现在
标题"本地重排。但它本质是全文分词匹配，短词仍会被通用字带偏，一律带
`_reliability: medium`。**确定某条规则的准确归属只能用 `--scope title`
或 `--exact`**，见 `source_defects.md`。

## L2：总局法规库与站点（规章与公告）

```bash
python scripts/tax_fgk.py "<关键词>" --size 10            # 只要目录（翻页自适应）
python scripts/tax_fgk.py "<关键词>" --size 3 --body      # 同时取正文
python scripts/tax_fgk.py "转让定价" --size 3 --pages 8    # 严格翻满 8 页（关自适应）

# 总局站点检索（覆盖总局站点与 fgk 法规库）
python scripts/tax_web_search.py "<关键词>" --size 10
```

**必须翻页，不能只读第一页**。总局检索接口把 `pageSize` 卡在 10 条，传 20/60/100
都只回 10 条，只能按页取。法规文件在结果里排得靠后，只读第 1 页会把
"库里没有"错报成"确实没有"。

**翻页基准是 0 起算，这一位不能错**（`search5` 的 `pageNum` 从 0 开始，本项目
对外的 `page` 从 1 开始，换算收在 `tax_web_search.search_chinatax` 一处）。
基准用错时的表现最骗人：返回结构正常、条数正常、翻页数正常，只是每一轮都少了
相关度最高的那一屏；命中不足 10 条时首屏是唯一一页，丢掉它就取回空清单。
NPC 法规库那个接口（`tax_search.py`）经实测是**从 1 起算**，两个源基准不同，
照抄其中一个去改另一个就会出这种错位。

取回的条目直接带三个录入项，别再写"未标明"（`tax_web_search` 落到
`document_number`/`status`/`effect_level`，`tax_fgk` 逐条透传给定级层）：

- **文号** `document_number`：接口给 `govDoc.docNum`，没有才从标题、摘要里按
  "X年第N号"形态提取。主依据那一行会连着文号一起印出来。
- **时效** `status`：接口给 `xxgk_aging`（如"全文有效"），来源记在 `status_from`，
  时效判定就按它走，不靠标题猜。
- **效力级别** `effect_level`：接口给 `xxgk_effectLevel`（如"税务规范性文件"），
  定级时优先按它归档，比按标题形态猜准。

条目另有**附件**一栏（`attachments`）：接口 `appendix` 是数组，每项固定四个键
（`appendixName`/`appendixType`/`appendixUrl`/`appendixContent`），`tax_web_search._attachments`
收成 `{name, type, url}`，名字或链接缺一项的那条丢掉——只给个文件名点不开，等于没给。
2026-10-04 本机实测：「成品油消费税」「增值税」「小型微利企业」三个词、
`docType=财政部税务总局公告`，取回 16 条里 3 条带附件，这 3 条的 `appendixContent`
全是空串——随文的申报表、税率表只在附件里，正文容器取不到那张表。所以正文没表
不等于没有表，要连着附件名一起说（CLI 印成「附件: 名称（链接）」）。这一栏是清单层
字段，打开 `--cache` 时随清单一起缓存。

默认**按需自适应**：连续 3 页没捞到新的法规库条目就收尾（上限 20 页）。默认检索范围
已是文件类标签（见下文「文件类标签下的分布」），还连着 3 页空手，剩下的只是与检索词
沾边的别的文件，硬翻到底只是白烧请求。代价是**可能漏掉间隔 3 页以上的条目**；若出现
`total` 很少但 `total_hits` 很大的可疑情形，就用 `--pages N` 显式指定页数——那时会关掉
自适应、严格翻满，但翻得越深相关性越差。

返回里三个字段合起来看：`total_hits`（命中总数）、`pages_scanned`（实际翻了几页）、
`stopped_early`（是否因自适应早停）。三者能区分"确实没有"、"翻到上限也没有"
和"自适应提前收了尾"——后者值得用 `--pages` 再确认一次。

详情页 HTML 里有完整正文，但该站不声明 charset，必须按 UTF-8 解码才能读出
中文；程序已处理，正文容器是 `div.zscont`（注释）与 `div.arc_cont`（正文）。
加 `--body` 逐篇取正文。

容器里的 `<table>` 铺成 Markdown 行（`tax_fgk._table_lines`：colspan 补空列、
rowspan 把值带到它盖住的每一行、行宽补齐、全空行丢弃、单行表不插 `---` 分隔行），
表格前后的文字按原顺序接在表行两侧。这类页不算多：2026-10-04 本机扫两批共 75 篇
法规库详情页（检索词取自「税目税额表」「出口退税率」等），35 篇那批 6 篇含 `<table>`、
40 篇那批 2 篇，合计 8 篇。

摊平写法丢的是行列对应。真实页面的表分两种形态：Word 粘贴产物每格包着 `<p>`，
摊平就成"一格一行"——c5204270 那张税则表 191 个单元格、152 个 `<p>`，摊平成 152 行
（表头四格成了 '序号'、'商品名称'、'税则号列'、'备注' 四行），铺表后是 49 行
（48 个数据行＋分隔行），表头一行就是 `| 序号 | 商品名称 | 税则号列 | 备注 |`；
另一种是包着图片的单格表（c5211628、c5208804 各两张，每表 1 个单元格、0 个文字），
摊平和铺表都回空，不产噪声。单元格不带 `<p>` 时同格文字才会首尾相接成一串字，
这 8 篇里没有这种形态，但那是 `_plain_text` 只在 `</tr>` 与 `<br>` 换行的直接后果，
用例按两种形态都钉了。c5194303 那张 24 行 `<tr>` 的表铺成 25 行 Markdown（含分隔行），
rowspan 的"产品种类"被带到它盖住的每一行：

```
| 序号 | 产品种类 | 产品范围 | 征收标准(元/台) |  |
| --- | --- | --- | --- | --- |
| 1 | 电视机 | 阴极射线管（黑白、彩色）电视机 | 13 |  |
```

末列恒空不是解析错：这些页的表每行末尾多一个 `width: 1.32777%` 的空 `<td>`（Word 的
间距格），补齐行宽后它留成一列空白。铺表不吞字：上表那 6 篇真实含表的页面里，
表内摊平行共 642 行，逐行在新输出里都找得到，0 行丢失（同批实测）。

跨格拼出假执行期限是另一层，属正则口径而不是已发生的缺陷：`tax_evidence._EXPIRY_RANGE`
只容空白间隔，摊平把左右两格的「自2023年1月1日」「至2027年12月31日」拼成一句，就会
凭空多出一段期限；上面 8 篇里没有出现这种两端分格的表，所以本机没取到活例。
改成 Markdown 行后两格隔着 `|`，这条路走不通了；同一格内写全的区间照常读得出。
`tests/test_tax_fgk.py::test_table_cell_split_does_not_fabricate_a_period` 两头都钉住
（拼在一起抽得出、拆开抽不出），它防的是将来换页形时踩上。

正文是视频/图片的条目会在结果里带 `media_only: true`
（CLI 里显示成"该条正文是视频/图片"）——**这不是取失败，重试也没用**，
别去引它的条文；只有真正取失败才会进 `body_error`。这类条目另带 `media_urls`
（`tax_fgk._media_urls`：正文容器里 img/video/audio/source 的 `src`，相对地址、
`/` 站根地址、`//` 协议相对地址都拼成可点开的绝对链接，`data:` 占位图跳过，去重），
取不到 `src` 就不带这个键。它是"没有文字可引时该去看的那张图"，答案里给不出条文
就该给出这个地址。实测口径：2026-10-04 用 `--all-labels` 搜「研发费用加计扣除」
翻 3 页取正文，5 条 media_only 全部带出素材地址（4 个 `.mp4`、1 个 `.jpg`，
形如 `…/c5213157/5213157/files/PdKQSULu.mp4`），抽查那两个直链都回 HTTP 200，
`Content-Type` 分别是 `video/mp4`（19712228 字节）与 `image/jpeg`（971707 字节）。
`media_urls` 是正文层字段，只有 `--body` 那一轮才有，不进清单缓存。

### search5 的可发参数与分面字段

下面这批数字是 2026-10-01 在本机逐个单发参数实测的，检索词固定「增值税」。整表跑完
后又逐行重发一次比对：21 行取值复现，唯一例外是 `likeDoc=0` 那行，先后两次分别是
16199 与 16919，这一档不能当常量引用（16919 恰好等于下表 `columnList` 各桶之和，两次
之差多半出在索引状态，本机没有更细的证据）。

**已接进命令行的有七组**（2026-10-01 起五组，2026-10-02 补 `xxgkAging`、
`xxgkEffectLevel`）：`wordPlace`、`participleRule`、`xxgkSonTaxPolicy`、
`docType`+`docYear`+`docNo`、`cwrqStart`+`cwrqEnd`、`xxgkAging`、`xxgkEffectLevel`，
都由 `tax_web_search.build_filters` 拼参数、`search_chinatax(filters=…)` 并入请求，
命令行开关见下文「search5 收窄维度的命令行」。另有两项不是收窄维度而是检索选项，
走 `search_chinatax` 的关键字参数：`label` 默认发 `FILE_LABELS` 那十个文件类标签
（`--all-labels` 关），`orderBy` 由 `--order` 选（默认相关度）。仍未接的是 `column`、
`xxgkTaxPolicy`、`xxgkFormulatedYear`、`xxgkIndustryType`、`likeDoc`、`searchWordMd5`。
往命令行加新维度时按行核对这张表，不要凭记忆改。

### 文件类标签下的分布（2026-10-02 实测，检索词仍是「增值税」）

上一张表的基数是全站（13152 条）。默认范围换成 `FILE_LABELS` 后基数变成 1908，
两维的取值域就是照这一行实测钉的：

| 发送的参数（叠在 `label=FILE_LABELS` 上） | `total` | 备注 |
|---|---|---|
| 基线：`label=FILE_LABELS` | 1908 | 全站同词 13152 |
| `wordPlace=1`（仅标题） | 908 | 界面「范围」那一栏的默认就是它，不是全站同维的 2998 |
| `xxgkAging=全文有效` | 252 | 五档之和 743，即 1165 条（约六成）该栏为空 |
| `wordPlace=1` + `xxgkAging=全文有效` | 146 | 界面把范围与时效两栏一起收窄时的实际命中（2026-10-02 经 `/api/search` 实测） |
| `xxgkAging=已修改` | 163 | |
| `xxgkAging=全文失效` | 9 | |
| `xxgkAging=全文废止` | 318 | |
| `xxgkAging=尚未生效` | 1 | |
| `xxgkEffectLevel=法律` | 9 | 八档之和 1626 |
| `xxgkEffectLevel=行政法规` | 10 | |
| `xxgkEffectLevel=国务院文件` | 9 | |
| `xxgkEffectLevel=税务部门规章` | 30 | |
| `xxgkEffectLevel=税务规范性文件` | 681 | |
| `xxgkEffectLevel=财税文件` | 636 | 与全站单发同值，财税文件全在标签白名单内 |
| `xxgkEffectLevel=其他文件` | 61 | |
| `xxgkEffectLevel=工作通知` | 190 | |
| `xxgkEffectLevel=财税文件` + `xxgkAging=全文有效` | 0 | 两维同发必然归零，见 `source_defects.md` |
| `likeDoc=1` | 1908 | 与基线同值，这一维在本接口上不起作用 |
| `xxgkIndustryType=科技创新` | 0 | 2026-10-02 四种组合都测过：全站×增值税、全站×空检索词、白名单×增值税、白名单×空检索词，一律 0（同基数不发这一维依次是 13152 / 43105 / 1908 / 5676）。行业维在本接口不可用，接进来只会把命中清零 |

时效这一维**不能拿来当默认**：六成条目该栏为空，默认收窄到「全文有效」会把没录
时效的现行文件一起筛掉。网页端的「时效」控件因此只在税务总局／法规库两个源下
给五个文本值，且默认「全部」（`tax_server._ui_filters` 的 `aging` 参数不兜默认）。

`label` 白名单换的是翻页窗口的成分，实测两例（`tax_fgk._scan_list`，自适应早停、
最多 20 页）：

| 检索词 | 全站 | 文件类标签 |
|---|---|---|
| 转让定价 | 181 条命中，前 3 屏 0 条法规库条目，自适应收尾得 **0** 条 | 18 条命中，**18** 条全部取回 |
| 小微企业 | 4822 条命中，首屏十条是 亚洲／各地动态×3／媒体视点×4／视频图解／视频政策解读 | 184 条命中，首屏十条全是文件 |

代价：标在「视频政策解读」「图片政策解读」上的法规库条目会被漏掉（2026-10-02 实测
「研发费用加计扣除」全站前 3 页有 7 条这一类）。那类页面回的是 `media_only` 空正文，
本来就引不了条文；要连它们一起取，命令行加 `--all-labels`。

| 发送的参数（单发） | `total` | 实测备注 |
|---|---|---|
| 基线：只有检索词 | 13152 | |
| `wordPlace=1` | 2998 | 仅标题；0 是全文，也是默认 |
| `participleRule=5` | 12963 | 精准；0 是模糊 |
| `searchSiteName=GSFFK` | 12963 | 与上一行同值属巧合，两个键不是一回事 |
| `indexCode=1` | 13152 | 单发不改变命中 |
| `xxgkEffectLevel=财税文件` | 636 | 八档取值与分布见下表 `effectLevelList` |
| `xxgkAging=全文有效` | 252 | 五档取值与分布见下表 `agingList` |
| `xxgkTaxPolicy=税收政策` | 1205 | 五档加一个 `null` 桶，见下表 `taxPolicyList` |
| `xxgkSonTaxPolicy=增值税` | 1016 | 收窄到基线的约十三分之一 |
| `xxgkFormulatedYear=2024` | 27 | |
| `cwrqStart` 与 `cwrqEnd`（2024 全年） | 207 | 与上一行差一个量级，两个字段不是同一个来源 |
| `xxgkIndustryType=金融业` | 0 | 外部资料记了 11 档行业，本机只测了这一档 |
| `docType=国家税务总局公告` | 330 | 取值不带空格：页面上印的是"财政部 税务总局公告"，接口对带空格的写法是宽松误命中（实测「财政部 税务总局公告」配 `docYear=2023` 归 0，去空格才有 25 条）。`--doc-type` 会自动去掉内部空白，命令行上照抄页面带空格的写法即可 |
| `docType=财税` + `docYear=2018` | 20 | |
| `docType=财税` + `docYear=2018` + `docNo=119` | 1 | 定点命中财税〔2018〕119号 |
| `docYear=2018`（单发） | 54 | |
| `docNo=119`（单发） | 4 | 脱离文种与年份的编号是宽松匹配，不能当唯一键 |
| `column=政策法规` | 1626 | 栏目共 15 个取值，见下表 `columnList` |
| `label=财税文件` | 636 | 与 `xxgkEffectLevel` 同值 |
| `label=文字政策解读` | 272 | |
| `likeDoc=0` | 16199 / 16919 | 关掉相似文档折叠；基线是折叠过的。两次实测取到不同值，见上文 |
| `searchWordMd5=<任意 32 位串>` | 13152 | 传错也不影响，是前端带出来的键，不要依赖它 |
| 不发 `type` | 13152 | 我们现在固定发的 `type=1` 实测无效果 |
| 不发 `column` 与 `label` | 13152 | 空串与不发等价 |

`orderBy` 四种取值都不改 `total`（全站基数都是 13152，白名单基数都是 1908），只换首屏
内容。2026-10-02 在白名单基数下按「增值税」连取两页 20 条，逐条读 `cwrq` 与 `label`
判出四档语义：

| `orderBy` | 20 条 `cwrq` 序列 | 判据 |
|---|---|---|
| 1 | 2026-09-04 → 2026-04-22，**严格单调递减**，`label` 跨类混排 | 成文日期倒序，最新在前 |
| 3 | 1984-10-18 → 1994-04-28，**严格单调递增** | 成文日期升序 |
| 2 | 非单调（2026-01-30 与 2026-01-01 来回跳），`label` 成片：前 9 条里 8 条财税文件，随后集中到税务规范性文件，末尾才是文字政策解读、税务部门规章 | **按类别排，不是按日期排** |
| 5 | 非单调，首末都在中间年份 | 相关度（本项目默认） |

所以「2＝日期倒序」是错的，日期倒序要用 1；本项目的 `ORDER_VALUES` 就是照这张表定的，
`--order date_desc` 发的是 `orderBy=1`。同一个检索词连测三个（增值税、小微企业、研发
费用）都单调递减，不是单次巧合。

**这一条只对带检索词的请求成立。** 2026-10-04 本机试了不带词的那一路（`searchWord=""`，
其余按 `search_chinatax` 的默认：`file_only=True` 即发 `FILE_LABELS` 十类白名单、
`pageNum=0`）：这一路 `total` 5676（十类标签下的整库数，与上表「增值税」的 13152／1908
两套基数都不同，不能互相推算），首屏十条的成文日期是
2026-09-28→2026-09-28→2026-09-04→…→2026-08-20，单调不增；同一趟把 `orderBy` 换成
5、1、2 各取一次，首屏十条逐条相同（日期序列也一致）——**空词时 `orderBy` 不起作用**，
排序是接口自己的默认序，恰好就是新在前。所以两条推论要分开记：带词想按新在前就发
`orderBy=1`（上表实测）；想拿"整库最新文件"不能写成"`orderBy=1` 加上空词"，依据只能是
"空词首屏本身就新在前"这一条实测，而且它只保证首屏，翻页后是否延续没有量过。
`--order` 与界面排序控件在空词请求上是空转，不是筛过了。

响应里另有八个 `*List` 分面。它们是选维度的依据，不是命中数：`columnList` 各桶之和
16919、`effectLevelList` 光空串一桶就 15245，都比 `total` 的 13152 大，两套数不在同一
基数上。下面「增值税」下的读数是整桶全量，不是抽样。

| 分面字段 | 结构 | 「增值税」下的全量读数 |
|---|---|---|
| `agingList` | `{key, doc_count}` | 7 桶：`''` 720、全文废止 331、全文有效 255、`'null'` 192、已修改 166、全文失效 9、尚未生效 1。空串与字符串 `null` 合计 912 条 |
| `effectLevelList` | `{key, doc_count}` | 9 桶：`''` 15245、税务规范性文件 703、财税文件 660、工作通知 190、其他文件 62、税务部门规章 31、行政法规 10、国务院文件 9、法律 9。八档之和只有 1674。注意分面桶值与上表单发 `total` 不是同一基数（同一档「财税文件」在表里是 636、这里 660），两边不要互相推算 |
| `taxPolicyList` | 每桶 `{key, key2, doc_count, sonDatas}` | 6 桶：税收政策 1240、税费征管 405、非税收入政策 68、其他 42、`'null'` 2、社会保险费政策 1。`key` 是一级主题，`sonDatas` 是该主题下的税种交叉（税收政策那桶给 31 项：增值税 1045、进出口税收 220、消费税 166、营业税 148、城镇土地使用税 72、城市维护建设税 64……），`key2` 是接口挑出的一个二级值，不保证与检索词对应（社会保险费政策那桶的 `key2` 也写着"增值税"） |
| `columnList` | `{key, doc_count}` | 15 桶：新闻发布 12924、政策法规 1665、互动交流 614、政策解读 503、信息公开 500、政策问答 294、疫情防控税收优惠政策及问答 165、税务视频 116、税收政策 80、减税降费政策操作指南查询 30、政策指引 10、最新政策文件 9、总局概况 5、网站其他 3、减税降费政策及问答 1 |
| `formulatedYearList` | `{key, doc_count}` | 38 桶，返回顺序的前三项：2016 126、2013 89、2015 77 |
| `labelList` | `{key, doc_count}` | 159 桶，返回顺序的前三项：各地动态 2363、媒体视点 2280、减税降费在行动 1299 |
| `industrytypenameList` | **JSON 字符串**，要再解一次 | 长度 318 |
| `taxDiscountList` | `{key, doc_count}` | 1 桶，`key` 为空、9 条 |

`columnList` 那 12924 条新闻发布，就是深页读不出法规的成因：一万三千多条里政策法规栏目
只有 1665 条，而 `tax_fgk._scan_list` 的自适应早停最多翻 20 页、也就是 200 条的窗口，
覆盖不到。把 `column=政策法规` 或 `xxgkSonTaxPolicy=<税种>` 下推到检索侧，才是把窗口对准
法规文件的做法；已经知道文件名与文号时，`docType`＋`docYear`＋`docNo` 一条请求就收到
1 条。有一组搭配必然为零，不要同时发：效力等级与时效两维，判据与数字见
`source_defects.md`「仍然是边界的几件事」。

正文与关联两件事也各有一条实测：

- 外部资料记着一个取正文的接口 `GET /jee2/download/query.jsp?doFlag=getZcwjk&id=…`，
  三个字段分别是 `docContent`（正文）、`docAnnots`（附件）、`docPubFileUrl`（Word 链接）。
  本机实测已下线：www 与 fgk 两个域名、搜索结果里的 `id` 字段与 URL 里的文章 id 两种
  取值，一律 HTTP 404 并回整页 404 HTML（131620 与 48024 字节）。所以正文这一层不接它，
  原因是该接口已不可用，不是本仓库漏登记；走的仍是上文那条详情页的路
  （`tax_fgk.fetch_fgk_body`）。
- 关联文件与关联解读要走 `POST /queryManuscriptAssociation`，表单参数 `id=` 详情页的
  `articleId`。实测 HTTP 200，返回里分 `policyDocument`、`policyInterpretation`、
  `policyGuidance`、`policyQA` 几组；静态详情页 HTML 里这几组是空的，正文中的
  `<a href>` 只能给出零星线索，所以关联走接口：`tax_fgk.fetch_associations` 走
  这个接口，`article_id_from_url` 从 URL 末段取 id（法律类页面 meta 可能没 articleId），
  命令行用 `tax_fgk.py … --assoc` 逐条现拉（关联里的政策文件也带时效，与正文一样不缓存）。
  两个域名不能混：POST 只在 www 域返回 200（同一 id 打 fgk 域回 404，实测），返回的
  `/zcfgk/…` 相对链接反过来要拼 fgk 域才取到正文（拼 www 回 404）。这一路对应 ④ 里
  "同一文件的现行版与被废止版"那条线索——`policyDocument` 每条带 `status`（时效），
  据此能看出关联到的是全文有效还是已废止的旧版。

### search5 收窄维度的命令行

七个收窄开关在 `tax_web_search.py` 与 `tax_fgk.py` 上共用一套（`build_filters` 拼参数），
都可选，不填就等于不发这一维；另有 `--order` 与 `--all-labels` 两项检索选项
（`search_opts_from_args` 收，走 `search_chinatax` 的关键字参数）：

```bash
# 站点检索：仅标题 + 精准分词 + 税种分面
python scripts/tax_web_search.py "增值税" --in-title --precise --tax-type 增值税

# 已知文件名与文号时定点收口（文种带空格也能过，内部空白会被去掉）
python scripts/tax_web_search.py "研发费用" --doc-type "财政部 税务总局公告" \
    --doc-year 2023 --doc-no 7

# 成文日期区间：只给日期会被补成整点时间戳（2024 全年收成 207 条，不给时间戳是 2428 条）
python scripts/tax_web_search.py "增值税" --cwrq-from 2024-01-01 --cwrq-to 2024-12-31

# 按录入的时效或效力等级收窄（取值域就是命令行回显的那几档，域外值发请求前就报错）
python scripts/tax_web_search.py "增值税" --aging 全文有效          # 1908 → 252
python scripts/tax_web_search.py "增值税" --effect-level 财税文件   # 1908 → 636

# 换排序：date_desc 发的是 orderBy=1（成文日期最新在前；orderBy=2 排的是类别不是日期）
python scripts/tax_web_search.py "增值税" --order date_desc --size 8

# 要连新闻、视频、各地动态一起搜时才关白名单（小微企业 184 → 4822）
python scripts/tax_web_search.py "小微企业" --all-labels

# 法规库清单同样可下推维度（把翻页窗口对准法规文件，见上文 columnList 那段）
python scripts/tax_fgk.py "增值税" --tax-type 增值税 --pages 5

# 逐条查关联文件/解读/问答（每条多一次 POST，不依赖 --body、不进缓存）
python scripts/tax_fgk.py "研发费用" --size 1 --assoc
```

五条要记住的：

- 维度拼到 0 条时报的是 `_filter_note`（"分不清拼窄还是没有"）而不是"库里没有这份
  文件"——放宽一维重取才有结论。判据与实测数字见 `source_defects.md`「仍然是边界的
  几件事」那条"效力等级 × 时效两维同时发必然 0"。
- 带维度的清单按维度分缓存键，换一维不会读回上一维的结果；不带维度与带维度也不同键。
  检索范围（`--all-labels`）与排序（`--order`）另走 `tax_fgk.scope_token`，只有偏离默认
  才追加键段，默认那一条键由 `LIST_KEY_REV` 的 `pn0-files` 兜住。
- 日期/年份/编号格式不合法（`2024-1-1`、`2024-13-01`、`doc-year 18` 这类）在发请求前
  就 `ValueError` 拦下——非法值发出去接口只会静默回基线命中或 0，不报错，那样错得最难查。
  时效与效力等级两维同理：域外值（`--aging 有效`）也在这里拦，`--order` 由 argparse 的
  `choices` 拦。
- 界面那一路（`tax_server._ui_filters`）已把「时效」控件接到 `xxgkAging`：数据源选税务
  总局／法规库时，该栏整栏换成 search5 的五个文本值且默认「全部」；NPC／聚合那两路仍发
  数字 `status`。两套值域互不发对方那条链。

## L3：360 站内搜索（地方口径）

```bash
# 指定站点
python scripts/tax_so360.py "<关键词>" --site chinatax.gov.cn --size 10
python scripts/tax_so360.py "<关键词>" --site shui5.cn --size 10
python scripts/tax_so360.py "<关键词>" --site gov.cn --size 10
python scripts/tax_so360.py "<关键词>" --site <省>.chinatax.gov.cn --size 10

# 全网（不限定站点）
python scripts/tax_so360.py "<关键词> 税收 政策解读" --size 10
```

省局子站（shenzhen/shanghai/beijing/guizhou 等）与地方文件**只能从这里进**，
NPC 法规库与总局检索都不收。省级与市级文件（地方留成返还、地方附加减免、
报送与受理时限、地方备案资料清单）全在这一层，跳过它就答不了"我们省怎么办"
这类问题。

## L4：税屋与公众号（实务案例与解读）

```bash
python scripts/tax_shui5.py "<关键词>" --size 5 --read    # 税屋：连正文一起取（推荐）
python scripts/tax_wechat.py "<关键词>" --size 5 --read   # 公众号：连正文一起取
python scripts/tax_browser.py --check                     # 探测本机已装浏览器，不下载
```

这一层回答的是"L1–L3 看不出来的那些事"：某个条件在实务中怎么认定、
申报表怎么填、基层税务机关卡在哪一步、同类业务别人怎么处理、
一个比例或期限在文件之间打架时按哪个执行。**没有 L4，L3 的抽象规定落不到
用户的情形上。**

税屋页面有阿里云 WAF 的 JS 挑战，纯 HTTP 客户端按公开算法算 cookie 服务端
一律不认。程序的处理是：先探测文件系统里已安装的浏览器（Edge、Chrome、Brave、
360、Firefox 的常见安装路径），用 `executable_path` 驱动它跑一次挑战，把放行
cookie 交给普通 HTTP 客户端连读多篇——**一个浏览器内核都不安装**。Edge 不可用
时依次换其他已装浏览器。浏览器过不了时自动退回纯 HTTP 直连，再退回 Jina Reader，
两级兜底都失败会在每条结果上写明 `_error`。正文容器是 `div.arcContent#tupain`，
元信息在 `articleResource`（作者、时间）与 `articleDes`（摘要）。

微信公众号走搜狗，只用 `type=2` 检索文章；还原跳转链接必须与检索共用同一会话
（依赖检索时拿到的 `SNUID`/`SUID` cookie），否则会被判成爬虫并跳到
`/antispider/`。搜狗跳转页不是 302，而是把真实地址拆成 `url += '...'` 的 JS
片段，拼接后还要去掉其中的 `@`。单次最多取 5 篇，还原请求间隔 1.2 秒，
不要并发加压。**这是搜狗的反爬设计，不是缺陷，只能靠遵守节奏规避。**

**取到正文不等于它是依据**：税屋与公众号是第三方实务解读，角色是"执行口径与实务认定"
（`practice`），照常进答案并参与论证，但不能顶替被它解读的那份文件当依据；引它的口径
时要写明原文待核。判据见 `evidence_grading.md`。

## 法规详情与全文下载

```bash
# 法规详情 — 元数据、摘要、全文
python scripts/tax_detail.py --info <法规ID>      # 元数据：标题/分类/状态/日期/机关
python scripts/tax_detail.py --preview <法规ID>   # 摘要：法条数、编号格式、前 5 条
python scripts/tax_detail.py --download <法规ID>  # 全文：下载 docx 到当前目录
```

注意 `--preview` 给的是**摘要**不是全文——只有法条数、编号格式与前 5 条（每条截断 100 字）。

CHECKPOINT · `--download <法规ID>` 会取回完整法规文本，先用 `--info` 与
`--preview` 看过摘要再决定，用户确认后才执行。理由：下载可能产生大文件。

NPC 详情接口 `flfgDetails` 的正文是 `data.content` 单个根节点（带 children），
不是 `contentTree` 数组；文件路径嵌在 `data.ossFile` 对象下，不在 `data` 顶层。
程序按真实结构取，并提供 `related` 字段（修订、解读、草案、依据）。

## 检索词归类

```bash
# 归类并取各源该用的检索词
python -c "import sys;sys.path.insert(0,'scripts');import tax_answer as A,json;\
print(json.dumps(A.search_terms('<用户原话>'),ensure_ascii=False,indent=2))"
```

## 缓存控制

三类缓存的默认值不同，这是有意的：

| 缓存 | 默认 | TTL | 为什么 |
|---|---|---|---|
| NPC 检索清单（`tax_search.py`） | 关 | 5 分钟 | 政策随时更新，默认每次实时查 |
| 总局法规库清单（`tax_fgk.py`） | 关 | 1 小时 | 同上；只有批量检索或多轮追查同一主题时才开 |
| 详情元数据（`tax_detail.py`） | **开** | 1 小时 | 详情接口慢、元数据变动频率低，值得用一点新鲜度换速度 |

总局清单的缓存键里带一个翻页基准版本号（`tax_fgk.LIST_KEY_REV`）。改翻页基准、
改每页条数、改默认检索范围这类会影响"清单里装的是哪一屏"的代码时，把它一起改，
改动前留存的清单就不会在改动后被当成新结果复用。当前键值是 `pn0-files`，对应
默认检索范围为文件类标签那十类；检索范围换回全站、每页条数或翻页基准一变，键值就要另起一个。

```bash
# NPC 法规库检索 —— 清单缓存，默认关，TTL 5 分钟
python scripts/tax_search.py "增值税" --cache
python scripts/tax_search.py --cache-stats        # 看条数与体积
python scripts/tax_search.py --cache-clear        # 只清检索缓存

# 总局法规库检索 —— 清单缓存，默认关，TTL 1 小时
python scripts/tax_fgk.py "转让定价" --size 10 --cache
python scripts/tax_fgk.py --cache-stats
python scripts/tax_fgk.py --cache-clear

# 详情元数据 —— 默认开，TTL 1 小时
python scripts/tax_detail.py --info <bbbs_id>
python scripts/tax_detail.py --cache-stats
python scripts/tax_detail.py --cache-clear        # 只清详情缓存
python scripts/tax_detail.py --info <bbbs_id> --no-cache   # 本次强制现拉
```

三条约定，不能破：

1. **只缓存清单与元数据，正文永不缓存。** 条文每次现拉——缓存旧条文会把
   已废止、被修订的法条当成现行有效引用，这对合规工具是不可接受的风险。
   （实测：NPC 详情接口返回的 `content` 树只有章 / 节标题，条文只在 DOCX / PDF 里，
   所以详情缓存里也没有正文；`tests/test_detail_cache.py` 有一条用例守着这个契约。）
2. **TTL 按源的变动频率定**：NPC 库政策更新快，取 5 分钟；总局法规库目录与
   详情元数据变动慢，取 1 小时。缓存都落在 `~/.cache/tax-analysis-engine`。
3. **命中缓存会在输出里留痕**：`tax_search.py` 打 `[缓存]`，`tax_fgk.py` 打
   `[清单缓存 Ns 前]`，`tax_detail.py` 打 `[详情缓存 Ns 前]`。
   **别拿 `fetched_at` 当"刚查过"的证据**——命中时它记的是第一次抓取的时刻；
   要做时效性判断，先 `--no-cache` 或 `--cache-clear` 再查。

三个脚本的缓存都由 `scripts/tax_cache.py` 提供（唯一实现）。同一个目录下靠条目里的
`_ns` 字段分命名空间（`search` / `fgk` / `detail`），所以各自的 `--cache-stats`、
`--cache-clear` 只作用于自己那一份，删不到详情缓存那一格；跨命名空间清理算越界缺陷。

## 命令一览

```bash
# NPC 法规库 — 法律与行政法规
python scripts/tax_search.py "<完整法规名>" --exact --status 3 --size 20
python scripts/tax_search.py "<关键词>" --status 3 --size 20

# 税务总局法规库 — 部门规章/公告（目录 + 正文都能取）
python scripts/tax_fgk.py "<关键词>" --size 10                     # 翻页自适应（默认）
python scripts/tax_fgk.py "转让定价" --size 3 --pages 8             # 严格翻满 8 页
python scripts/tax_fgk.py "<关键词>" --size 10 --body      # 同时逐条取详情页正文
python scripts/tax_fgk.py "<关键词>" --size 10 --cache     # 清单缓存(TTL 1h)，正文仍现拉

# 总局站点检索
python scripts/tax_web_search.py "<关键词>" --size 10

# 税屋 / 公众号 — 执行口径与实务解读（不是被解读文件的原文）
python scripts/tax_shui5.py "<关键词>" --size 5 --read
python scripts/tax_wechat.py "<关键词>" --size 5 --read

# 360 站内搜索 — 省局与地方文件
python scripts/tax_so360.py "<关键词>" --site chinatax.gov.cn --size 10

# 法规详情 — 元数据与全文
python scripts/tax_detail.py --info <法规ID>
python scripts/tax_detail.py --preview <法规ID>

# 多源聚合 — 五源全开
python scripts/tax_aggregator.py "<关键词>" --size 10
python scripts/tax_aggregator.py "<关键词>" --sources npc,chinatax,so360,shui5,wechat --size 10
python scripts/tax_aggregator.py "<关键词>" --from 2024-01-01 --to 2026-12-31   # 按源分两种口径，见 L1 的 --from/--to 说明
```

## NPC 限流：已由代码强制串行

NPC 触发限流有三种表现，**都不返回 429**：直接断连（`RemoteDisconnected`）、
HTTP 200 但正文是一份带 `<noscript>` 与混淆 JS 的挑战页、以及直接 5xx。
程序已能识别挑战页并按 2/4/8 秒退避重试。

**串行不再只靠自觉**。`tax_search.py` 里有一道跨进程文件锁 `npc_gate`
（类 `NpcSerialGate`，锁文件在系统临时目录），`tax_search.py` 与
`tax_detail.py` 的每次 NPC 请求都要先过闸——两者**共用同一把锁**，因为打的
是同一个站。同一时刻只允许一个进程访问 NPC；同进程多线程也会先在进程内
排队（Windows 的文件锁不可重入，不排队一并发就报错）。

等待超过 `TAX_NPC_LOCK_TIMEOUT` 秒（默认 180）会抛 `TimeoutError`，提示
"另有进程正在跑 NPC 检索"。**遇到它不要重试**，等对方跑完即可；确实要放宽
就调大这个环境变量。

这道闸只管 NPC（`flk.npc.gov.cn`）。总局站（`chinatax.gov.cn`，由
`tax_fgk.py` / `tax_web_search.py` 访问）是另一个站，靠 fgk 的按需自适应
翻页控制请求量，不走这把锁。评测期间仍不建议并行跑多个 NPC 检索。

失败先分诊：区分"限流"与"代码坏了"——限流等退避重跑，代码问题才改代码。
把限流误读成接口变更，会去改本来正常的代码。

## 各源失败表现与处理

| 情况 | 表现 | 处理 |
|---|---|---|
| NPC 限流 | 断连、挑战页、5xx | 等退避重跑，不要改成别的关键词 |
| fgk 正文取不到 | `_error` 写明"该条正文为视频/图片，无文字内容" | 换同题材的文号条目，不要据标题推测正文 |
| 税屋正文取不到 | 每条结果带 `_error` | 确认 `--check` 是否探到浏览器；仍失败改用公众号 |
| 360 读超时 | 程序已重试 | 重跑一次；再失败改用更短的关键词 |
| 总局清单为空却报告了命中数 | `_empty_reason` 分两种成因：写"已翻过末页"就是翻页参数越界，按已取回的清单下结论即可；写"该页在末页之内却没给条目清单"就是这一轮没取到清单，换检索词再取一轮才有结论 | 两种都不等于"库里没有这份文件"，答案里不得据空清单说无此规定 |
| 全部为空 | 五源无结果 | 放宽关键词、换源、告知用户并建议 12366 |
| 子技能限流 | HTTP 429 | 等一分钟再试；准则条文一次取全篇（`ref/{slug}`），别为凑证据面猛刷搜索 |
| 子技能词太短 | HTTP 400 | 检索词补足两字以上，或改用 `by-tag` 按标签取名 |
| 子技能帖子不存在 | HTTP 404 | `link` 必须是检索接口原样返回的值，手拼的 tid/链接一律当作无效 |
| 子技能准则 slug 取不到原文 | `{"error":"未找到该准则"}` | slug 不可由准则名拼出，只能用 `learn/search` 或 `references` 返回的 `slug`；取不到就说取不到，不要凭记忆写条文 |
| 子技能某日无问答 | `recent` 返回 `results` 为空且 `total` 为 0 | 该端点按天更新，空数组是真没有当日帖，不等于服务坏了 |

**取正文失败时只报告失败原因（`body_error` / `_error`），绝不得据标题推测
条文内容。**
