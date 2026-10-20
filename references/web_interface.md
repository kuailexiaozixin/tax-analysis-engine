# 网页可视化界面

> 本文件是 `SKILL.md` ⑫ 的下沉版。网页（`frontend/index.html`，由
> `scripts/tax_server.py` 托管）是同一套后端的**可视化界面层**，与命令行脚本共用
> 六源。**网页只是呈现方式不同，不构成绕过工作流的理由**——顶层约束一律照常执行。

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
     `source`（npc|chinatax|fgk|aggregated，取值域是 `tax_server.SEARCH_SOURCES`，
     显式给这四个之外的值一律 400 并在 `error` 里列出取值域；不给这一维才是默认
     NPC——改前这样的值会落进 `api_search` 的 else 分支按 NPC 查完回 200，调用方
     以为结果出自自己点的那一源）/ `province` / `date_from` / `date_to` / `size` / `page`（1 起算，只对 NPC 与税务总局两路下推，法规库与聚合无此维度）/ `body`
     结果不在顶层：单源在 `result.results`，`source=aggregated` 在 `result.items`；
     每条的法规号字段叫 `id`，它就是后面三个接口要用的 `<bbbs_id>`。
     归类换源写在 `result._routed`，源被拦写在 `result.errors`（聚合）里。
     `source=chinatax`／`fgk` 时，`scope`／`exact`／`date_from`／`date_to`／`aging`
     不再只对 NPC 生效：`api_search` 经 `_ui_filters` → `tax_web_search.build_filters`
     把它们翻成 search5 的收窄维度透传下去（范围→`wordPlace`、匹配→`participleRule`、
     日期→`cwrqStart`/`cwrqEnd`、时效→`xxgkAging`），排序另走 `SORT_TO_ORDER`
     （`sort=date`→`orderBy=1`），命中的维度会原样回显在 `result.filters`。这四维在
     「增值税」×文件类白名单基数上各收到多少条、界面把范围与时效两栏一起收窄又剩多少条，
     是站点层读数，记在 `subskills/chinatax/NOTE.md` 第二节（那一行标明取的是
     `/api/search` 那一路）。
     时效在这一路走 `aging`（五个文本值）而不是 `status`（NPC 的时效码），界面靠
     `syncFilterControls` 随数据源换那一栏的值域，默认「全部」——白名单里六成条目
     该栏为空，照 NPC 的默认「现行有效」收窄会把现行文件一起筛掉。非法日期与非法
     时效在这一层报 400，接口对域外值回的是 0 条而不是错误码。归类自动换源到法规库那一路也吃同一套映射：sta 专题
     被改查法规库时同样经 `_ui_filters` 下推（否则专题题在界面上收窄无效，只在
     命令行生效）；归到税种走 `parent_law` 那条留在 NPC 一路，`scope`／`status`／`sort`
     照各自参数下推，只有 `exact` 被归类固定成精确检索。
     `_routed` 有两种内容：归到税种时写"按〈税种〉的本体法检索：〈法名〉"或
     "〈专题〉属总局专题，已改查法规库：〈检索词〉"；归不出税种时写
     `UNROUTED_NOTE`——这时检索词就是用户原话，NPC 按字面匹配标题（实测整句
     提问回 687 条、首条《证券投资基金法》）。这句必须看到：不写出来，一摞无
     关法条会读成"按本题找出来的依据"。

     size/page 取值域由 `_int_param` 统一闸：size 1~100，page>=1，
     坏值一律 400。`result._count_note` 把「库内总数」与「本轮给出」拆成两个口径写成整句，
     前端照抄进标签。`result._page_note` 只有在 `page>1` 且实际取数层没有这一维时出现
     （判定基于 `effective` 而不是用户点的那一源）。前端「本轮条数」与「页码」控件的启用判据是
     `PAGE_SOURCES` 常量，切换到另两源时按钮置灰并把理由写进 label.title。
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
   - `GET /api/quick-tax-types` — 快捷税种清单，整份由 `tax_search.TAX_TYPE_KEYWORDS`
     渲出（现有 33 项），每项只有 `label` 与 `keyword` 两枚字段且同值：点下去发的就是
     专题名，接下来按哪部本体法检索由 `resolve_tax_type` 与 `api_search` 的换源规则决定，
     界面不替它预判。这一份不在路由里手写第二遍，也不加第三枚字段（`icon` 那类）——
     前端只读那两枚，多出来的清单迟早和词表对不上，表现为界面上点不到而命令行走得通。
     发票不在归类词表的键里（它是 `tax_search.detect_intent` 的五个意图之一），
     入口在引导面板第三步的"发票问题"。
     键序就是按钮顺序：首页直接铺出前 18 枚，其余 15 项收在"展开其余 15 项"那一枚里。
     这个 18 不是凑整——`TAX_TYPE_KEYWORDS` 的键序前 18 项正是我国现行十八个税种
     （增值税到环境保护税，含烟叶税），第 19 项起才是横切专题，`QUICK_TAX_SHOWN` 取的
     就是这个段长。切点落在段中间时会有税种默认点不到。段长与这一枚数由
     `tests/test_server_routes.py` 的 `test_default_row_covers_all_statutory_tax_types`
     钉住（该用例另存一份十八个税种的名字，按单行法律／暂行条例数出来的，不从词表反推）。
     词表挪键序前要想到归类打平时的胜负手：`resolve_tax_type` 比的是（别名长度、
     有无本体法、别名长度），三项全平才由键序决定，2026-10-20 把烟叶税挪进税种段时
     359 道标注题逐题归类结果不变。
   - `GET /api/calc` — 算式展开面板的骨架清单。`骨架` 数组每项给 `名称`／`公式`／
     `参数`／`结果`，`限制` 是 `tax_calc.DISCLAIMER`。`参数` 来自
     `tax_calc.inputs_spec`，每项是 `{名称, 形态, 列}`：`形态` 取 `数值` 或 `税率表`，
     后者的 `列` 直接抄 `tax_calc.RATE_TABLE_COLUMNS`（走 `分段累进` 的三枚是
     上限／税率／速算扣除数，走 `超率累进` 的是上限／税率／速算扣除系数）。列名只能由
     引擎给——前端猜错的那一列会被 `row.get` 当成没填，界面少报一档。参数名与先后一律
     走 `tax_calc.inputs`，所以排表单和报缺参数同源，加一步就多一个框，不会变成
     "算出缺少参数但没地方填"。
   - `POST /api/calc` — 把一条骨架按给定值展开成算式。请求体是 `{骨架: 名称,
     参数: {参数名: 值}}`，值也可以写成带出处的 `{参数名: {值: …, 来源: …}}`
     （`tax_calc._split` 认这两种）。**纯本地求值：不联网、不调外部模型**，
     与 `/api/ai-interpret` 不同，这一路不动 ⑦ 第 19 条的付费闸门。成功回 `骨架`／
     `公式`／`步骤`／`结果`／`参数`／`无来源参数`／`限制`；`步骤` 每项有 `名称`／
     `算式`／`代入`／`结果`，用到累进函数那一步另挂 `分档`（`适用档`／`逐档`／
     `结果`／`速算式`），分档明细不进下一条算式，只挂着给人对着税率表核。
     四道 400 的分工：① 骨架名不在 `SKELETONS` 里 → `error` 加 `可选` 全名单，
     这道闸排在调用之前，否则 `tax_calc.inputs` 的 KeyError 落到通用分支就是 500；
     ② `参数` 不是对象 → 回一句说明两种写法；③ 参数名有多余 →
     `「骨架名」没有这些参数：…` 加 `需要的参数`，这一道也必须早于求值，因为 `run`
     把调用方的键并进求值命名空间（`values.update(given)`），多给一枚名为 `max` 的键
     就把算式允许调用的 `max` 换成一个 Decimal，随后报
     `'decimal.Decimal' object is not callable`，看着像引擎坏了，实际是请求里多带一个字段；
     ④ 缺参数 → `缺少参数：…` 一次列全（`Missing.names`，按 `inputs()` 的先后）
     加 `缺的参数`，不按步骤挤。取值写法坏掉的那些（非数值、布尔、空表、末档封顶、
     档界与速算扣除不自洽、表行写成列表、表行少给一列）各抛哪一类，见
     `api_calc_run` 的注释与 `tests/test_server_routes.py`，一律 400 并把引擎原话带回。
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
    其余三维在同一基数上的命中数见上文控件那一段，不重复一遍数字。
  - 数据源选 NPC：范围与排序下推；时效发 `status`（数字时效码）；匹配／精确只在
    `resolve_tax_type` 归不出本体法时才听界面那一栏（归得出就被改成精确检索）；
    日期发 `gbrq`，语义是**公布日期**，缺哪一端由 `tax_search.DATE_CEIL`／`DATE_FLOOR`
    补界。只给上界不补下界是空转（`gbrq` 发出去是空数组）：2026-10-03 实测「增值税」
    带 `date_to=2020-12-31` 回 45 条、与不带日期逐条一样，补界后 38 条、区间内最大
    2020-08-04。接口对**精确检索＋区间**这一组会丢掉检索词（`search_tax
    ("中华人民共和国增值税法", search_type=1, date_from="2026-01-01")` 回 88 条、
    首条《民族团结进步促进法》、逐条核对没有一条含"增值税"，同词不带日期是 2 条），
    所以 `search_tax` 取回后再按「标题是否含检索词」复核一次：`total` 只报复核后的
    条数，接口原报的 88 条留在 `source_total`，成因写成整句 `_date_note`。非法日期
    在发请求前由 `tax_search.check_iso_date` 拦下并报 400，不再静默当成"没填"。
  - 数据源选多源聚合：日期区间六路都喂，但生效方式分两种。NPC、总局与中国税网这三路
    由接口自己收窄（`tax_aggregator.aggregate_search` 收下 `date_from`/`date_to`，分别
    下推成 `search_tax` 的 `gbrq`、`tax_web_search.build_filters` 的 `cwrq_from`/`cwrq_to`
    与 `tax_ctax.search_ctax` 的 `startDate`/`endDate`；三家的"日期"字段含义不同——
    公布日期 vs 成文日期 vs 发布日期，同一区间在各侧圈到的条目本就不必相同）；
    360、税屋、微信公众号那三路没有日期参数，改在**本轮取回的条目窗口内**
    按条目自带日期补筛（`tax_aggregator.DATE_LOCAL_SOURCES`），逐源计数回显在
    `result._date_filter.local_window`，非零的源由界面渲成 📊 那一行。2026-10-03 实测
    「增值税」2024-01-01—2026-12-31：不带日期 16 条里有 4 条越界，带日期 9 条、0 条
    越界（公众号取回 5 条剔掉 4 条）。**没带日期的条目保留而不删**，只另计 `no_date`——
    360 那一路的解析结果压根没有日期字段，按越界处理会让整源消失，空清单就会被读成
    "该源在这个区间里没有内容"。范围／时效／排序仍按源各自生效，`exact` 由归类结果
    决定；归到 sta 专题时 NPC 整源被剔掉（`sources` 排除 `"npc"`）。聚合那两路同样
    在发请求前校验日期、非法报 400。
  - 「时效」那一栏在换到 search5 两源时整栏换值域：选项由 `syncFilterControls` 从
    `status` 码（现行有效／全部）换成 `AGING_S5` 五个文本值，且默认「全部」而不是
    「现行有效」——白名单里六成条目该栏为空，照 NPC 的默认收窄会把现行文件一起筛掉。
    归类自动换源到法规库那一路也吃同一套映射（见 `tax_server._ui_filters`）。
  空结果按成因分流显示：取数失败（`_fetch_failed`）单独报"取数失败…可稍后重试"排在最前，
  收窄到 0 条显示 `_filter_note` 那句成因，翻页取空显示 `_empty_reason`，
  法规库翻完未筛出显示那句 `_error`；带 `_reliability` 标记的条目在卡面上单独印一行提醒，
  句子直接取自条目自带的 `_reliability_note`（来源没写原因时退成一句明说"未写明存疑在
  哪一处"的话），界面不另存一份按档位配的文案，不再是"仅参考"角标——角标只给一个记号，
  读者不知道该核对什么
- **智能引导面板（4 步向导）**：① 选身份（企业 / 个人·个体户 / 代理记账·税务师 /
  其他）→ ② 选税种 → ③ 选意图（查政策·税率 / 优惠资格 / 风险自查 / 申报流程 /
  发票问题 / 计算标准）→ ④ 补充条件 → 点"生成搜索"自动拼词并搜索。向导里**没有**
  "只看某省"这类选项：省份在本接口不过滤结果，别在界面上造出这个承诺
- **算式展开面板**（页头那枚「算式展开」，`toggleCalc`）：与智能引导互斥，两块都靠右
  滑到同一位置，同开会让后开的盖住先开的却看不见标题，开一块就收掉另一块。骨架清单
  取 `GET /api/calc`，选一条后按 `参数` 的 `形态` 排表单：`数值` 一枚输入框（`calcVal_i`），
  `税率表` 一张编辑表（`calcTbl_i`，列头就是 `列` 那三枚，另有「加一档」和逐行删除），
  每枚参数旁再一格出处（`calcSrc_i`）。这一栏里不出现任何税率、扣除标准或档界，名目
  全部由引擎给、数值由填表的人从检回的文件抄。空着的那一格不进请求体（`calcPayload`
  逐格 trim，整行全空的档当作没这一档，一档都没填就把 `税率表` 整个键省掉），报缺参数
  由引擎给；回 400 时按 `缺的参数` 找回对应控件加 `.calc-missing` 描红，只有文字报错
  看不出少填的是哪一格。求值成功铺三样：`结果` 每枚数一个 chip、`步骤` 的
  「算式／代入／结果」三列、用到累进函数那一步另铺 `分档`（`适用档`／`速算式` 加 `逐档`
  一张表），最末一行的句子是 `tax_calc.DISCLAIMER`（引擎只做算式展开，不含任何现行税率
  与扣除标准，要改口径改的是参数来源）。`无来源参数` 非空时单独一行提醒数值要对着一份
  检回来的文件核、页面不替你判断它出自哪份文件。这一路**纯本地**：
  `TestCalcRoutes.test_post_never_leaves_the_process` 把 `socket.socket` 钉死仍回 200，
  与 `/api/health` 报的 `paid_llm.enabled` 无关；它输出的性质是"按你给的这些数展开的
  算式"，不是"系统判定该缴多少"，接出去引用时仍要排在时效核对过的那几条依据后面
- 结果卡片分两种，别对用户说"都能点开看原文"：带 `id`（NPC bbbs_id）的条目点开是
  四标签弹窗；多源聚合里总局、税屋、公众号的条目没有这个 ID，卡面只给条目自带的
  原文链接，点开无弹窗。要这些条目的正文得回命令行——总局/法规库条目用
  `python scripts/tax_fgk.py "<关键词>" --body`，税屋与公众号用各自的取正文入口
- 卡面 📅 那一栏的词按源给：`frontend/index.html` 的 `DATE_FIELD`，六枚键与
  `tax_aggregator.DEFAULT_SOURCES` 同集再加单源那一路的 `fgk`，值写这一路收的是哪枚
  字段（公布／成文／发布／站点发布）；`renderResults` 经 `dateFieldLabel(item)` 取词。
  钉它的是 `TestFrontendSourceVocabulary` 那一条：接口自己收窄那三路（`DATE_SERVER_SOURCES`）
  逐源对上后端 `DATE_SERVER_FIELDS` 的字段名，`fgk` 与 `chinatax` 两枚必须同词（`tax_fgk`
  从 search5 的行里原样抄 `publish_date`），`so360` 反向钉一次——那一路的条目压根没有
  日期字段，哪天真的开始给日期，就得同时往这张表里加一项。表里没有的源退成中性的"日期"，
  不猜它是什么日期。改前那一枚是 `renderResults` 模板里写死的「公布」，六路共用（提交
  d65da7b 的 `frontend/index.html` 可查），于是总局与法规库卡面把成文日期标成了公布日期；
  2026-10-20 真浏览器实测改后：`source=chinatax` 搜「增值税」四张卡全印「成文:」，
  `source=aggregated` 里 NPC 卡印「公布:2024」与总局卡的「成文:2021」并排。日期值本身
  一处没动，动的是那一枚字段名。弹窗头（`renderLawTextTab`）那一枚仍写「公布」——
  那个页面只对带 NPC bbbs_id 的条目打开，`publish_date` 本就是人大库的公布日期
- 来源角标（`sourceBadge`）读条目上的 `_source`：聚合那一路由 `tax_aggregator` 逐条标，
  四路单源（含 sta／overseas 两处自动换源）由 `tax_server._stamp_source` 标，所以屏上
  每一条都带着"这一批实际是谁取回的"。`SOURCE_UI` 数的是聚合六路，比 `SEARCH_SOURCES`
  少一枚 `fgk`——那是总局换源后落到的一层，不在六路之内，这一枚退回条目自带的
  `source_label` 渲角标。改前 `renderResults` 里那一枚写成 `SOURCE_UI[item._source] ? … : ''`
  的行内三元（同一提交可查），`_source==='fgk'` 的卡片整枚角标不渲染，而同一条卡片的
  日期栏已经按 fgk 标成「成文」，等于一边承认这一路有身份、一边把身份藏起来
  （实测 `source=npc` 搜「转让定价」，改后两条换源到法规库的条目角标印「税务总局法规库」）
- 六源在界面上的名字只存一份：`frontend/index.html` 的 `SOURCE_UI`（键序、`label`、
  `icon` 逐字对齐后端 `tax_aggregator.DEFAULT_SOURCES` 与 `SOURCE_LABELS`，`short`
  供「每源几条」那一行，`url` 供页脚链接）。七处渲源名的地方全从这张表取：每源计数、
  区间内补筛的逐源计数、卡面来源角标、页脚站点清单、空结果那句"切到多源聚合能查到
  哪几路"的提示、弹窗页脚的法规源名、页首副标题。副标题那一句由 `renderHeroSubtitle`
  拼：路数取 `Object.keys(SOURCE_UI).length`，名字取 `AGG_SOURCE_HINT`（由 `SOURCE_UI`
  的 `short` join 出）。改前那里是一行写死的静态文案「数据源自国家法规库 · 实时同步 ·
  现行有效」（提交 d65da7b 的 `frontend/index.html` 可查）：既断言了结果只出自一路，
  又对整屏结果承诺"现行有效"，而时效是否收窄要看这一轮筛了什么。改后实测这一行是
  「实时检索 6 路数据源：NPC、总局、360、税屋、公众号、中国税网 · 时效与日期在筛选栏里收窄」
  这几处改前各抄了一份，抄本之间已经互相不一致：npc 与 ctax 两处文本与后端不同（界面写
  "NPC国家法规库""中国税网(报社登载)"），页脚手写的站点清单只列 3 个，空结果提示只报
  两路。`tests/test_server_routes.py` 的
  `TestFrontendSourceVocabulary` 逐键比对内容并查出词表之外的第二份，加一路源只改
  后端那一处就会在这里显形
- 筛选栏的静态文案不写源语义。「日期」「时效」两枚控件的悬浮说明由
  `test_date_and_status_tooltips_name_no_source` 查：既不许出现源名，也不许出现某一路
  自己的字段名。理由是这两句对整屏结果说话，而本轮究竟哪一路收哪一枚日期字段、取回与
  剔除多少条，只有后端逐轮才知道（`result._date_note` 与 `_filter_note`）；说明只声明
  有两种生效方式——接口自己收窄、在本轮取回的条目窗口内补筛——并把读者指到结果上方
  那一行。字段名因此必须真的出现在那句里，`tests/test_date_window.py` 的
  `test_note_names_each_server_field` 拿 `DATE_SERVER_FIELDS` 三枚逐一比对
  `_date_scope_note` 的输出
- 快捷税种那一栏铺 `GET /api/quick-tax-types` 回来的 33 项，前 18 项（现行十八个税种）
  直接给按钮、其余 15 项由"展开其余 15 项"这一枚铺开（`QUICK_TAX_SHOWN` 取词表税种段
  的段长，与清单总条数无关；段长变了这一枚要跟着变，用例 `test_default_row_covers_all_statutory_tax_types` 会报红）。
  点一项就是把专题名填进搜索框发一次检索，界面不预判它归到哪部本体法——换源规则在
  `api_search` 那一侧
- 4 Tab 法规弹窗：法规原文（关键词高亮）/ 官方解读 / AI 解读 / 相关网页；
  AI 解读这一页在切进去时会把闸门状态写在按钮下方——未开启时那里就是开启步骤，
  不是"生成失败"
- 税法要件落在会计确认与计量上时，结果区上方多一条提示（`/api/search` 的
  `accounting_note`）。这一栏只说该去取准则，界面上不答会计问题
- 题面点名的是草案/征求意见稿时，同位置多一条"收录范围"（`/api/search` 的
  `legislative_note`，判据见 `out_of_library_layers.md`）
- 填了日期起止时，结果区上方多一条 📅（`result._date_note`）：这一句按源说明区间
  是被接口收的还是在本源窗口内补筛的、两个"日期"字段含义不同、补筛到 0 条不能读成
  "该源没有内容"、没带日期的条目已保留但不算区间内。补筛的逐源计数另列一条 📊，
  由 `result._date_filter.local_window` 渲出「取回／区间内／区间外剔除／无日期保留」
  四个数，只列本轮真的取回过条目的源。两句都在 `_routed` 那条下面、卡面之前，
  文字全部来自后端，前端不自己判断哪一维生效
- 相关网页这一页取不到时，头部那行的"全网检索被拦，…"是后端给的一句成因
  （`tax_http.short_reason` 整形过的错误，或 360 限流的固定说法），不是裸异常文本

## 第四步：合规约束不变

走网页路径**不豁免**任何顶层约束：仍须先分析再检索、仍须完成层级下挖与六源聚合、
仍须先判时效再引用、附实时时间戳与免责声明、禁止代替用户报出应纳税额、禁止预测
稽查结果、禁止引用已废止法规不标注。`/api/ai-interpret` 只是把同样的内容换成网页
呈现，不替代 SKILL.md ⑦ 禁止行为清单。
