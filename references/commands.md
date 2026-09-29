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

默认**按需自适应**：连续 3 页没捞到新的法规库条目就收尾（上限 20 页）——总局站里
法规库条目占比低、集中在靠前页，后面多是新闻，硬翻到底只是白烧请求。代价是
**可能漏掉间隔 3 页以上的条目**；若出现 `total` 很少但 `total_hits` 很大的可疑情形，
就用 `--pages N` 显式指定页数——那时会关掉自适应、严格翻满，但翻得越深相关性越差。

返回里三个字段合起来看：`total_hits`（命中总数）、`pages_scanned`（实际翻了几页）、
`stopped_early`（是否因自适应早停）。三者能区分"确实没有"、"翻到上限也没有"
和"自适应提前收了尾"——后者值得用 `--pages` 再确认一次。

详情页 HTML 里有完整正文，但该站不声明 charset，必须按 UTF-8 解码才能读出
中文；程序已处理，正文容器是 `div.zscont`（注释）与 `div.arc_cont`（正文）。
加 `--body` 逐篇取正文。正文是视频/图片的条目会在结果里带 `media_only: true`
（CLI 里显示成"该条正文是视频/图片"）——**这不是取失败，重试也没用**，
别去引它的条文；只有真正取失败才会进 `body_error`。

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

**取到正文不代表它是依据**：税屋与公众号是第三方实务解读，可引用性只有 10 分，
只能进"参考材料"栏，见 `evidence_grading.md`。

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
改每页条数这类会影响"清单里缺了哪一屏"的代码时，把它一起改，改前留存的清单
就不会在改后被当成新结果复用。

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
`--cache-clear` 只作用于自己那一份——以前 `tax_search --cache-clear` 会把详情缓存
一起删掉，属于越界。

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

# 税屋 / 公众号 — 实务解读（参考材料，非依据）
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
