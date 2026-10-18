---
name: shui5
description: |
  税屋（www.shui5.cn）站点子技能。当需要**从税屋按关键词找文章、限定栏目或发布时间区间找、
  不检索直接按栏目或标签翻它的清单、读回某篇正文、判断税屋这一路为什么 0 条或正文为什么短了一截**，
  或需要重测这个站的放行机制、站内搜索表单、栏目分页与正文容器时使用。触发词：税屋、shui5、
  实务解读、地方口径文章、税务问答、税屋正文、税屋取不到、WAF、acw_sc__v2、arg1、
  站内检索、栏目翻页、栏目表、标签清单。
  本子技能管"这个站怎么取"，母技能管"取回来的东西在答案里算什么"；全程只发 HTTP 请求、
  只驱动本机已装浏览器，**不调用任何模型、不消耗任何额度**。
---

# 税屋（shui5.cn）站点子技能

税屋是实务解读与答疑站，属母技能 ③ 的 L4 一层。它的取数难点不在"有没有这份内容"，
而在**同一个地址在没凭据时返回的是另一份页面**：没有放行 cookie 时，首页、文章页、
`robots.txt`、甚至一个不存在的路径都返回同一份挑战页，HTTP 全是 200，
所以"请求成功"与"取到内容"在这个站是两件事。本文件写这条边界上的判据，
逐项读数（含挑战页字节数）记在 `NOTE.md`，复测靠 `probe_shui5.py`。

## 职能分工

**留在母技能，不在这里改**：税屋是五源之一（`tax_aggregator.DEFAULT_SOURCES` 的
`shui5`）、L4 必做、角色 `practice` 进答案与论证、不得顶替被解读的那份文件当依据
（⑦ 第 6 条）、取回条目回 ⑧ 按层级·时效·角色定级。空结果归因看这一轮两条通道各走到
哪一步：`search_shui5` 回报 `_channels`，`tax_aggregator._build_gaps` 据此决定要不要给
税屋标 `blocked_by: so360`。这几条判据的措辞与用例都在母技能，本文件只写它们落到这个
站上时要知道的事实。

**归本子技能**：WAF 挑战面与凭据寿命、两条检索入口（360 `site:` 与站内原生检索）及其
栏目、发布日期区间、匹配域三个服务端过滤维度与按总数取全、栏目地图与分页参数形态、
栏目表 `data/shui5_columns.json` 的建与查、标签页取数、正文容器与失真点、站点的 robots
允许范围、实测台账、`probe_shui5.py` 复跑工具。

## 工作流

**第一步 定这一趟要什么**——下面几行的入口、耗时与凭据不同，先选对再动手：

| 要什么 | 走哪条 | 需要什么凭据 |
|---|---|---|
| 按关键词找文章，只要链接与标题 | `tax_shui5.search_shui5(channel="so360")`，即 360 `site:shui5.cn`（`tax_so360.so360_search`） | 不需要 |
| 按关键词找文章，还要每行日期、所属栏目与这一词的总条数 | `tax_shui5.search_native`（`POST /e/search/index.php`） | 一份放行 cookie，过一次约 6 秒 |
| 在上面那一路上再限定栏目、发布时间区间、只匹配标题，或按总条数取全 | 同一函数的 `classid`、`date_from` + `date_to`、`match`、`fetch_all`；`search_shui5` 收到这类参数时把 `auto` 换成站内那一路（`_channels` 里写明"没问 360"） | 同上 |
| 母技能默认那一路 | `channel="auto"`：先 360，它被拦或 0 条才换站内 | 换过去才要凭据 |
| 不检索，直接把某一栏的清单读几页 | `tax_shui5.list_column(栏目, pages=N)`，命令行 `--list` | 一份放行 cookie |
| 不检索，按站点自己在用的标签读清单 | `tax_shui5.list_tag(标签, pages=N)`，命令行 `--tag`；标签名没有清单可枚举，只能 `tags_of(页面)` 从链接里取 | 一份放行 cookie |
| 这一栏的目录名、classid、页数条数 | `load_columns()` / `resolve_column()` 读 `data/shui5_columns.json` | 读表不发请求；重建（`--update-columns`）才要凭据 |
| 只读已知 URL 的正文 | `tax_shui5.fetch_shui5` / `read_articles` | 一份放行 cookie |

**第二步 取放行凭据**：`tax_browser.BrowserSession` 用 `executable_path` 驱动本机已装
浏览器（`find_installed_browsers` 按 Edge → Chrome → Brave → 360 → Firefox 的顺序找）
跑一次挑战，把 cookie 交给 `requests.Session` 连读，不安装任何浏览器内核。
实测 5.9–7.0 秒过挑战；`acw_sc__v2` 单独就能放行，剩余寿命约 60 分钟，`acw_tc` 与
`ssxmod_itna`/`ssxmod_itna2` 单独提交都不放行（约 30 分钟）。**批量读多篇的正确入口是
`read_articles`：WAF 只过一次**，之后每篇 0.2 秒级；逐篇重启浏览器是把 6 秒乘以篇数。

**第三步 取数**：两条入口覆盖的是两种排序，不是彼此的备份——同一个词「研发费用加计扣除」
按文章 ID 归一后比首屏：站内 30 篇、360 10 篇，**同一篇 0 条**、并集 40 篇（当日两轮探针
读数一致）。它证明两路取的是不同的排序，但只比了这一个词的首屏，不推广成站点常数；
复跑探针第 5 项即可重算。所以"这一路 0 条"读不成"另一路也 0 条"，
`_channels` 里两条各记一句 `_status`，归因照它走。
站内搜索的结果页字节数每次都会变，提示页有两份且只差 31 字节，**判据一律用页面文案**：
「没有搜索到相关的内容」是这一轮 0 条，「系统限制的搜索时间间隔」是请求太密（等过间隔
重发一次）。词式敏感性：多词之间站点按 AND 收，「高新技术企业认定 条件」这类组合直接
落进 0 条，短词比长词稳。表单字段与行容器判据在 `tax_shui5.SEARCH_FIELDS` 与
`_ROW_*_RE`，改版时先比 `NOTE.md` 的读数再动这几个常量。

站内那一路能把四件事下推给服务端，各自的边界是实测出来的（逐项读数在 `NOTE.md`
「站内检索的服务端过滤参数」）：`classid` 认逗号多选（`152,12673` 与 `北京,深圳` 等价，
10 + 1 = 11 条与两栏各自相加相符），认竖线只取第一段、`classid[]` 回空页，所以
`classid_spec` 只按逗号切；`endtime` 单给就过滤，**`starttime` 单给不过滤**（同词同为
433 条），必须成对——只给 `date_from` 时这个缺口写进 `_filter_note`，不静默当成没限定；
`show` 是匹配域不是排版（`--match title` 那一档 150 条，三域 433 条）；`line` 站点不认，
每页恒 30 行，取全只能按页面报的总数翻页（`fetch_all` / `--all`，上限
`SEARCH_ALL_MAX_PAGES` 页，撞到上限时 `_status` 写明只取了多少）。
栏目名要换算成 classid 才能发出去，换算只查 `data/shui5_columns.json`
（`resolve_column`：目录名 → 导航名 → 首页短标签 → classid → 唯一的行内栏目名），
**查不到就整体报缺**，绝不发一条半截限定的命令——那看着像收窄了其实没有。
`list_column` / `list_tag` 取回来的行与检索结果同形（`title`/`url`/`date`/`snippet`/`column`），
`_read_pages` 说明真读了几页，`_error` 分开记每一页的取数结果。

**第四步 取正文，长文按本篇 ID 跟到末页**：正文容器是 `div.arcContent`，收口边界取
`left2e`（点赞／分享／扫码块，正文之后第一个兄弟容器），`left2b` 与 `blank20` 只作
站点改版时的第二、三道保险；漏掉 `left2e` 时抽样 12 篇有 11 篇的正文尾部混入
「点赞（）微信 微博 × 用微信扫码二维码 分享至好友和朋友圈」。
正文之后那一整块（`left2e` 起，含相关阅读与各
推荐位）本来就在切割之外，不需要另加剔除分支：抽样 12 篇平均 169.6 条去重文章链接落在
这一块，其中 `left2b` 单独那块只有 0–6 条，所以剔噪音不能只盯 `left2b`。
元信息在 `articleResource`（作者、时间）与 `articleDes`（摘要）。地址形如
`/article/<两位十六进制>/<ID>.html` 的中间段是哈希分桶，不是栏目，不能按它推断这篇属于
哪一栏。
长文的续页地址是 `<ID>$2.html`，由 `fetch_shui5` 按**本篇 ID 出现在页面上的 `$N` 链接**
逐页读并拼接（`_own_pages`）；不按"页面上有没有 `$N` 形态的链接"数——相关阅读里混着
别人文章的续页链接，会把单页文章判成分页文章。也不按"往后一页是不是 404"试探：实测
只有三页的第 70138 篇，`$4`、`$5` 都回 200 且正文与已读页同一份（站点把越界页码钳回已有
内容），文本与已读页重合即判到底。单篇最多读 `MAX_CONTINUATION_PAGES` 页，某一页取不到
时正文停在已读部分、原因写进 `_pages_error`，不静默当成整篇没有。

**第五步 回母技能定级**：取回的正文按角色 `practice` 进答案，写口径时标明原文待核。
这一步的判据在母技能 ⑧ 与 `references/evidence_grading.md`，本子技能不参与。

## 命令

```bash
# 检索并按篇取正文（一次过 WAF）
python scripts/tax_shui5.py "研发费用 加计扣除 口径" --size 5 --read
python scripts/tax_shui5.py "<关键词>" --size 5 --json                # 只要链接与标题，不启浏览器
python scripts/tax_shui5.py "留抵退税" --channel native --size 10     # 只走站内原生检索
python scripts/tax_shui5.py "研发费用加计扣除" --match title --all    # 只匹配标题，按总条数翻到底
python scripts/tax_shui5.py "增值税" --column 北京,深圳 --size 20     # 限定两栏（逗号；也接 classid）
python scripts/tax_shui5.py "留抵退税" --from 2025-01-01 --to 2025-12-31   # 区间要成对
python scripts/tax_shui5.py --list 实务解读 --pages 2                 # 不检索，按栏目读清单
python scripts/tax_shui5.py --tag 增值税法 --pages 2                  # 不检索，按标签读清单
python scripts/tax_shui5.py --columns                               # 打印栏目表，不发请求
python scripts/tax_shui5.py --update-columns                        # 重建栏目表（48 个请求，约 150 秒）
python scripts/tax_browser.py --check                                 # 探测本机已装浏览器，不下载内核
python subskills/shui5/probe_shui5.py                                 # 全量复测（十项，约 30 个请求）
python subskills/shui5/probe_shui5.py --no-browser                     # 只跑不需要放行的挑战面
python subskills/shui5/probe_shui5.py --keyword 留抵退税 --column ShuiWuChouHua
python subskills/shui5/probe_shui5.py --tag 增值税法                    # 指名复测标签翻页
```

`probe_shui5.py` 十项覆盖挑战面、凭据与寿命、两条检索通道与两路首屏的交集、栏目地图与
翻页、正文形态、站内过滤档位、栏目表与页面的同源核对加标签翻页；全项约 30 个请求。
判据本身不重写一份——第 9 项调发货的 `search_native` 与 `classid_of`，第 10 项调
`resolve_column`、`tags_of`、`_read_page`、`_col_control`、`_COL_PAGE_LINK_RE` 与
`TAG_RESULT`，其余项引 `tax_shui5` 的 `_search_rows`、`_ARC_RE`、`_own_pages`、
`SEARCH_FIELDS` 与两条
提示页文案，所以探针报出来的就是 `tax_shui5` 看到的。第 9 项的档位判据写成"这一档 vs
那一档"的自比（逗号两栏 == 两单栏之和、只给起始 == 不限、只标题 < 三域），不硬编码某天的
总数。它只读，cookie 只在内存里。逐项读数记在 `NOTE.md`，那份台账开头还写了每一轮的取样时刻。

## 栏目地图与栏目分页

11 个主栏目在首页 `li.mainlevel` 那一块（名单见 `NOTE.md`「栏目与正文形态」），
**另外 36 个地方栏目目录与 7 个 `/s/` 专栏不在 `li.mainlevel` 里**，它们是首页其他位置
的链接，只按 mainlevel 解析会读出"地方栏目 0 条"；`/article/` 目录合计 47 个，
全都收在 `data/shui5_columns.json` 里。三个取值例外要知道：
山西的目录名是 `category_181` 而不是拼音，而 `ShanXiShengCaiShuiFaGui` 这一栏的行内
栏目名是**陕西省法规**（两个目录的文章链接集合重合 0 条）——**归属只认行里的 `column`
字段，不认目录名的拼音串**；部分省级目录名被站点自己截断（如
`GuangDongShengCaiShuiFaGu`），**所以目录名只能从页面链接里取，不能由拼音拼**；
父栏页面里混着子栏的名字（`DiFangCaiShuiFaGui` 一页读到 20 个不同 `column` 名），
导航名只说明这一栏挂在首页哪个位置，不说明里面是哪一类文章。

栏目列表页的分页形态是 `/article/<栏目>/<classid>_<页>.html`（例：
`/article/ShiWuJiDu/12684_2.html`），控件在 `div.pageWrap > div.pagelist`，文案
「共 X 页 Y 条记录」。三种常见猜法都无效：`index_2.html`、`list_2.html` 为 404，
`?page=2` 返回第 1 页原样（参数被忽略，不是错误）。**按 `?page=` 翻栏目的人会拿到
第 1 页的全部内容并以为翻完了。** 行容器与检索结果页同为 `div.xwt2`，每页 30–35 行；
换成整页 `a[title=]` 取链接会把页头推荐区、侧栏、页脚的共有块算进来（同一栏目两页那样
取有 64 条重合），所以 `list_column` 用的是 `_search_rows` 那一份判据。

标签页 `/e/tags/?tagname=<真实标签>` 已接成 `list_tag`（命令行 `--tag`）。这一路与栏目页
相反，`&page=N` **真起作用**；但标签页没有页数控件，只能一页一页读到空，而且第 2 页回空
有两种成因——这个标签本身只有一页，或翻页失效，判之前先看第 1 页有没有行。标签名站点
没有可枚举清单，只能从页面链接里取（`tags_of`），不存在的名回 0 行。标签跨栏目且跨度大：
「社保基数」首屏 33 行覆盖 26 个行内栏目名，「增值税法」首屏 33 行全在财税实务一栏。

## 边界：这一层不给什么

1. **不给法定依据**：税屋的文章是第三方解读，被它解读的那份文件才是依据（母技能 ⑦ 第 6 条）。
2. **不给地方口径的替代**：省局原文在母技能 ③ L3 的 360 站内那一路，税屋的"地方法规"栏目
   是转载与解读，引地方规定仍要回官方站点核对原文。
3. **不给日期当时效判据**：正文区的「时间：2026-10-15」这类字段是站点自己的发布日期，
   不是文件的成文或废止日期；站内检索每行给的 `p.p3` 日期同样是发布日期。
4. **不取附件，这是站点的规定不是本层的缺口**：过 WAF 后的 `robots.txt` 明写
   `User-agent: *` + `Disallow: /d/`，而附件正挂在 `/d/file/p/` 下。正文里只留文件名＋链接，
   不下载、不解正文，任何时候不要改成去取 `/d/`。
5. **不并发加压**：`READ_INTERVAL = 2.0` 秒是逐篇连读的间隔，站内检索、栏目连读与标签连读
   另按 `SEARCH_MIN_GAP = 3.0` 秒留间隔（站点自己声明是 2 秒）；探针逐篇用 1 秒。
