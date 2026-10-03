# 网页可视化界面

> 本文件是 `SKILL.md` ⑫ 的下沉版。网页（`frontend/index.html`，由
> `scripts/tax_server.py` 托管）是同一套后端的**可视化界面层**，与命令行脚本共用
> 五源。**网页只是呈现方式不同，不构成绕过工作流的理由**——顶层约束一律照常执行。

## 目录

- [何时启用网页路径](#何时启用网页路径)
- [第一步 启动服务](#第一步启动服务)
- [第二步 取数与呈现（后端直连接口）](#第二步取数与呈现后端直连接口)
- [第三步 网页能力总览](#第三步网页能力总览呈现给用户的内容)
- [第四步 合规约束不变](#第四步合规约束不变)

---

## 何时启用网页路径

- 用户明确要求"打开网页 / 给我看界面 / 用智能引导"
- 用户想要可视化结果（法规原文关键词高亮、4 Tab 解读弹窗）而非纯文字
- 用户希望用 4 步向导而不是自己写搜索词

## 第一步：启动服务

```bash
# 方式 A（推荐，一键）：自动装依赖 + 启动服务 + 打开浏览器
start_local.bat

# 方式 B（手动）：直接启动 Flask 服务（默认端口 5080）
python scripts/tax_server.py
```

启动后访问 `http://localhost:5080`，用 `curl http://localhost:5080/api/health`
验证服务已起。

## 第二步：取数与呈现（后端直连接口）

AI 直接调用网页同款后端接口完成取数，再用自动化打开页面呈现结果，
全程不需要用户手动点击：

1. **取数（后端直连）**，接口与网页按钮背后调用的完全一致。字段名以本技能实测
   回显为准，直连时按下面的路径取：
   - `POST /api/search` — 搜索法规。请求体字段：`keyword` / `scope`（title|fulltext）/
     `exact`（布尔）/ `status`（3=现行有效）/ `aging`（时效文本值，只对税务总局与法规库
     两源用）/ `sort`（relevance|date）/
     `source`（npc|chinatax|fgk|aggregated）/ `province` / `date_from` / `date_to` / `size` / `body`
     结果不在顶层：单源在 `result.results`，`source=aggregated` 在 `result.items`；
     每条的法规号字段叫 `id`，它就是后面三个接口要用的 `<bbbs_id>`。
     归类换源写在 `result._routed`，源被拦写在 `result.errors`（聚合）里。
     `source=chinatax`／`fgk` 时，`scope`／`exact`／`date_from`／`date_to`／`aging`
     不再只对 NPC 生效：`api_search` 经 `_ui_filters` → `tax_web_search.build_filters`
     把它们翻成 search5 的收窄维度透传下去（范围→`wordPlace`、匹配→`participleRule`、
     日期→`cwrqStart`/`cwrqEnd`、时效→`xxgkAging`），排序另走 `SORT_TO_ORDER`
     （`sort=date`→`orderBy=1`），命中的维度会原样回显在 `result.filters`（2026-10-02
     实测「增值税」×文件类白名单：基线 1908、范围=标题 908、时效=全文有效 252、
     两栏一起收窄 146；全站基数下同两维是 13152 与 2998）。
     时效在这一路走 `aging`（五个文本值）而不是 `status`（NPC 的时效码），界面靠
     `syncFilterControls` 随数据源换那一栏的值域，默认「全部」——白名单里六成条目
     该栏为空，照 NPC 的默认「现行有效」收窄会把现行文件一起筛掉。非法日期与非法
     时效在这一层报 400，不会静默回基线命中。归类自动换源到法规库那一路也吃同一套映射：sta 专题
     被改查法规库时同样经 `_ui_filters` 下推（否则专题题在界面上收窄无效，只在
     命令行生效）；归到税种走 `parent_law` 那条留在 NPC 一路，`scope`／`status`／`sort`
     照各自参数下推，只有 `exact` 被归类固定成精确检索。
     `_routed` 有两种内容：归到税种时写"按〈税种〉的本体法检索：〈法名〉"或
     "〈专题〉属总局专题，已改查法规库：〈检索词〉"；归不出税种时写
     `UNROUTED_NOTE`——这时检索词就是用户原话，NPC 按字面匹配标题（实测整句
     提问回 687 条、首条《证券投资基金法》）。这句必须看到：不写出来，一摞无
     关法条会读成"按本题找出来的依据"。
   - `GET /api/text/<bbbs_id>` — 法规全文。正文是 `sections` 数组，每项
     `{type, text}`，`type` 取 chapter|article|heading|body；条文数是 `article_count`，
     没有名为 `content` 的单字段。
   - `GET /api/detail/<bbbs_id>` — 法规元信息（发布机关、文号、时效），网页弹窗用，
     整包在 `detail` 下。
   - `GET /api/interpretations/<bbbs_id>?keyword=&province=` — 官方解读，列表字段是
     `sources`。`province` 换成该省税务局的站点去检索，**只有这个接口真的按省份过滤**
   - `GET /api/ai-interpret/<bbbs_id>?keyword=` — AI 通俗解读，正文在 `interpretation`。
     **这一步花的是使用者模型账号的钱**，所以过 SKILL.md ⑦ 第 19 条的闸门：闸门没开返 503，
     `code=paid_llm_disabled`；开了但上游没钱返 503，`code=quota_exhausted`，服务端
     不重试。两者的 `error` 都是照着能敲的开启步骤
   - `GET /api/health` — 除 `status` 外还回 `paid_llm`：`enabled` 是闸门开没开，
     `explanation` 是没开时的原因与开启方法。前端在用户点按钮之前就要读它，
     因为环境变量在 Flask 进程里，浏览器猜不到
   - `GET /api/web-related/<bbbs_id>?keyword=` — 相关网页，列表字段也是 `sources`
   - `GET /api/quick-tax-types` — 快捷税种
2. **开浏览器呈现**：AI 启动服务并用自动化打开 `http://localhost:5080`，
   把搜索结果 / 法规弹窗呈现给用户。
3. **代点智能引导面板（可选）**：若用户要走向导，AI 用自动化依次点选
   身份 → 税种 → 意图 → 补充条件 → "生成搜索"。面板内部就是按这些选择拼出
   关键词并调用 `POST /api/search`。

`/api/search` 已按 `tax_categories.md` 的 `authority` 自动换源：npc 专题按
`parent_law` 走精确检索，sta 专题改查法规库，响应里的 `_routed` 会写明这次改查了
什么；归不出税种时同一字段写的是"未能归类到税种或专题"。

**空清单的成因有四种，别把它们混成一种**。搜索源（`chinatax`／`fgk`）取数真失败
（连接超时、HTTP 5xx、响应非 JSON）时带 `_fetch_failed`——这是服务侧故障，界面排在
最前，写成"取数失败…可稍后重试"，绝不能读成"库里没有"（`search_chinatax` 的
`_empty_result`、`tax_fgk._scan_list` 的 `first_error` 分支各置这个标记）。收窄到 0 条
时带 `_filter_note`——接口对"维度拼窄"与"该库没有"回的是同一个 0，分不清，这句
必须原样显示（`source=chinatax`/`fgk` 且 `result.filters` 非空即为收窄）。翻页取空是
`_empty_reason`（见 `tax_web_search.search_chinatax`）。第四种是 `fgk` 翻完前 N 页没筛出
法规库条目，只带一句 `_error`（那是扫描结论，不是失败）。解读类接口（`/api/interpretations`、
`/api/web-related`）在搜索引擎被拦时会带 `engine_error`（并给出被拦的查询数
`queries_blocked`），此时 `sources` 为空只说明**没取到数据**。这几种标记都不带的空列表
才是"库里确实没有这份文件／该法规没有公开解读"。被拦与取数失败的那种结果不进缓存，
限流或上游恢复后重跑即可取到。

**搜索栏里没有"省份"，省份只在"官方解读"页生效**。人大法规库只有全国性法规，
没有省级维度。省份下拉现在长在法规弹窗的"官方解读"标签页顶部，切省就是**换检索
站点**：选中某省后这一页改去该省税务局的子站（`{子域名}.chinatax.gov.cn`）检索。
它**不影响**上一步的法规搜索结果。`/api/search` 仍接受 `province` 参数，但只原样
回填给调用方、不参与检索——地方口径要走 L3 用 `--scope title` 查省局文件。

## 第三步：网页能力总览（呈现给用户的内容）

- 搜索框 + 快捷税种按钮 + 高级筛选（检索范围 标题|正文 / 匹配方式 精确|模糊 /
  时效 / 排序 相关度|日期↓ / 数据源 NPC|税务总局|税务法规库|多源聚合 / 公布日期起止）。
  这些控件的**按源生效范围**要说清（`tax_server.api_search` 的四条分支各不相同）：
  - 数据源选税务总局或法规库：范围、匹配、日期、时效、排序五项全下推。前四项经
    `_ui_filters` → `tax_web_search.build_filters` 翻成 search5 的 `wordPlace`／
    `participleRule`／`cwrqStart`+`cwrqEnd`／`xxgkAging`，排序经 `SORT_TO_ORDER` 换成
    `orderBy`（日期↓→`orderBy=1`），实际用到的维度回显在 `result.filters`。日期这一路是真
    收窄（2026-10-02 实测法规库「增值税」起 2026-01-01 → 8 条全落在 2026-01-01～2026-08-27）。
    其余三项在同一基数下的实测值：基线 1908、范围=标题 908、时效=全文有效 252、范围＋时效
    146（全站基数下同两维是 13152 与 2998）。
  - 数据源选 NPC：范围与排序下推；时效发 `status`（数字时效码）；匹配／精确只在
    `resolve_tax_type` 归不出本体法时才听界面那一栏（归得出就被改成精确检索）；
    日期是坏的——`tax_search.search_tax` 填上 `gbrq` 后接口就不按 `searchContent` 过滤，
    实测 `search_tax("中华人民共和国增值税法", search_type=1, date_from="2026-01-01")`
    回 88 条、首条《民族团结进步促进法》、逐条核对没有一条含"增值税"，同词不带日期是 2 条。
    危害方向是"带日期反而递回无关清单"，不是收窄成 0。
  - 数据源选多源聚合：只有喂给 `search_tax` 的那几项生效（`scope`／`status`／`sort`，
    `exact` 由归类结果决定），总局、法规库、全网、税屋、公众号四路只按检索词取回——
    `tax_aggregator.aggregate_search` 里 `search_chinatax(keyword, size=size)` 不带
    `filters`，函数签名里也没有日期参数，`api_search` 那两路调用也就没把 `date_from`／
    `date_to` 传下去（实测同一个请求体带与不带 `date_from=2026-01-01` 回来的 12 条完全
    相同，里面仍有 2019-11-27 的）。归到 sta 专题时 NPC 整源被剔掉（`sources` 排除
    `"npc"`），那几项连落脚的地方都没有，只剩 `sort` 在跨源重排时起作用。
  - 「时效」那一栏在换到 search5 两源时整栏换值域：选项由 `syncFilterControls` 从
    `status` 码（现行有效／全部）换成 `AGING_S5` 五个文本值，且默认「全部」而不是
    「现行有效」——白名单里六成条目该栏为空，照 NPC 的默认收窄会把现行文件一起筛掉。
    归类自动换源到法规库那一路也吃同一套映射（见 `tax_server._ui_filters`）。
  空结果按成因分流显示：取数失败（`_fetch_failed`）单独报"取数失败…可稍后重试"排在最前，
  收窄到 0 条显示 `_filter_note` 那句成因，翻页取空显示 `_empty_reason`，
  法规库翻完未筛出显示那句 `_error`；法规库深页条目（`_reliability==medium`）卡面挂"仅参考"角标
- **智能引导面板（4 步向导）**：① 选身份（企业 / 个人·个体户 / 代理记账·税务师 /
  其他）→ ② 选税种 → ③ 选意图（查政策·税率 / 优惠资格 / 风险自查 / 申报流程 /
  发票问题 / 计算标准）→ ④ 补充条件 → 点"生成搜索"自动拼词并搜索。向导里**没有**
  "只看某省"这类选项：省份在本接口不过滤结果，别在界面上造出这个承诺
- 结果卡片分两种，别对用户说"都能点开看原文"：带 `id`（NPC bbbs_id）的条目点开是
  四标签弹窗；多源聚合里总局、税屋、公众号的条目没有这个 ID，卡面只给条目自带的
  原文链接，点开无弹窗。要这些条目的正文得回命令行——总局/法规库条目用
  `python scripts/tax_fgk.py "<关键词>" --body`，税屋与公众号用各自的取正文入口
- 4 Tab 法规弹窗：法规原文（关键词高亮）/ 官方解读 / AI 解读 / 相关网页；
  AI 解读这一页在切进去时会把闸门状态写在按钮下方——未开启时那里就是开启步骤，
  不是"生成失败"
- 税法要件落在会计确认与计量上时，结果区上方多一条提示（`/api/search` 的
  `accounting_note`）。这一栏只说该去取准则，界面上不答会计问题
- 题面点名的是草案/征求意见稿时，同位置多一条"收录范围"（`/api/search` 的
  `legislative_note`，判据见 `out_of_library_layers.md`）
- 相关网页这一页取不到时，头部那行的"全网检索被拦，…"是后端给的一句成因
  （`tax_http.short_reason` 整形过的错误，或 360 限流的固定说法），不是裸异常文本

## 第四步：合规约束不变

走网页路径**不豁免**任何顶层约束：仍须先分析再检索、仍须完成层级下挖与五源聚合、
仍须先判时效再引用、附实时时间戳与免责声明、禁止代替用户报出应纳税额、禁止预测
稽查结果、禁止引用已废止法规不标注。`/api/ai-interpret` 只是把同样的内容换成网页
呈现，不替代 SKILL.md ⑦ 禁止行为清单。
