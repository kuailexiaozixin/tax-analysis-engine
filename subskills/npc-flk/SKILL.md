---
name: npc-flk
description: |
  NPC 国家法律法规数据库（flk.npc.gov.cn）站点子技能。当需要**按税种或法名找上位法（法律、行政法规）、
  把命中按法律性质门类或制定机关收窄、按公布日期区间与时效状态取清单、读回某一份法规的元数据与全文
  下载件、判断这一站为什么回 0 条或条数为什么和界面不一样**，或需要复测这一站的检索接口参数、
  分类树 `enumData` 与详情接口形态时使用。
  触发词：NPC、flk、国家法律法规数据库、上位法、search/list、enumData、flfgCodeId、zdjgCodeId、
  codeIdList、叶节点、gbrq、sxrq、gbrqYear、sxx、现行有效、已废止、已修改、尚未生效、公布日期、
  flfgDetails、bbbs、docx 下载、附件、NpcSerialGate、ConnectionReset。
  本子技能管"这一站怎么取"，取回来的东西在答案里算什么由分析层判；全程只发 HTTP 请求，
  **不调用任何模型、不消耗任何额度**。
---

# NPC 国家法律法规数据库（flk.npc.gov.cn）站点子技能

这一站是官方文本的上位法来源：服务端渲染加一枚 JSON 检索接口，没有 WAF、不要凭据、不启浏览器。
取数难点不在接入而在三处：**分类编号只认叶节点，而且法律性质与制定机关两棵树的编号会撞**（发进错的树
不回错误码，只把命中收到 0）；**`total` 随 `pageSize`、`pageNum` 换值**，拿它当命中总数或翻页上限
就会在一份还能翻的集合上提前收尾；**连发或并发直接断连**（TLS 握手就失败，HTTP 状态都拿不到），
所以取数路径装了一把跨进程串行闸。判据写在本文件，逐项读数（含取样口径）记在 `NOTE.md`。

## 工作流

**第一步 定这一趟要什么**——下面几行的入口、前置各不同，先选对再动手：

| 要什么 | 走哪条 | 前置 |
|---|---|---|
| 上位法（法律、行政法规）按关键词或税种找 | `tax_search.search_tax(关键词)`，即 `POST flk.npc.gov.cn/law-search/search/list` | 无：开放接口，不要凭据、不启浏览器 |
| 本体法名已知，只要那一份 | 同一函数的 `search_type=1`，命令行 `--exact` | 无 |
| 按法律性质门类或制定机关收窄 | 同一函数的 `flfgCodeId`／`zdjgCodeId`，值从 `GET /law-search/search/enumData` 取 | **先整份取回分类树**，只发父节点编号回 0（判据见第四步） |
| 按公布日期区间取 | `--from`／`--to`（`gbrq` 数组） | 只给一端要补另一端，空转形态见第三步 |
| 按时效状态取 | `--status` 整数（1 已废止／2 已修改／3 现行有效／4 尚未生效，`tax_detail.SXX_MAP`） | 一次只能要一种；不带 `--status` 等于「现行有效」而不是"全部" |
| 正文里提到某个词的那些法规 | `--scope fulltext` | 这一路带的 `_reliability: medium` 不能去掉，见第二步 |
| 一份法规的元数据、关联件与全文下载件 | `tax_detail.fetch_detail`（`--info`）／`download_file`（`--download`） | 先用 `--info`／`--preview` 看过；`--download` 要用户确认之后才执行 |

界面那一路不必启：网页上「范围」「精确」「公布日期」「时效」几个控件逐个映射到上面这几枚参数，
换算收在 `tax_server._ui_filters`（`scope`／`exact`／`date_from`／`date_to`／`aging` 五枚），
命令行与界面走的是同一份 `search_tax` 判据。

**第二步 翻页基准、每页条数与 `total` 先分清楚，再读条数。**

- `pageNum` 从 **1** 起算。国家税务总局那一路（search5）的 `pageNum` 从 0 起、`pageSize` 卡在 10 条，
  两站基准不同，照其中一个去改另一个就错位：基准错时的表现最骗人——结构正常、条数正常、翻页数正常，
  只是每一轮都少了相关度最高的那一屏；命中不足一页时首屏是唯一一页，丢掉它就取回空清单。
  换算各收在自己那一处（本站在 `tax_search.search_tax`）。
- 每页条数**照请求给行**：「环境保护」×现行有效发 `pageSize=100` 就取回满 100 行（上限未探，
  本层 `min(size, 100)`）。这与 search5 恒 10 行相反，翻页代码不能两边共用一个常数。
- `total` 不是命中总数：同一枚「环境保护」在 `pageSize=5` 报 3,118、在 10／20／50／100 报 224，
  换到第 23 页它又回 3,118，而第 624 页只给 3 行收尾。按 `⌈total/pageSize⌉` 算页数就会提前收尾。
  命令行因此不报可达页数：抬头只标「接口 total N 条」，单屏那一句印「第 N 页，本屏 M 条」并写明
  可达页数不按它推算。逐档读数与三条判据见 `NOTE.md` 第二节。
- 外壳两条形态相反：检索响应的 `total` 与 `rows` 挂在**根节点**上，不再包一层 `data`；
  同一前缀下的 `GET /law-search/search/enumData` 反倒包在 `data` 里。按一种解包法统一处理就会把另一条
  读成空——`tax_search` 用 `data.get("data", data)` 这种两边都收的写法。手工复测若只按 `data` 取，
  读数会记成 None；这样作废的 12 发记在 `NOTE.md` 开头的请求数表里。
- `--scope fulltext` 是全文分词匹配：接口确实按词过滤（「紫貂养殖」2,415 条、「zzzqqq123」7 条），
  但短词会被通用字带偏（「研发费用加计扣除」带到《诉讼费用交纳办法》），所以这一路一律带
  `_reliability: medium`，只能用来**定位**，不能用来确定某条规则的归属；要确定就用
  `--scope title` 或 `--exact`。

**第三步 日期与状态：语义、空转与两种"看着没生效"。**

- `gbrq` 是**公布日期**。国家税务总局／法规库那一路给的是**成文日期**（`cwrq`），两站这一列不是同一件事，
  跨源比日期时别把它们并成一栏，也别拿一方的区间去解释另一方的条数。
- 格式必须是补零的 `YYYY-MM-DD`，`tax_search.check_iso_date` 在发请求**之前**拦：`2024-1-1`、
  `2024-13-01`、`20240101` 都报错退出（CLI 退 2、接口那一路报 400），不会静默当成"这一维没填"。
- 只给一端按 `DATE_FLOOR`（`0001-01-01`）／`DATE_CEIL` 补另一界。**只给上界不补下界是空转**：
  「增值税」`--to 2020-12-31` 与不带日期都是 45 条，补界后 38 条。
- 接口收到 `orderByParam={order:-1,sort:gbrq}` 并不真按公布时间排（实测同一词回 2024-12-25、
  2011-01-08、1994-02-22、2025-12-25，明显被打散）。本层取回窗口后自己按公布日期降序排一次，
  所以 `--sort date` 排的是**取回的那个窗口**，要框"最新"就配 `--from` 收口；给了 `--sort date`
  就不再按标题命中重排，本体法不会自动置顶——既要本体法又要最新口径时分两次查。
- **`--exact` 带上日期时接口会丢掉检索词**，只按区间回一叠清单（「中华人民共和国增值税法」
  `--from 2026-01-01` 回 88 条、没有一条含"增值税"，同词不带日期是 2 条）。程序不拒绝这个组合，
  取回后按「标题是否含检索词」复核：报出的条数是复核后的，接口原报的留在 `source_total`，
  另打一句 `_date_note` 说明成因。真要按日期在全库收窄，走税务总局／法规库那一路的成文日期区间。
- `--status` 走 `sxx` 数组、一次一档；施行日期区间 `sxrq` 与公布年份数组 `gbrqYear` 本层没接
  （`gbrqYear=["2024"]` 实测能收窄，282 条，接的时候按 `NOTE.md` 第二节那一档给值）。

**第四步 分类编号只认叶节点，值域从 `enumData` 取，两棵树的编号会撞。**

- `flfgCodeId`、`zdjgCodeId` 两枚数组本仓库恒发 `[]`，实测它们都在收窄：「环境保护」×现行有效 ×
  `pageSize=5` 基线 3,118 条，`zdjgCodeId=[120]`（国务院）收到 57 条。
- **按门类收窄不能只发父节点那一枚编号**：只发「法律」的 `101` 回 0 条，发 `101` 自带的
  `codeIdList`（14 枚）回 19 条。难点是回 0 不区分原因——`101` 那发是编号发在了父节点，
  而「宪法」叶节点 `flfgCodeId=[100]` 那发的 0 是「环境保护」这个词下确实没有宪法类文件，
  **两种 0 在响应里是同一个形态**，看命中数判断不出该改参数还是该接受空结果。
  先按下面两条把编号确认成叶节点，剩下的 0 才读成"这一档没有内容"。
- `enumData` 给的是两棵树：法律性质 34 枚非空编号、制定机关 39 枚，其中 **22 枚在两边各指一种东西**
  （`100` 一边是宪法、一边是全国人大；`120` 一边是民法商法、一边是国务院；`220` 一边是监察法规、
  一边是辽宁）。编号发进错的树不回错误码，只把命中收到 0，所以取值必须连树一起取，
  别把两棵树的编号当成一套号段。整棵树只有 `enumData` 能给，界面上那些下拉框是它渲染出来的；
  它回三枚键 `flfgfl`、`zdjgfl`、`flcaStatus`，后者回 `0`，不是时效值域。

**第五步 详情层：元数据、四类关联与下载件。**

- 详情走 `GET /law-search/search/flfgDetails?bbbs=<法规ID>`。`ossWordPath`／`ossPdfPath` 嵌在
  `data.ossFile` 对象下，不在 `data` 顶层——按顶层取会静默拿到空串，于是下载只报「No download URL」；
  `content` 是单个节点对象而不是数组，取正文要按节点展开（`_flatten_content`）。
- `--info` 回的不是摘要，`--preview` 才是摘要；全文要 `--download`（docx／pdf），
  **用户确认之后才执行**这一步。下载件用 `_parse_docx_from_bytes` 解出段落（纯标准库，不装依赖）。
- 关联件四类各一栏，都在详情响应里：`xgwj`（相关文件）、`lsyg`（历史沿革，即被废止／被修改的那一版）、
  `xgzl`（修改资料）、`flfg`（制定依据）。一份法规"现行版在哪、哪一版被它顶掉"读这一层，
  检索清单层读不出这四组。
- 详情元数据默认缓存 1 小时（键前缀 `detail`，`--no-cache` 强制现拉），缓存命中时 `fetched_at`
  是第一次抓取的时刻，条目带 `_from_cache` 与 `_cache_age_s` 说明这一点；下载件不缓存，每次现下。

**第六步 限流：闸在里面，复测在外面按 15 秒。**

- 连发或并发会 `ConnectionResetError`，TLS 握手就断，HTTP 状态都拿不到。检索、详情、下载三个入口
  打的是同一个站，所以共用一把闸：`tax_search.NpcSerialGate`（`%TEMP%` 下一份跨进程文件锁加进程内
  `threading.Lock`）罩住，闸内另有 0.6 秒最小间隔与按 2／4／8 秒退避的四次重试。
- 等锁超过 `TAX_NPC_LOCK_TIMEOUT`（默认 180 秒）抛 `TimeoutError`，报错直说"另有进程正在打 NPC"——
  这是排队的提示，不是代码坏了，先找自己开的另一个会话。
- 绕开闸门手工复测时按 15 秒以上间隔。限流记录的台账（最近实测日期、方法、当时结果三格）在主技能
  源缺陷登记的「限流记录的实测台账」那一处单点维护，复测看到的现象记回那一行，本文件不另立一份。

**第七步 这一层只负责取。** `sxx` 的数字进 `status_code`（`SXX_MAP` 翻成四个中文状态），
公布日期进 `publish_date`，施行日期进 `effective_date`——归档与时效判定按这些录入值走，
比按标题形态猜准；空读成"站点没录"，不读成"这份没有时效"。0 条与深页那些坑见下面「边界」。

## 命令

命令行与函数同一份判据。全部只发 HTTP，零模型调用。

```bash
# 检索（标题路，默认）
python scripts/tax_search.py "增值税" --status 3 --size 20                 # 现行有效，第 1 页
python scripts/tax_search.py "中华人民共和国增值税法" --exact --status 3    # 本体法名已知，必须走精确
python scripts/tax_search.py "增值税" --sort date --from 2024-01-01        # 窗口内按公布日期降序
python scripts/tax_search.py "环境保护" --status 3 --size 5                # 看 total 那一枚在抬头怎么标
python scripts/tax_search.py "增值税" --scope fulltext --size 10           # 正文检索定位，带 medium 标记
python scripts/tax_search.py "增值税" --json                               # 结构化输出

# 详情与下载
python scripts/tax_detail.py --info <法规ID>              # 元数据 + 四类关联 + 正文树
python scripts/tax_detail.py --preview <法规ID>           # 摘要，不是全文
python scripts/tax_detail.py --download <法规ID>          # 全文 docx；用户确认后才执行
python scripts/tax_detail.py --download <法规ID> --format pdf
python scripts/tax_detail.py --cache-stats / --cache-clear

# 分类树整份取回（之后所有"哪些编号是叶节点"都在本地副本上复算）
python -c "import requests,json;r=requests.get('https://flk.npc.gov.cn/law-search/search/enumData',timeout=30,verify=False);d=(r.json().get('data') or {});print({k:type(v).__name__ for k,v in d.items()})"
```

翻页不用 `--size` 凑：`total` 那枚不能拿来推算页数（第二步那三条判据）。维度拼到 0 条时不要写
"库里没有这份文件"——先按第四步确认编号是叶节点、再确认它没发进另一棵树。

## 站点底座

| 项 | 读数 | 依据 |
|---|---|---|
| 谁在发请求 | 纯 HTTP：`requests` 直连，无凭据、无 Cookie、不启浏览器；检索、详情、下载共用一把串行闸 | 不带登录态可取；`tax_detail` 与 `tax_search` 都取 `npc_gate` 那同一把锁 |
| 检索接口 | `POST https://flk.npc.gov.cn/law-search/search/list`，`pageNum` 从 1 起、`pageSize` 照请求给行 | 拼参数在 `tax_search.search_tax`；逐档读数在 `NOTE.md` 第二节 |
| 详情接口 | `GET /law-search/search/flfgDetails?bbbs=<法规ID>`，`ossFile` 嵌在 `data` 下一层 | `tax_detail.fetch_detail` 的 docstring 记的就是这次解包踩的坑 |
| 值域的来源 | 分类树与状态值域都在 `GET /law-search/search/enumData`（回 `flfgfl`、`zdjgfl`、`flcaStatus` 三枚键，第三枚回 `0` 不是时效） | 界面上那些下拉框是这一枚接口渲染出来的 |
| 每页条数 | 照请求给行，本层 `min(size, 100)`；上限未探 | 见第二步 |
| 编码 | 接口回 JSON 带 charset；下载件是 docx／pdf 字节，段落由 `_parse_docx_from_bytes` 解 | 见第五步 |
| 时效与日期字段 | `sxx`（四个状态整数）、`gbrq`（公布日期）、`sxrq`（施行日期） | `tax_detail.SXX_MAP` 是这四个状态的唯一对照表 |

## 边界：这一层不给什么

1. **不给"0 条就是没有"的结论**。这一站的 0 至少有四种成因：检索词真的没命中、编号发在了父节点、
   编号发进了另一棵树、这一档确实没有该类文件。前两条分别靠整份 `codeIdList`、靠 `enumData` 连树取值
   来处置；判不动时把「用的哪个编号、取自哪棵树」写进答案，别只写"库里没有"。
2. **不给 `total` 当命中总数**。它随 `pageSize`、`pageNum` 换值（第二步），要"到底有多少条"得逐档重发或
   换用日期区间收口，报数时说明读的是哪一档。
3. **不给整库全量**：这一站是按词召回的开放接口，翻页取回的是窗口，不是封闭清单。
4. **不给总局与地方口径**。规范性文件、公告、文字解读在国家税务总局那一站（`subskills/chinatax/`），
   地方口径不在这一路。这一层给的只有国家法律与行政法规这一层级的文本。
5. **不给时效判定**。`sxx` 是录入值，一次一档；不带 `--status` 取到的是「现行有效」而不是全部，
   这条最容易读反。判定在 `tax_evidence.judge_validity`，观察时点要显式 `--at` 传入。
6. **不给 `--scope fulltext` 的准确归属**。那一路只用来定位相关法规，条目自带 `medium` 那句提醒，
   照抄进答案，不参与挑选主依据。
7. **不对 NPC 并发加压**。闸保证跨进程串行；绕闸复测的间隔与失败计数记回主技能那份限流台账，不另立一份。

## 命令依赖

上面的命令调 `scripts/tax_search.py`、`scripts/tax_detail.py` 两个模块，它们又用主技能 `scripts/` 下的
`tax_http`（统一发请求与短错误行）、`tax_cache`（详情缓存）、`tax_terms`（检索词拆分）。
这三个模块不只服务这一站：多源聚合与命令行、网页两路都从这里取数，拆出来就要在两边各维护一份，
所以留在主技能 `scripts/` 下。**除这几个模块之外，本子技能不依赖主技能的任何文档**：
判据、读数、命令都在本目录这两份文件里；只有第六步提到的那份限流台账是例外，它在主技能登记
是因为 `tests/test_rate_limit_ledger.py` 逐格解析那一份表，两处各记一份必漂。
