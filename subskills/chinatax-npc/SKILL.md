---
name: chinatax-npc
description: |
  官方法规原文层三站（国家税务总局站点 www.chinatax.gov.cn、国家税务总局政策法规库 fgk.chinatax.gov.cn、
  NPC 国家法律法规数据库 flk.npc.gov.cn）站点子技能。当需要**按税种或法名检上位法与总局公告、把命中收窄到
  某个时效／效力等级／税种／行业／发文字号／成文日期区间、判断这三站为什么回 0 条或条数为什么和界面不一样、
  手上只有一枚文号要回库里对、读回某一份的正文与随文附件、找同一文件的现行版与被废止版**，或需要复测这三站
  检索接口的参数值域、返回结构、正文容器形态与两个域名之间的分工时使用。
  触发词：国家税务总局、总局、chinatax、法规库、fgk、NPC、flk、国家法律法规数据库、search5、search/list、
  enumData、GSFFK、flfgCodeId、zdjgCodeId、gbrqYear、docYear、docType、cwrqStart、xxgkAging、
  xxgkEffectLevel、industrytypename、wordPlace、participleRule、orderBy、likeDoc、翻页基准、0 条、
  附件、media_only、queryManuscriptAssociation、发文字号、成文日期、税种分面、效力等级。
  本子技能管"这三站怎么取"，母技能管"取回来的东西在答案里算什么"；全程只发 HTTP 请求，
  **不调用任何模型、不消耗任何额度**。
---

# 国家税务总局与 NPC 三站（chinatax-npc）站点子技能

三站都是服务端渲染加一枚 JSON 检索接口，没有 WAF、不要凭据、不启浏览器。取数难点不在接入而在三处：
**翻页与每页条数的基准两站不同**（search5 的 `pageNum` 从 0 起、`pageSize` 卡在 10，flk 的 `pageNum` 从 1 起、
每页照请求给）；**收窄条件给越界值不回错误码**（search5 的时效与效力等级两维给域外值回 `total=0`，
和"这一维确实没有内容"在响应里是同一个形态，flk 的分类编号只认叶节点、发父节点那一枚也回 0）；
**清单层与正文层各装一半**（附件只在清单层、只有名与链，表格与执行期限只在正文层）。
判据写在本文件，逐项读数（含取样口径）记在 `NOTE.md`。

## 工作流

**第一步 定这一趟要什么**——下面几行的入口、耗时与前置各不同，先选对再动手：

| 要什么 | 走哪条 | 前置 |
|---|---|---|
| 上位法（法律、行政法规）按税种找 | `tax_search.search_npc(关键词)`，即 `POST flk.npc.gov.cn/law-search/search/list` | 无：开放接口，不要凭据、不启浏览器 |
| 本体法名已知，只要那一份 | 同一函数的 `search_type=1`，命令行 `--exact` | 无 |
| 总局公告、规范性文件、文字解读 | `tax_web_search.search_chinatax(关键词)`，即 `GET www.chinatax.gov.cn/search5/search/s` | 无 |
| 规章与法规库条目（国际税收、转让定价、处罚依据在这一路） | `tax_fgk.search_fgk(关键词)`，翻页自适应 | 无 |
| 在上面两路把命中收窄到某一维 | 同一批函数的 `filters=`，命令行七个开关（见「命令」） | 值域先过第三步那一串 |
| 某一栏的全部文件（横截面，不是按词召回） | `tax_gov_list.py`：先 `sync --channel 栏名` 建索引，再离线 `lookup`／`stats` | **必须先建索引**，没建过 `lookup` 报缺索引，那是前置不是失败 |
| 已知地址读正文与注释 | `tax_fgk.fetch_fgk_body`（命令行 `--body`） | 无 |
| 同一文件的现行版与被废止版、关联解读与问答 | `tax_fgk.fetch_associations`，命令行 `--assoc` 逐条现拉 | 详情页的 `articleId` |
| 实务材料援引的文号回官方库对 | `tax_answer.check_practice_citations`（母技能 ⑧） | 走 `search_chinatax` 那一路，不发新接口 |
| 一份法规的全文文件 | `tax_detail.py --download <法规ID>` | 先用 `--info`／`--preview` 看过摘要，用户确认后才执行 |

界面那一路不必启：这三站的检索都是同一条 GET 或 POST，网页上的"范围""时效""效力等级""发文字号"
几个下拉框逐个映射到第三步那几枚参数名，`tax_server._ui_filters` 已经在做同一件换算。

**第二步 翻页基准与每页条数先按站点分清楚，再读条数。** search5 把 `pageSize` 卡在 10 条，
传 20/60/100 都只回 10 条，只能按页取，且 `pageNum` 从 0 起（本项目对外的 `page` 从 1 起，
换算只收在 `search_chinatax` 一处）；flk 的 `pageNum` 从 1 起，`pageSize` 照请求给行
（`pageSize=5` 时第 624 页仍回行，与 `total` 对得上）。基准抄错时的表现最骗人：结构正常、
条数正常、翻页数正常，只是每一轮都少了相关度最高的那一屏；命中不足 10 条时首屏是唯一一页，
丢掉它就取回空清单。NPC 与总局这两个源的基准不同，照其中一个去改另一个就出这种错位。

`total` 与"这一页取到几行"是两件事，读条数前先确认读的是哪一个。flk 那一路的响应把 `total`
与 `rows` 放在**根节点**上，没有再包一层 `data`；同一前缀下的 `GET /law-search/search/enumData`
反倒包在 `data` 里。两条形态相反，按一种解包法统一处理就会把另一条读成空——
`tax_search` 用的是 `data.get("data", data)` 这种两边都收的写法所以读得出来，
手工复测若只按 `data` 取会把读数记成 None；这样作废的 12 发记在 `NOTE.md` 开头的请求数表里。
更要紧的是 `total` 本身：同一枚「环境保护」在 `pageSize=5` 报 3,118、在 10／20／50 报 224，
换到第 23 页它又回 3,118，所以**别拿它当命中总数或翻页上限**，读数表与判据见 `NOTE.md` 第二节。

**第三步 search5 的收窄只按站点认的那一式提交，越界的回法是 0，不是回基线。**

- 时效五档、效力等级八档、排序四档分别写在 `tax_web_search.AGING_VALUES`、`EFFECT_LEVEL_VALUES`、
  `ORDER_VALUES`，域外值在 `build_filters` 抛 `ValueError` 停在发出之前。为什么必须在这儿拦：
  2026-10-20 实测把 `xxgkEffectLevel` 发成「法规」、`xxgkAging` 发成「有效」，两次都回 `total=0`，
  同一次跑的基线是 1910——**越界值不发错误码也不回基线，它和"这一维真的没内容"长得一模一样**。
  界面与录入写的是「行政法规」，站点也收「行政法规」：`getSearch.js` 里那段把「行政法规」换成「法规」
  的分支整块是注释掉的，现行页面按原值发（逐行读数见 `NOTE.md`「站点自己发的那一串」）。
- `docType` + `docYear` + `docNo` 是**发文字号**那一组，`docYear` 键在发文字号里的年份，
  不是成文年份：同一枚「个人所得税」配 `docYear=1994` 回 82 条、配 `xxgkFormulatedYear=1994` 回 90 条，
  差的 8 条是《中华人民共和国增值税法》那一类没有文号的法律。两枚都要用对得上号的文件时才发。
  文种带空格时站点只对「财政部 税务总局公告」这一枚去空格（`getSearch.js` 里硬编码的就这一处），
  `--doc-type` 是通用去掉内部空白，所以命令行上照页面写法给也能过。
- 行业那一维的键名是 `industrytypename`，不是 `xxgkIndustryType`：前者在「增值税」×文件类标签的基数下
  回 70（社会民生），后者回 0——`xxgkIndustryType` 只出现在响应的分面侧，不是请求参数。
  这一维仍未接进命令行，接的时候按 `NOTE.md` 那十二档读数给值。
- `cwrqStart`/`cwrqEnd` 的语义是**成文日期**，与 NPC 的公布日期不是同一件事，跨源比日期时别把它们并成一列。
  站点自己发的是 `yyyy-mm-dd 00:00:00` 配 `23:59:59`，快捷时间档（7/30/365 天）只发起点、终点给空串；
  `build_filters` 同样只在有值时提交那一枚，并按整点时间戳补齐。**只给 `yyyy-mm-dd` 时日期值根本不参与比较**，
  这一发改的是集合：全站基数下「2024 全年」「只发起点」「只发终点」「换成 2025 上半年」四种裸写法都回同一个 2432
  （不发日期是 13176），带整点时间戳才按区间收到 207；文件类标签基数下这一过滤是恒等（1910 → 1910），
  看着像没生效，其实是这一族条目本来就全录了成文日期。所以裸日期的 1910 不能读成"日期筛过了"，
  也不能读成"这一维不生效"；补时间戳是站点自己那一式，命令行拿到的也是那一式。
- 效力等级与时效两维同时发必然归零，不要拼：判据与数字见 `references/source_defects.md`。
- `label` 白名单 `FILE_LABELS` 那十枚与站点「综合」tab 提交的那一串是**同一集合**（逐枚比对无差异），
  不是本仓库自创的收窄；代价是标在「视频政策解读」「图片政策解读」上的法规库条目会被漏掉，
  要连它们一起取才加 `--all-labels`。
- `searchSiteName=GSFFK` 换不了库：实测只把 1910 收到 1893，它是页面带出来的一枚标签，不是索引开关。
  代码里那枚 `FGK_MARKER` 判的是**条目地址的主机名**，跟这枚参数不是一回事，别拿它当换库的证据。
- 排序只在带检索词的请求上有效：空词那一路 `orderBy` 四档回同一份首屏，接口自己的默认序恰好新在前。
- 响应里八个 `*List` 分面是选维度的依据、不是命中数（`columnList` 各桶之和比 `total` 大一套基数）。
  其中 `industrytypenameList` 回的是 JSON 字符串，要再解一次，站点自己把解出来长度 2 当空。

**第四步 NPC 那一路：分类编号只认叶节点，值域从 `enumData` 取，两棵树的编号会撞。**

- 三枚数组 `flfgCodeId`、`zdjgCodeId`、`gbrqYear` 本仓库恒发 `[]`，实测它们都在收窄：
  「环境保护」×现行有效 ×`pageSize=5` 基线 3,118 条，`zdjgCodeId=[120]`（国务院）收到 57 条，
  `gbrqYear=["2024"]` 收到 282 条。
- **但按门类收窄不能只发父节点那一枚编号**：只发「法律」的 `101` 回 0 条，发 `101` 自带的
  `codeIdList`（14 枚）回 19 条。这一条和中国税网那个 `nodeID` 只认叶节点是同一类陷阱。
  这里的难点是回 0 不区分原因：`101` 那发是编号发在了父节点，而「宪法」叶节点 `flfgCodeId=[100]`
  那发的 0 是「环境保护」这个词下确实没有宪法类文件——**这两种 0 在响应里是同一个形态**，
  看命中数判断不出该改参数还是该接受空结果。这一路走得通的证据是 `zdjgCodeId=[120]`（国务院）回 57 条：
  同一枚数组、叶节点、收窄生效。要按门类取，先把 `GET /law-search/search/enumData` 的 `codeIdList`
  整份取下来，按叶节点那一层发。
- `enumData` 给的是两棵树：法律性质 34 枚非空编号、制定机关 39 枚，其中 **22 枚在两边各指一种东西**
  （`100` 一边是宪法、一边是全国人大；`120` 一边是民法商法、一边是国务院；`220` 一边是监察法规、
  一边是辽宁）。编号发进错的树不回错误码，只把命中收到 0，所以取值必须连树一起取，
  别把两棵树的编号当成一套号段。整棵树只有 `enumData` 能给，界面上那些下拉框是它渲染出来的。
- `--status` 走 `sxx` 数组、`--from`/`--to` 走 `gbrq` 数组；施行日期区间 `sxrq` 与 `gbrqYear` 本层没接。
- 接口收到 `orderByParam={order:-1,sort:gbrq}` 并不真按公布时间排，本层取回窗口后自己排一次；
  精确检索带日期时接口会丢检索词，两条都写在 `references/commands.md`，那一段讲的是怎么办这一层取回的东西。
- 连发或并发会 `ConnectionResetError`（HTTP 都不回来），跨进程那一档由 `tax_search.NpcSerialGate` 串行；
  绕开闸门手工复测时按 15 秒以上间隔，限流读数记在 `references/source_defects.md` 那张台账里。

**第五步 正文层与清单层各装一半，说的时候别说成一半。**

- 详情页回 `Content-Type: text/html` 不带 charset，必须按 UTF-8 解，否则中文静默变乱码而 HTTP 仍是 200；
  正文容器 `div.arc_cont`，注释容器 `div.zscont`。
- 表只在正文层：容器里的 `<table>` 有两种页形（Word 粘贴的每格包 `<p>`、包图片的单格表），
  `tax_fgk._table_lines` 铺成 Markdown 行。**清单层读不出执行期限，正文层读得出**
  （`tax_evidence.expiry_of`／`period_start_of`），所以"这份没写期限"要分清是在哪一层读的。
- 附件只在清单层，且只有名与链：接口 `appendix` 每项四个键里 `appendixContent` 取样全为空串，
  随文的申报表、税率表**只在附件里**，正文容器取不到那张表。正文没表不等于没有表。
- 正文是视频或图片的条目带 `media_only: true` 与 `media_urls`——这不是取失败，重试也没用，
  答案给不出条文就该给出那几个素材地址。
- 外部资料记着的那个取正文接口 `GET /jee2/download/query.jsp` 已下线（www 与 fgk 两个域名、
  两种 id 取值一律 404），正文仍走详情页那一路。
- 关联文件与关联解读走 `POST /queryManuscriptAssociation`：只在 www 域回 200（同一 id 打 fgk 域 404），
  返回里的 `/zcfgk/…` 相对链接反过来要拼 fgk 域才取到正文（拼 www 回 404）。两个域名不能混着发。

**第六步 回母技能定级——这一层只负责取，时效、层级、角色都在 ⑧ 判。**
三站给的都是**录入值**：`xxgk_aging` 进 `status`（来源记在 `status_from`）、`xxgk_effectLevel` 进
`effect_level`、flk 的 `sxx` 数字进 `status_code`，定级层按它们归档，比按标题形态猜准。
两处别读反：`xxgkAging` 那一栏约六成条目为空，空读成"站点没录"，不读成"这份没有时效"；
flk 的 `--status` 一次只能要一种状态，不带 `--status` 等于「现行有效」而不是"全部"。
文号回库核对（`check_practice_citations`）打的也是这一层，`not_in_library` 说的还是
"我们查的那份库里没对上"，不是那份文件不存在（母技能 ⑦ 第 24 条）。

## 命令

命令行与函数同一份判据；参数怎么取、失败了怎么办，全文在 `references/commands.md`。

```bash
# NPC 法规库（上位法）
python scripts/tax_search.py "增值税" --status 3 --size 20        # 标题检索
python scripts/tax_search.py "中华人民共和国增值税法" --exact --status 3   # 本体法名已知，必须走精确
python scripts/tax_search.py "增值税" --sort date --from 2024-01-01        # 取回窗口内按公布日期降序
python scripts/tax_search.py "增值税" --scope fulltext --size 10           # 正文检索定位相关法规，带 medium 标记
python scripts/tax_detail.py --info <法规ID>                      # 元数据；--preview 是摘要不是全文
python scripts/tax_detail.py --download <法规ID>                  # 全文 docx，用户确认后才执行

# 总局站点检索（覆盖站点与法规库）
python scripts/tax_web_search.py "增值税" --size 10
python scripts/tax_web_search.py "增值税" --in-title --precise --tax-type 增值税
python scripts/tax_web_search.py "研发费用" --doc-type "财政部 税务总局公告" --doc-year 2023 --doc-no 7
python scripts/tax_web_search.py "增值税" --cwrq-from 2024-01-01 --cwrq-to 2024-12-31
python scripts/tax_web_search.py "增值税" --aging 全文有效 --effect-level 财税文件   # 这两枚不要同发
python scripts/tax_web_search.py "小微企业" --all-labels          # 关文件类标签白名单
python scripts/tax_web_search.py "增值税" --order date_desc --size 8

# 总局法规库（规章、公告，翻页自适应）
python scripts/tax_fgk.py "增值税" --size 10
python scripts/tax_fgk.py "增值税" --size 3 --body                # 连正文一起取
python scripts/tax_fgk.py "转让定价" --size 3 --pages 8           # 关自适应，严格翻满
python scripts/tax_fgk.py "研发费用" --size 1 --assoc             # 逐条查关联文件/解读/问答

# 整栏横截面（清单源）
python scripts/tax_gov_list.py sync --channel 政策法规
python scripts/tax_gov_list.py lookup --aging 现行有效
python scripts/tax_gov_list.py stats

# 复测值域（零模型调用，逐档读数见 NOTE.md）
python -c "import sys;sys.path.insert(0,'scripts');import tax_web_search as W;print(W.AGING_VALUES,W.EFFECT_LEVEL_VALUES,W.ORDER_VALUES)"
```

`--aging` 与 `--effect-level` 同发的结果是 0 条而不是交集，那一格 `_filter_note` 会写明分不清
是拼窄还是没有，放宽一维重取才有结论。维度拼到 0 条时不要写"库里没有这份文件"（母技能 ⑦ 第 24 条）。

## 站点底座

| 项 | 读数 | 依据 |
|---|---|---|
| 谁在发请求 | 三站都是纯 HTTP：`requests` 直连，无凭据、无 Cookie、不启浏览器 | 本层全部取数走 `tax_web_search`／`tax_fgk`／`tax_search`，实测不带登录态可取 |
| 检索接口 | `GET https://www.chinatax.gov.cn/search5/search/s`；`POST https://flk.npc.gov.cn/law-search/search/list` | 前者由 `SEARCH_URL` 拼，后者在 `tax_search` 里 |
| 结果页真正的落点 | 法规库的结果页 AJAX 打的也是 **www 域**那一条 `search5/search/s`；站点那份 `getSearch.js` 里 fgk 域那一行写成 www 行的下一行、整行注释掉 | 依据是能再取回的 `https://fgk.chinatax.gov.cn/zcfgk/xhtml/js/getSearch.js`，字节数与逐行读数见 `NOTE.md` |
| 值域的来源 | 总局那一路的值域在页面 JS 的下拉框里，NPC 那一路在 `GET /law-search/search/enumData` | enumData 回三枚键：`flfgfl`、`zdjgfl`、`flcaStatus`，后者回 `0`，不是时效值域 |
| 每页条数 | search5 恒 10 条；flk 照请求给（`pageSize` 上限未探，本层 `min(size, 100)`） | 见第二步 |
| 编码 | 接口回 JSON 带 charset；详情页 HTML 不带 charset，须按 UTF-8 解 | 见第五步 |
| 域名分工 | 检索与关联接口在 www 域，正文页与清单页两域各有可达形态；`FGK_MARKER` 按条目主机名归类 | 见第五步与 `NOTE.md` |

## 边界：这一层不给什么

1. **不给"0 条就是没有"的结论**。0 条至少有四种成因：检索词真的没命中、维度拼窄了（效力等级×时效
   同发必 0）、值不在站点认的域内（这一种也回 0，不发错误码）、分类编号发到了错的树上。
   前三条分别由放宽一维、按值域拦停、回 `enumData` 取值来处置；判不动时 `_filter_note` 那句要照抄进答案。
2. **不给整库全量，也不给整栏横截面**。search5 是按词召回的开放集合，每页 10 条、`tax_fgk`
   自适应最多 20 页；"现行有效的规范性文件都有哪些"这类横截面走 `tax_gov_list.py`，它不在 ④ 的五源里。
3. **不给地方规定**。省局与地方文件只在母技能 ③ L3 那一路（360 站内搜索），这三站不收。
4. **不给执行口径与实务认定**。税屋、公众号、中国税网给的那一层在 ③ L4，这一层给的是文件本身。
5. **不给时效判定**。三站给的都是录入值，`xxgk_aging` 六成为空、flk 的 `--status` 一次一档；
   判定在母技能 ⑧ 的 `judge_validity`，观察时点要显式 `--at` 传入。
6. **不给"正文取不到就是这份没内容"**。`media_only` 的正文本来就是视频或图片，`body_error`
   才是取失败；取失败只报原因，不得据标题推测条文（母技能 ⑦ 第 18 条）。
7. **不给规范层级与主依据挑选**。`rank_of`／`pick_primary` 在 ⑧，这一层只把 `effect_level`
   与 `document_number` 原样带回去。
8. **不对 NPC 并发加压**。`NpcSerialGate` 保证跨进程串行；绕闸门手工复测时的间隔与失败计数
   按 `references/source_defects.md` 那张台账记，不另立一份。
