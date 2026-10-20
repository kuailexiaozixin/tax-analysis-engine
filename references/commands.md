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

`--sort date`、`--from`／`--to`、`--scope fulltext` 这三枚在站点上各有自己的走法，
逐项读数与取样口径在 `subskills/chinatax-npc/NOTE.md`「一、NPC（flk）命令行三处细节」，
这里留判据：

- `--sort date` 的排序在**本地做**：接口收到 `orderByParam={order:-1,sort:gbrq}` 并不真按发文
  时间排，不传排序参数时是"接口自己钉住首条、其后大致降序"，两样都不可信，所以程序取回窗口后
  自己按公布日期降序排一次。排的是**取回的那个窗口**，不是全库——要框定"最新"就配 `--from`
  收口，别只靠排序。给了 `--sort date` 就不再按标题命中重排，本体法不会自动置顶；既要本体法
  又要最新口径时分两次查，一次 `--exact` 取主依据、一次 `--sort date --from <年份>` 取新文件。
  多源聚合同样吃这一枚（`tax_aggregator.py --sort date`，网页上是"排序=公布时间"），
  时间序下**不再按权威度分层**：跨源比时间时各源的日期字段统一到 `publish_date`，
  缺日期的条目排在末尾。
- `--from`／`--to` 是**公布日期**（`gbrq`）区间。格式必须是补零的 `YYYY-MM-DD`，
  `tax_search.check_iso_date` 在**发请求之前**校验，`2024-1-1`、`2024-13-01`、`20240101`
  都直接报错退出（CLI 退 2，`/api/search` 报 400），不会静默当成"这一维没填"再回一份基线命中。
  只给一端就按 `DATE_CEIL`（缺上界）／`DATE_FLOOR`（缺下界，`0001-01-01`）补另一界；
  下界取 `0001-01-01` 而不是 1949-10-01 是量过的，两个下界同条数，取更早的不额外排除任何一条。
  **精确检索（`--exact`）带上日期时接口会丢掉检索词**，只按区间回一叠清单；程序不拒绝这个组合，
  改成取回后按"标题是否含检索词"复核——输出的条数是复核后的，接口原报的条数留在 `source_total`，
  并打一句 `_date_note` 说明成因。真要按日期在全库收窄，改用税务总局／法规库那两路
  （`tax_web_search` 的 `cwrqStart`/`cwrqEnd`，语义是**成文日期**，与 NPC 的公布日期不是同一件事，
  跨源比日期时别把它们并成一列）。多源聚合的同一对参数走 `tax_aggregator.py --from/--to`：
  NPC 与总局由接口自己收窄，360／税屋／微信公众号没有日期参数，是在**本轮取回的条目窗口内**
  按条目自带日期补筛，命令行会为每个补筛源打一行「取回／区间内／区间外剔除／无日期保留」计数，
  JSON 里对应 `_date_filter.local_window`；窗口外该源仍可能有区间内的文件，补筛到 0 条不能读成
  "该源在这个区间里没有内容"。
- `--scope fulltext`（NPC 正文检索）可用它定位相关法规：程序给接口加了 `sort=score` 降序
  （接口每条都带 `score` 字段却不用），再按"检索词整段出现在标题"本地重排。但它本质是全文分词
  匹配，短词仍会被通用字带偏，一律带 `_reliability: medium`。**确定某条规则的准确归属只能用
  `--scope title` 或 `--exact`**，见 `source_defects.md`。

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

默认**按需自适应**：连续 3 页没捞到新的法规库条目就收尾（上限 20 页）。默认检索范围
已是文件类标签（那十枚就是站点「综合」tab 提交的那一串，见 `subskills/chinatax-npc/SKILL.md`
第三步），还连着 3 页空手，剩下的只是与检索词沾边的别的文件，硬翻到底只是多花请求。代价是
**可能漏掉间隔 3 页以上的条目**；若出现 `total` 很少但 `total_hits` 很大的可疑情形，就用
`--pages N` 显式指定页数——那时会关掉自适应、严格翻满，但翻得越深相关性越差。

返回里三个字段合起来看：`total_hits`（命中总数）、`pages_scanned`（实际翻了几页）、
`stopped_early`（是否因自适应早停）。三者能区分"确实没有"、"翻到上限也没有"
和"自适应提前收了尾"——后者值得用 `--pages` 再确认一次。

详情页 HTML 里有完整正文，但该站不声明 charset，必须按 UTF-8 解码才能读出
中文；程序已处理，正文容器是 `div.arc_cont`（正文）与 `div.zscont`（注释）。
加 `--body` 逐篇取正文。

清单层与正文层各装一半，这件事有四条后果：附件只在清单层且只有名与链（`appendix` 每项四个键里
`appendixContent` 取样全为空串，随文的申报表、税率表只在附件里，所以正文没表不等于没有表）、
表格与执行期限只在正文层（`tax_evidence.expiry_of`／`period_start_of`）、正文是视频或图片的条目
带 `media_only: true`（这不是取失败，重试也没用；只有真正取失败才进 `body_error`）、`media_urls`
是正文层字段且只有 `--body` 那一轮才有。判据写在 `subskills/chinatax-npc/SKILL.md` 第五步；
铺表口径（colspan 补空列、rowspan 把值带到它盖住的每一行、末列恒空的成因、不吞字的逐行复算）
与逐篇取样读数（三个检索词 16 条里 3 条带附件、两批 75 篇里 8 篇含 `<table>`、5 条 `media_only`
全部带出素材地址且抽查两个直链回 200）在 `subskills/chinatax-npc/NOTE.md`
「三、法规库清单页与正文的形态」。

### search5 的单发参数与分面字段：读数在子技能

`search5` 的可发参数、八个分面字段与逐项单发读数都在 `subskills/chinatax-npc/NOTE.md`：
「四、search5 的可发参数与分面字段」是 2026-10-01、10-02 两批单发实测，「五、search5
的值域、两族日期与行业键名单发读数（2026-10-20 取样）」按当日基线重发，两批各自的全站与
文件类标签基数写在表头，**这两档基数随上游录入每天在漂，比对前先重发当日基线**；
「六、站点自己发的那一串」是 `getSearch.js` 与 `public.js` 两份脚本的逐行读数，键名以那里为准。
判据收在 `subskills/chinatax-npc/SKILL.md` 第三步。
这里只登记接进了什么：

- **已接进命令行的有七组**（2026-10-01 起五组，2026-10-02 补 `xxgkAging`、`xxgkEffectLevel`）：
  `wordPlace`、`participleRule`、`xxgkSonTaxPolicy`、`docType`+`docYear`+`docNo`、
  `cwrqStart`+`cwrqEnd`、`xxgkAging`、`xxgkEffectLevel`，都由 `tax_web_search.build_filters`
  拼参数、`search_chinatax(filters=…)` 并入请求，命令行开关见下文「search5 收窄维度的命令行」。
  另有三项不是收窄维度而是检索选项，走 `search_chinatax` 的关键字参数：`label` 默认发
  `FILE_LABELS` 那十个文件类标签（`--all-labels` 关），`orderBy` 由 `--order` 选（默认相关度），
  `column` 由调用方直接给值（`tax_web_search.py` 自己的命令行没有这一枚开关，用它的是
  `tax_cases.py` 的 `CASE_COLUMN=5741`）。
- **函数层与命令行都不发**：`xxgkTaxPolicy`、`xxgkFormulatedYear`、`industrytypename`、`likeDoc`、
  `searchWordMd5`，以及 `startTime`／`endTime` 那一族（同一区间带完整时间戳时与 `cwrq` 那族收到
  同一个集合，页面自己发的是 `cwrq`）。往命令行加新维度时按 `subskills/chinatax-npc/NOTE.md`
  第四节那张单发参数表逐行核对，不要凭记忆改。
- 值域是拦停依据：`AGING_VALUES`／`EFFECT_LEVEL_VALUES` 之外的值由 `build_filters` 在发请求前
  抛 `ValueError`。放过去拿到的是 `total=0`，与"这一维真的没有内容"在响应里是同一个形态。
- 效力等级与时效两维**同发必然归零**，不要拼：判据与数字见 `source_defects.md`
  「仍然是边界的几件事」。
- 时效这一维**不能拿来当默认**：约六成条目该栏为空，默认收窄到「全文有效」会把没录时效的现行
  文件一起筛掉；网页端「时效」控件因此只在税务总局／法规库两个源下给五个文本值，且默认「全部」
  （`tax_server._ui_filters` 的 `aging` 参数不兜默认）。
- `label` 白名单换的是翻页窗口的成分（「转让定价」「小微企业」两例的对比在 `NOTE.md`），代价是
  标在「视频政策解读」「图片政策解读」上的法规库条目会被漏掉；那类页面回的是 `media_only`
  空正文、本来就引不了条文，要连它们一起取才加 `--all-labels`。
- 把翻页窗口对准法规文件的做法是把 `column=政策法规` 或 `xxgkSonTaxPolicy=<税种>` 下推到检索侧；
  已经知道文件名与文号时，`docType`＋`docYear`＋`docNo` 一条请求就收到 1 条。响应里那八个
  `*List` 分面是选维度的依据、不是命中数，各桶之和与 `total` 不是一套基数。
- 正文与关联两件事：外部资料记着的那个取正文接口 `GET /jee2/download/query.jsp?doFlag=getZcwjk`
  已下线（www 与 fgk 两个域名、搜索结果里的 `id` 与 URL 里的文章 id 两种取值一律 404），
  正文仍走详情页那一路（`tax_fgk.fetch_fgk_body`）；关联文件与关联解读走
  `POST /queryManuscriptAssociation`（表单参数 `id=` 详情页的 `articleId`），只在 www 域回 200，
  返回里的 `/zcfgk/…` 相对链接反过来要拼 fgk 域才取到正文，两个域名不能混着发。
  代码里是 `tax_fgk.fetch_associations` 与 `article_id_from_url`，命令行用 `--assoc` 逐条现拉，
  关联里的政策文件也带时效，与正文一样不进缓存。

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

# 成文日期区间：命令行只收 YYYY-MM-DD，发出去前补成整点时间戳（全站基数下 2024 全年收到 207 条；
# 不补时间戳的那一发量的不是区间，见 subskills/chinatax-npc/NOTE.md 第五节）
python scripts/tax_web_search.py "增值税" --cwrq-from 2024-01-01 --cwrq-to 2024-12-31

# 按录入的时效或效力等级收窄（取值域就是命令行回显的那几档，域外值发请求前就报错；
# 这两维在文件类标签基数下各收到多少，读 NOTE.md 第四节那张表）
python scripts/tax_web_search.py "增值税" --aging 全文有效
python scripts/tax_web_search.py "增值税" --effect-level 财税文件

# 换排序：date_desc 发的是 orderBy=1（成文日期最新在前；orderBy=2 排的是类别不是日期）
python scripts/tax_web_search.py "增值税" --order date_desc --size 8

# 要连新闻、视频、各地动态一起搜时才关白名单（关掉后基数换成全站那一档，两档的成分对比见 NOTE.md 第四节）
python scripts/tax_web_search.py "小微企业" --all-labels

# 法规库清单同样可下推维度（把翻页窗口对准法规文件；栏目与分面那批读数在 subskills/chinatax-npc/NOTE.md 第四节）
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
  就 `ValueError` 拦下——越界值发出去接口不回错误码，它回 `total=0`，跟"这一维真的没有
  内容"在响应里是同一个形态，那样错得最难查。
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
python scripts/tax_shui5.py "<关键词>" --channel native --size 5   # 只走站内搜索，带日期/摘要/栏目
python scripts/tax_shui5.py "<关键词>" --channel native --column 北京,深圳 --size 20  # 限定栏目
python scripts/tax_shui5.py "<关键词>" --channel native --from 2025-01-01 --to 2025-12-31  # 区间要成对
python scripts/tax_shui5.py "<关键词>" --channel native --match title --all  # 只匹配标题，按总数翻到底
python scripts/tax_shui5.py --list 实务解读 --pages 2     # 不检索，按栏目读它的清单
python scripts/tax_shui5.py --tag 增值税法 --pages 2      # 不检索，按站点标签读清单
python scripts/tax_shui5.py --columns                     # 打印栏目表（目录名/classid/页数条数），不发请求
python scripts/tax_shui5.py --find-doc "财税[2019]13号"   # 只有一枚文号时回站内定位（默认交 20 条候选）
python scripts/tax_ctax.py "<关键词>" --size 5 --read       # 中国税网：连正文一起取（无凭据、不启浏览器）
python scripts/tax_ctax.py "<关键词>" --column 政策解读 --size 10   # 限定一栏（栏目名或 nodeID，只认叶节点）
python scripts/tax_ctax.py "<关键词>" --from 2025-01-01 --to 2025-12-31  # 两枚各自独立，单给一侧照常收
python scripts/tax_ctax.py "<关键词>" --match title --all --max-rows 500  # 只匹配标题，翻到底但最多取 500 行
python scripts/tax_ctax.py --find-doc "财政部 税务总局公告2026年第29号"   # 按文号定位并把一稿多栏并成一条
python scripts/tax_ctax.py --list 税案通报 --pages 2         # 不检索，按栏目列表页读清单（最只读到第 10 页，每页行数随模板浮动）
python scripts/tax_ctax.py --columns                        # 打印本地栏目表，不发请求
python scripts/tax_wechat.py "<关键词>" --size 5 --read   # 公众号：连正文一起取
python scripts/tax_browser.py --check                     # 探测本机已装浏览器，不下载
```

栏目那一串参数（`--column` / `--from` / `--to` / `--match` / `--all`）只有站内那一路有，
`--channel auto` 撞上它们时会自动换成站内并在 `_channels` 里写明"这一轮没问过 360"。
`--list` / `--tag` 完全不检索，是把站点自己的组织方式（栏目、标签）当成入口用；
两者取回的行与检索结果同形。`--column` 认目录名、导航名、首页短标签或 classid，
多个用逗号，换算只查 `data/shui5_columns.json`，查不到就整体报缺而不发半截限定的命令。

这一层回答的是"L1–L3 看不出来的那些事"：某个条件在实务中怎么认定、
申报表怎么填、基层税务机关卡在哪一步、同类业务别人怎么处理、
一个比例或期限在文件之间打架时按哪个执行。**没有 L4，L3 的抽象规定落不到
用户的情形上。**

税屋的站点机制——WAF 挑战的形态、放行凭据哪一枚有效与能用多久、正文容器与元信息字段、
栏目地图与分页参数、站内搜索表单——全在 `subskills/shui5/SKILL.md`，取不到正文时先按
它判"是没凭据还是站点上没这篇"。这里只留主干上一条：这个站在没凭据时，**同一个地址
返回的是另一份页面**，首页、文章页、`robots.txt` 与一个不存在的路径都回同一份挑战页，
HTTP 都是 200，所以"请求成功"不等于"取到内容"。程序的处理是探测文件系统里已安装的
浏览器（Edge、Chrome、Brave、360、Firefox 的常见安装路径），用 `executable_path` 驱动
它跑一次挑战，把放行 cookie 交给普通 HTTP 客户端连读多篇——**一个浏览器内核都不安装**，
Edge 不可用时依次换其他已装浏览器。浏览器过不了时退回纯 HTTP 直连，再退回 Jina Reader，
两级都失败会在每条结果上写明 `_error`。

中国税网（`scripts/tax_ctax.py`，中国税务报社的媒体站）走的是另一套取数：检索是一个开放
JSON 接口 `GET /xy/Search.do`，无 WAF、不校验 UA、不需要凭据，也不启浏览器。这一路要认的
失败形态和税屋正相反——税屋是"请求成功但回的是另一份页面"，税网是**参数越界不回错误码**：
翻页只认 `pageNo`（`start`/`page`/`pageNum`/`p`/`offset`/`currentPage` 六枚别名一律被忽略，
提交它们等于反复取第一页）；匹配域靠"提交哪一枚参数名"表达，`q` 与 `title` 同发时 `q` 会把
`title` 盖掉；栏目限定只认叶节点，给父节点回 0 条而它下面确实挂着文章，这一句会写进
`_filter_note`；`channel=0` 在这一层等于不提交那一枚、拿到网站与触屏的并集，只有站点不认的
值才打到那份 37 字节的空结构。三种越界的回法（HTTP 500、静默按缺省、回空结构）逐档记在
`subskills/ctaxnews/NOTE.md`，取数前判据在 `subskills/ctaxnews/SKILL.md`。
`match`、`channel`、`size`、`max_rows`、`--pages` 这五枚的取值域在本层是**发出之前**判的：
越界的写法不回半份数据，`_status` 直接写「没走成」并带上那一枚的值域；`--size` 收的是合并同稿后的
**篇**数、`--max-rows` 收的是站点给的**行**数，两枚都只收紧，谁小谁生效，翻到底时撞到行数上限会在
`_status` 写明「已到 max_rows=N 行的上限」。
两条硬边界：一篇稿挂几栏就出几行且 `articleid` 各不相同，`dedup_rows` 按「标题＋发布日期＋文号」
并成一条，所以收满按并完的篇数算；2023-02 改版前的行地址缺一段目录，正文永久取不到，
`_address_state` 只看段数就能判定，这类行只当题录（`_url_state` 写明原因，不重试）。
这一路不进 ④ 的五源聚合。定级那一步也不会替它把关：`tax_evidence.rank_of` 先按标题形态判，
转载件的标题就是原文件那份标题，所以「财政部 税务总局公告2026年第29号」这样一行会判成
`normative`、角色成 `direct`，而 `PRACTICE_SOURCES` 那六枚来源标记里没有 `ctaxnews.com.cn`，
标题不带"解读"二字的行也落不进 `interpretation`。**读成：这一路的地址只作定位与线索，
依据链里的 url 要回官方库那份文件**。

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

# 中国税网 — 报社登载的政策转载、解读与税案通报（不进五源聚合，判据见 subskills/ctaxnews/）
python scripts/tax_ctax.py "<关键词>" --size 5 --read
python scripts/tax_ctax.py --find-doc "<文号>"

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

**"并行必现"这句是什么时候量的，要能查**。站点风控随时间改，记录会过期，
所以每条限流记录在 `references/source_defects.md`「限流记录的实测台账」里都
带最近实测日期、实测方法（连发几次 + 几线程）、当时结果三个字段。复跑：
`python tests/probe_rate_limit.py --target npc`（`--dry-run` 只看要发的请求，
一个请求都不发；单次总量卡在脚本里的 `MAX_REQUESTS`）。2026-10-04 这一轮把
NPC 验到 16 路并发、三趟共 104 次请求，下面那三种形态一个都没出现——这动摇的是
"必现"两个字，不是闸：闸的代价是排队等待而不是失败，所以本轮保留闸、只把记录
改成可复查的。要放宽并行，先看台账那一行的日期是否晚于你要改动的日期。

失败先分诊：区分"限流"与"代码坏了"——限流等退避重跑，代码问题才改代码。
把限流误读成接口变更，会去改本来正常的代码。

## 各源失败表现与处理

| 情况 | 表现 | 处理 |
|---|---|---|
| NPC 限流 | 断连、挑战页、5xx | 等退避重跑，不要改成别的关键词 |
| fgk 正文取不到 | `_error` 写明"该条正文为视频/图片，无文字内容" | 换同题材的文号条目，不要据标题推测正文 |
| 税屋正文取不到 | 每条结果带 `_error` | 确认 `--check` 是否探到浏览器；仍失败改用公众号。**先分清是没凭据还是站点上没这篇**：没凭据时任何地址都回同一份挑战页，判据与凭据寿命见 `subskills/shui5/SKILL.md` |
| 360 读超时 | 程序已重试 | 重跑一次；再失败改用更短的关键词 |
| 总局清单为空却报告了命中数 | `_empty_reason` 分两种成因：写"已翻过末页"就是翻页参数越界，按已取回的清单下结论即可；写"该页在末页之内却没给条目清单"就是这一轮没取到清单，换检索词再取一轮才有结论 | 两种都不等于"库里没有这份文件"，答案里不得据空清单说无此规定 |
| 中国税网返回"没走成" | `_status` 以「没走成」开头的是**请求被拦在发出之前**：栏目名不在 `data/ctax_columns.json`、或与表里多行重名（`column_lookup` 的 `ambiguous`/`missing`，会把候选一并列出）、或空词没带栏目、或日期不是 `yyyy-mm-dd`、或 `match` 不在 `MATCH_POSITIONS` 的四枚里、或 `channel` 不在 0/1/2 里、或不收满时 `size` 不是正整数、或 `max_rows` 为负、或 `--list` 的 `--pages` 非正。绕过命令行直接给 `_search_once` 传越界值时，站点那一路回 500，`_status` 末尾带 HTTP 码与字节数并写明"不是限流" | 换限定条件重发，别把它当成"站内没有"；站内确实没命中时 `_status` 写的是「0 条：这一词（连同本轮限定）在站内没有命中」，两句不是一回事 |
| 中国税网取到的条数明显比预期少 | `_status` 写「取到 N 条，这一词共 M 条；已到页数上限」就是没翻到底（`SEARCH_MAX_PAGES` × `SEARCH_PAGE_SIZE`），写「已到 max_rows=N 行的上限」就是先撞到 `--max-rows` 那枚行数预算，写「合并同一篇挂多栏的行 K 行」则是一篇稿挂了几栏出几行被并成一条 | 第一种加 `--all`（要控制花费就配 `--max-rows`），第二种是 `--max-rows` 给的预算用完了、想多要就加大它或去掉，第三种是正常合并、不是丢数据；三种都不读成"这一词只有这些" |
| 中国税网正文取不到 | 该行带 `_url_state`，写的是地址缺一段目录（2023-02 前的老行，回 500 加一份错误页；字节数随请求带的 UA 变，带浏览器 UA 572、不带 170，所以尺寸不是判据，段数才是） | 只用题录字段（标题／发布日期／文号／发文机关），正文回官方库或改版后的转载件；**不重试**，这一站错误地址回 500 不是 404，重试没有意义还占间隔 |
| 全部为空 | 五源无结果 | 放宽关键词、换源、告知用户并建议 12366 |
| 子技能限流 | HTTP 429 | 等一分钟再试；准则条文一次取全篇（`ref/{slug}`），别为凑证据面猛刷搜索 |
| 子技能词太短 | HTTP 400 | 检索词补足两字以上，或改用 `by-tag` 按标签取名 |
| 子技能帖子不存在 | HTTP 404 | `link` 必须是检索接口原样返回的值，手拼的 tid/链接一律当作无效 |
| 子技能准则 slug 取不到原文 | `{"error":"未找到该准则"}` | slug 不可由准则名拼出，只能用 `learn/search` 或 `references` 返回的 `slug`；取不到就说取不到，不要凭记忆写条文 |
| 子技能某日无问答 | `recent` 返回 `results` 为空且 `total` 为 0 | 该端点按天更新，空数组是真没有当日帖，不等于服务坏了 |

**取正文失败时只报告失败原因（`body_error` / `_error`），绝不得据标题推测
条文内容。**
