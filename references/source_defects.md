# 源缺陷：已由代码保证的，与仍是边界的

> 本文件是 `SKILL.md` ⑩ 的下沉版。凡是修完的源缺陷，都从"要人记着的注意事项"
> 落成代码里的一个判定——不必再靠人记，程序自己会拦、会标、会把话说清楚。这里逐条
> 登记"由哪段代码保证、保证到什么程度"，最后列出**真正剩下的边界**（程序做不到、
> 也不该假装做到的）与**量过之后决定不改的**。用法见 SKILL.md 各工作流小节。

## 目录

- [由代码保证的源缺陷](#由代码保证的源缺陷)
- [限流记录的实测台账](#限流记录的实测台账)
- [案例通道的实测台账](#案例通道的实测台账)
- [两个值得单独说明的细节](#两个值得单独说明的细节)
- [量过之后决定不改的三件事](#量过之后决定不改的三件事)
- [主线动作归属](#主线动作归属)
- [仍然是边界的几件事](#仍然是边界的几件事)

---

## 由代码保证的源缺陷

| 源缺陷 | 现在由谁保证 | 保证到什么程度 |
|--------|--------------|----------------|
| NPC 限流，并行必现 | `tax_search.npc_gate`（实现为 `tax_http.SerialGate`）：`%TEMP%\tax-analysis-skill-npc.lock` 跨进程文件锁 + 进程内 `threading.Lock` | 多个进程同时开跑会自己排队，不用再记"别并行"。**这句记录是什么时候量的、今天还成不成立，见[限流记录的实测台账](#限流记录的实测台账)** |
| 搜狗被反爬 → 税屋只剩站内那一路；站内那一路也要浏览器过 WAF，过不了就整层空 | `tax_aggregator` 返回的 `gaps` 与 `degraded_note` | 缺了哪层、为什么缺、能不能拿别的源顶，都写成一句可直接照抄的话；网页端渲染成警示条，不再冒充普通的"0 条" |
| 官方解读的无期限组合缓存遮蔽搜狗 30 分钟过期；15 秒线程等待会丢掉仍在 HTTP 时限内的结果 | `tax_server.search_interpretations` 逐站有序取数，不另存组合缓存；三个适配器的 `tax_doc` 同步 | `test_interpretations_page_cache_expiry` 在 TTL 前后核对真实取数层 fetch 次数 1／2；`test_interpretations_sources_run_in_order_without_worker_timeout` 验证逐站完成与顺序，不增加生产调用层 |
| 普通 4xx 重复请求，增加等待且消耗预算 | `tax_sogou._fetch` 只重试网络异常与 5xx；403／429 仍冷却，其他非 200 直接报错 | `TestSogouWebSearch.test_only_server_errors_are_retried` 核对各状态的请求与等待次数；网络异常恢复、验证码及跳转另有用例。离线响应向量不证明线上放行 |
| fgk 翻得越深相关性越差 | `tax_fgk` 第 2 页起标 `_reliability: medium`，并写上 `FGK_DEEP_NOTE`（`FGK_SHALLOW_PAGES = 1`） | 深页条目留在结果里参与分层，提醒就是那句写明"取自第几页、引用前要回上一级数据库核对上位法"的原文，不降权也不剔除 |
| 公众号并发加压触发反爬 | `tax_wechat._sogou_get`：1 秒最小间隔 + 搜狗专用串行闸（`%TEMP%\tax-analysis-skill-sogou.lock`） | 打 `weixin.sogou.com` 的请求（检索、链接还原）统一进闸；离线并发用例断言同一时刻在跑的请求数峰值为 1 |
| `_reliability: medium` 只能定位 | `tax_evidence.grade` / `_caveats` 解析该标记；`tax_aggregator` 把整源标记连同说明逐条带进聚合结果；`tax_formatter` 的两条渲染路径都会印出来 | 转成一条具体提醒，句子照抄条目自带的 `_reliability_note`，不参与 `pick_primary` 的排队；单源渲染路径吞掉这句就只剩一个角标，所以单源与多源归并两条路径都要先印警告 |
| 同一个档位在不同来源指不同的事 | 原因由来源自己写（`tax_fgk.FGK_DEEP_NOTE`、`tax_search.RELIABILITY_NOTES`、`tax_answer._legis_round`），定级层、命令行摘要与界面卡面一律照抄，不按档位配固定文案 | `medium` 的三个出处（NPC 全文分词命中、法规库深页排序变松、立法过程件不在收录范围）各得各的提醒；按档位配一句就有一句是假的——全文检索与站内检索都没有"页位"这件事。`test_medium_reminder_is_the_sources_own_sentence`、`test_cli_prints_the_reminder_text_not_a_level_ban`、`test_frontend_prints_the_reminder_not_a_downweight_badge` 钉住 |
| `_reliability: low` 在值域里却没有来源会发出来 | 标记值留在 `_reliability_of` 的值域里备用；`tax_search` 不发"不得作为依据引用"那句——那是降权模型的说法，与核对无关 | 带 `low` 的条目按同一规则处理：有说明照抄说明，没说明用 `RELIABILITY_CAVEAT["low"]` 那句中性提醒；`test_reliability_does_not_change_the_pick` 与 `test_pick_is_by_role_not_by_marker` 钉住"标记不改挑选结果" |
| 立法过程草案件混进主依据 | `_legis_round` 给草案/审议页条目打 `legislative_process`；`pick_primary` 把它整条挡在主依据之外；`tax_answer` 草案轮覆盖 legis+shui5+wechat 三源 | 草案线索即使唯一召回也不当主依据，`_why` 写明"本组只有立法过程线索"，行本身仍留在【待核对线索】与 `_runners_up` 里；`test_legislation_lane_items_cannot_become_primary` 与两条 `test_draft_round_*` 钉住 |
| 取数失败被记成"这一轮 0 条" | `_rows_and_error` 把源失败与判空分开带出；`gather` 记 `errors` 与 `rounds_done[].failed`；`compose`/`_print_answer` 打【取数失败】 | 主依据静默降级读得出来了；`test_source_defects.py::TestRoundFailureIsNotSilent` 三条（具名失败、干净空结果仍静默、草案轮不得顶位）|
| 实务文章讲的规则可能早已废止，而"降权标签"看不出这一点 | `tax_answer.check_practice_citations` 按文号回法规库查存在与时效，写在条目 `official_status`；`tax_evidence.OFFICIAL_CAVEAT` 九种结果各一句，`citation_note` 报核对进行到哪 | 每条实务材料自己说清"援引的那份文件现在还算不算数、下一步做什么"；九种结果逐一可达由 `test_nine_outcomes_are_all_reachable_and_each_has_a_sentence` 钉，字面量漏配句子由 `test_every_outcome_literal_in_the_producer_has_a_caveat` 扫源码拦 |
| `search_fgk` 零命中时也会写一句 `_error` 当说明 | `_doc_number_in_library` 只认 `_fetch_failed`／`_empty_reason` 两个标记判故障，句子本身不作判据 | 接口挂了与库里真没有这一份分成 `lookup_failed` 与 `not_in_library` 两种结果，读者动作不同；`test_zero_hit_with_an_explained_empty_is_a_miss_not_a_failure` 正反两个方向都钉 |
| 总局接口的 `pageNum` 从 0 起算，与本项目对外的页码差一位 | `tax_web_search.search_chinatax` 在发请求处减 1；`tests/test_routing_terms.py::test_search5_page_index_is_zero_based` 打桩断言发出去的 `pageNum` | 基准错时结构照常、条数照常，只是每轮都丢掉相关度最高的首屏。清单缓存键带 `tax_fgk.LIST_KEY_REV` |
| NPC 那一路的 `total` 随 `pageSize`、`pageNum` 换值，同一枚「环境保护」×现行有效在 `pageSize=5` 报 3,118、在 10／20／50 报 224，换到第 23 页它又回 3,118 | `search_tax` 一次只取一页，翻页不读这一格；命令行回显也不再从它推算页数（`tax_search.py` 的 `total_tag` 与单屏那一行：抬头标「接口 total N 条」，单屏印「第 N 页，本屏 M 条」并写明可达页数不按它推算）。逐档读数与取样口径见 `subskills/npc-flk/NOTE.md` 第二节 | 危害落在回显上：默认 `--size 20` 那一句报成「12 页 224 条」，而同一集合按 `--size 5` 能翻到 624 页——报的不是可达上限。接口自己换值这件事离线钉不住，能钉的是回显不再把这一枚当命中总数与页数上限：`tests/test_source_defects.py::TestFlkTotalIsNotTheEchoedHitCount` 两条（抬头不带「共」、回显不出现「第 X/Y 页」）钉住，复现入口在 `subskills/npc-flk/NOTE.md` 第三节「复测入口」 |
| 总局列表项里的文号、时效、效力级别不逐个落字段就一律报"未标明" | `tax_web_search` 把录入项 `govDoc.docNum`/`xxgk_aging`/`xxgk_effectLevel`/`cwrq` 落成 `document_number`/`status`/`effect_level`/`publish_date`，`tax_fgk._scan_list` 逐条透传，`tax_evidence.rank_of`/`judge_validity` 按 `effect_level` 定档、按 `status` 判时效 | 主依据一行直接印文号与时效；`status` 带上 `status_from` 说明是哪个录入项给的，判级理由可追溯 |
| 解读件标题整份嵌着原文件名，被认成原文件本身 | `tax_evidence.rank_of` 先于分类与形态规则判「以「解读」收尾」→ 技术性口径；`tax_answer.locate_cited_document` 把原文排在解读之前 | 只取到解读时 `compose` 不把它放主依据，另给 `cited_note`（"原文待核"），答案里必须写这句 |
| "已修改"被当成时效不明 | `tax_evidence.judge_validity` 把"已修改/已修订"判为 `effective`，并在 `note` 里追加"引用须按修改后的版本" | 仍在效的文件留在依据层，该说的话换成"引哪一版" |
| 空清单有四种成因，塌成一句"未找到/库里没有"就分不清是哪种 | 见下三条：`_fetch_failed`（取数真失败）、`_filter_note`（维度拼窄）、`_empty_reason`（翻页取空）、翻完未筛出的 `_error`；`frontend/index.html` 的 `renderResults` 按"越靠近真相越先看"分流，取数失败排最前 | 越界是翻页用法问题、拼窄要放宽维度、失败要稍后重试，四种都不写成"该库没有这份文件"；盯它的是 `test_source_defects.py::TestFrontendShowsDegradedNote::test_empty_state_keeps_the_causes_apart` |
| 规则陈述类问题被回三条"你的主体/地区/金额未交代" | `tax_analyze.RULE_STATEMENT_TYPES` 与 `RULE_AXES`：`lookup`/`fill_blank` 只保留时点轴，其余缺失转成 `rule_note` | 追问只问会影响规则本身的问题；个案缺失改在答案里以【适用边界】写明 |
| "这类题必须有哪些依据"只写在 ① 表格与 ② 四轴的散文里，没人能逐条判有没有，"依据充分"成了作答人自报的一句话 | `tax_coverage.load()` 载入时把注册表 `data/evidence_requirements.json` 的要件名与 `QUESTION_TYPES[型]["needs"]`、轴键与 `CONTEXT_AXES` 逐一比对；`assess` 逐项判 已满足／缺／不适用／待核，出 `覆盖率 = 已满足 ÷（总数 − 不适用）` | 改代码里的名字不改这张表就载入报错（五种漂移各有一条用例）；`None`（还没检）与 `[]`（检了没有）分成待核与缺，检索故障读不成"这类题不需要依据"；待核永不计入已满足；分母为 0 出 `null` 不出 1.0。用例 `tests/test_evidence_coverage.py` |
| 「尚未生效」在传了 `--at` 又没有施行日期时被转正成现行有效 | `tax_evidence.judge_validity`：`at` 分支对 `pending` 单独走一条判定——有施行日期且不晚于观察时点才转正，没日期留在 `pending` | 观察时点那条推荐用法不再把没开始施行的文件送进依据层（`pending` × 规范性文件 = 38.5 分）|
| 「财税文件」那一栏根本不录时效，上位规则被成片压在依据线以下 | `tax_answer.corroborate_validity_from_target` 读点名文件正文首段"根据……规定"列出的制定依据，给状态判不出来的条目打上 `corroborated_by`；`tax_evidence.judge_validity` 认这个字段，按在效计分 | 只有状态"判不出来"的条目会被补，明文废止或未生效的一律不动；只读首段；答案里【其余法定依据】那一条下面会印证据来源，要照抄 |
| 同一档检索意图在 markdown 与界面各有一套标签表，漏一档不报错 | `tax_search.INTENTS` 登记取值域；`tests/test_routing_terms.py::test_intent_vocabularies_agree` 断言它与 `tax_formatter.INTENT_HEADERS`、`tax_server.INTENT_LABELS` 键集合相等，并且与主线九类题型键互不相交 | 这一档只决定展示措辞；真正下结论用的判型是 `tax_analyze.QUESTION_TYPES` 那九类 |
| 时效录入项没填时接口回的是字符串 `"null"`，被当时效文本读 | `tax_web_search.aging_of` 把 `null`/`none`/`-`/`—`/`/` 归一成空串，`search_chinatax` 走它 | 空串不进 `status`，条目照实报"时效未标明"；网页徽章不会印出 `null` |
| 归不出税种时，一整摞字面匹配的无关法条看起来像按本题找出的依据 | `tax_server.UNROUTED_NOTE`：聚合与单查 NPC 两条分支都在归不出税种时写进 `result._routed`，`frontend/index.html` 的 `renderResults` 渲染成一行 | 结果层分辨不出相关性，就把"没归类"这件事交给界面说；两次实测见 `test_server_routes.py` 与 `test_source_defects.py` |
| 跨税种通用的词收进某一个税种会抢走别处的整题 | 不收词，改为把边界写成用例：`test_alias_table_pins_accepted_and_rejected_words` 同时钉住"收进来的词归对"和"没收的词没被收回来" | 排序按别名长度定胜负，实测"应纳税所得额"改判 13 题、"业务招待费/广告费"改判 6 题（见 `tax_categories.md`）|
| 留空前是系动词"是/为"被当成列举动词，整题既拿不到填空分也拿不到选项分 | `tax_analyze._ENUM_VERBS` 只收集合动词（`包括/有/属于/有哪/以下`），不收 `是/为`；`test_content_decides_and_form_only_breaks_ties` 钉住 | 把这两个字加回去、其余不动，1001 题立刻多出 38 题兜底（18→56）|
| 填空形态分无条件压过内容分，带留空的算税题被改写成"术语填空" | `tax_analyze.classify` 的 `if is_fill_blank and not scores`：形态只在词表零命中时参与定胜负 | 代价在检索计划：`liability` 要"计税依据与税率"，`fill_blank` 要"定义条款"。单独放开这一条，137 题被形态抢走。判据用 `probe_classify.py --oracle` |
| 判型理由把形态加分也说成"命中 N 个该类信号词" | `tax_analyze.classify` 里的 `form_of` 记下每类分数中有多少来自形态，`reason` 按 `word_n`/`form_n` 分开写 | `fill_blank` 的信号表是空的；现在靠形态判到的题明写"题面形态：……（不靠信号词）" |
| 用户点名的文件被"标题里引用了它的另一份文件"顶掉 | `tax_terms.title_identity` 判"就是这一份"：文种不同直接否，其余要求核心标题互相包含或属漏字缩略；`tax_answer.gather` 只有首位带 `_cited_identity` 才打 `_cited_target`，`compose` 见 `cited_located=False` 就回到正常分层并写 `cited_note` | 实测点名「税收征管法」时字面命中率 1.0 的那条是国税发〔2001〕110号——它在书名号里引用了这部法（用例见 `out_of_library_layers.md`）|
| 全网那一趟取到的条数一变，整路结果静默消失 | `tax_server._search_web_broad` 一律返回 `(results, why)` 二元组；`test_broad_web_survives_to_the_payload` 在真实边界打桩，1/2/3 条各断一次；`test_source_defects.py::TestTupleReturnContract` 静态扫 `-> tuple[A, B]` 的函数不许 `return` 单值 | 调用方按 `items, why = ...` 解包，返回裸列表时成败取决于长度 |
| 取数失败那句裸异常把界面与命令行的错误行撑满 | `tax_http.short_reason`：抹掉链接与参数串、折行压平、丢掉 urllib3 的 `(Caused by ...)` 嵌套、按空格边界截断，末尾留异常类名 | `str(e)` 给的是三百多字整条查询串，真正的原因反而读不出来（用例 `test_http_layer.py::TestShortReason`）|
| 用户问的是草案，取回的同名现行版本被当成草案内容 | `tax_analyze.LEGISLATIVE_STAGES` 与 `legislative_stage` 在 ① 登记立法阶段，`legislative_note` 写成整句；命令行与界面"收录范围"共用这一句 | 判据只看题面用词；盯它的是 `test_legislative_stage_flags_draft_wording` 与 `test_legislative_note_survives_the_keyword_swap` |
| 接口对「维度拼窄」与「该库根本没有」回的是同一个 0，用户看不出区别 | `tax_web_search.build_filters` 只放行有实测数字支撑的七组收窄维度（2026-10-01 五组＋2026-10-02 的时效与效力等级），`search_chinatax` 在 `filters` 非空且 `total==0` 时写 `_filter_note`（把发出去的维度一并附在句尾），`tax_fgk._scan_list` 把它从第一页接住并顶替"翻完前 N 页"那句 | 这七组都已接进命令行；盯它的是 `test_search5_zero_with_filters_says_it_might_be_too_narrow` 与 `test_filters_produce_too_narrow_instead_of_libraries_empty`。取值域外（`--aging 有效`）在 `build_filters` 就 `ValueError`：2026-10-20 实测接口对域外值回 `total=0`（当日基线 1910），不回错误码也不回基线命中，与"这一维真的没有内容"是同一个形态 |
| 同一文件在库里有现行版与被废止版两份，正文里却读不到对方的链接 | `tax_fgk.fetch_associations` 走 `queryManuscriptAssociation`（POST 到 www 域、入参是 `article_id_from_url` 从 URL 末段取的 id），关联条目里相对链接一律补 `FGK_HOST` 前缀；`--assoc` 逐条挂载 | 静态详情页 HTML 里那四组是空的，`fetch_fgk_body` 又把标签全剥干净，所以 `<a href>` 一条都抠不出来；接口主机与链接主机相反这件事是本机实测出来的（POST www 200 / fgk 404，链接 fgk 200 / www 404），盯它的是 `test_fetch_associations_parses_measured_payload` 与 `test_article_id_from_url_forms` |
| 界面高级筛选栏的范围／匹配／日期／排序只对 NPC 生效，数据源选到税务总局／法规库就被静默丢掉，控件看着全局实则空转 | `tax_server.api_search` 的 chinatax／fgk 分支经 `tax_web_search.build_filters` 把控件翻成 search5 `filters` 透传（范围→`wordPlace`、匹配→`participleRule`、日期→`cwrqStart`/`cwrqEnd`、时效→`xxgkAging`），排序另走 `tax_server.SORT_TO_ORDER`（日期↓→`orderBy=1`），非法日期与非法时效报 400；「时效」那一栏在 search5 两源换成五个文本值且默认「全部」——白名单基数 1908 条里 1165 条该栏为空，照 NPC 的默认「现行有效」收窄会把现行文件一起筛掉；0 条成因走 `result._filter_note`，深页条目在卡面上单独一行提醒 | 实测两栏都收窄（范围=标题＋时效=全文有效）「增值税」1908→146，单发时效 252；全站基数下同一维是标题 2998、基线 13152；盯它的是 `test_chinatax_maps_ui_filters_into_the_request`、`test_fgk_fulltext_does_not_narrow_to_title`、`test_bad_date_on_chinatax_is_400_not_silent_zero`、`test_bad_aging_on_chinatax_is_400_not_silent_zero`、`test_ui_aging_reaches_xxgk_aging_and_blank_sends_nothing`、`test_sort_from_ui_reaches_order_on_both_search5_paths` |
| 「时效」换值域时把「全部」那项写成空数组，浏览器下拉里冒出一个 `undefined` 选项，而 `select.value` 仍回 `""` 看着正常 | `frontend/index.html` 的 `syncFilterControls` 把两档值域都写成 `['值','文案']` 两项数组；`test_frontend_aging_control.py::TestOptionPairsAreNeverIncomplete` 静态扫 `const opts=` 那行，命中空项 `[[]` 就报红 | **这条只有真浏览器读那一栏才看得见**：本机 2026-10-02 打开 `filterStatus` 逐源读出 chinatax／fgk 首项是字面量 `undefined`（`st.value` 却是 `""`，纯静态读源码与后端测试全绿），修回 `['','全部']` 后复读四源选项集：npc／aggregated 两档、chinatax／fgk 「全部」+五档 |
| 归类把 sta 专题自动换源到法规库那条路仍把那几个控件丢掉，只有手选数据源才生效 | `tax_server.api_search` 抽出 `_ui_filters`，sta 专题改查 `search_fgk` 时也走它下推 filters 与 `SORT_TO_ORDER`（非法日期、非法时效同样 400） | 换源那一路若只透 `body`，界面收窄对专题题就静默无效；盯它的是 `test_sta_topic_routed_to_fgk_carries_ui_filters` |
| 取数真失败与"翻了没筛出/库里没有"共用一句 `_error`，界面一律印"未找到" | `tax_web_search._empty_result` 与 `tax_fgk._scan_list` 的 `first_error` 分支各补 `_fetch_failed`，`renderResults` 把它单列成"取数失败…可稍后重试"排在最前，不进未找到分支 | 服务侧故障被读成"换个词重搜就有"是静默降级；盯它的是 `test_request_failure_is_flagged_not_read_as_no_hit`、`test_scan_list_flags_fetch_failure_apart_from_empty`、`test_fetch_failed_flag_survives_into_payload` |
| 检索不发 `label` 时是全站，法规库清单被新闻与各地动态挤满：「转让定价」全站前 3 屏 0 条法规库条目，自适应早停在第 3 页收尾，整趟取回 **0** 条 | `tax_web_search.FILE_LABELS` 是十个文件类标签的白名单，`search_chinatax` 与 `tax_fgk._scan_list` 默认按它发（`file_only=True`，只有 CLI 的 `--all-labels` 能关，界面那一路恒用默认范围）；实测同一词收窄后 18 条全部取回，「小微企业」4822→184。清单缓存键的 `LIST_KEY_REV` 随之从 `pn0` 改成 `pn0-files`，非默认范围另走 `tax_fgk.scope_token` | 收窄的代价是漏掉标在「视频政策解读」「图片政策解读」上的法规库条目（实测「研发费用加计扣除」全站前 3 页 7 条），那类回的是 `media_only` 空正文；`_scan_list` 在 0 条那句 `_error` 里把范围写明，不让人把"窗口里没有"读成"库里没有"。盯它的是 `test_file_labels_is_the_default_scope`、`test_all_labels_switch_reopens_the_whole_site`、`test_scan_list_threads_scope_and_order_to_every_page`、`test_scope_and_order_have_their_own_cache_keys`、`test_zero_hit_sentence_names_the_label_scope` |
| 界面「公布日期」那一栏在三条路径上语义不同却长得一样：多源聚合根本没下发（`tax_aggregator.aggregate_search` 的签名里没有 `date_from`/`date_to`）；NPC 只给上界时 `gbrq` 发出去是空数组，等于没收窄；NPC 精确检索（`search_type=1`）带上日期后接口丢掉检索词，只按区间回一叠与本题无关的法律清单 | 聚合分支把区间下推：NPC 缺端补界（`tax_search.DATE_CEIL`/`DATE_FLOOR`，只给上界也发真区间），税务总局经 `tax_web_search.build_filters` 的 `cwrq_from`/`cwrq_to`；搜狗、税屋与微信公众号这三路的日期都只在部分条目上（搜狗与税屋取自结果引用行，公众号取结果块里的时间），改在本轮取回的条目窗口内按条目自带日期补筛（`tax_aggregator.DATE_LOCAL_SOURCES`），逐源计数进 `_date_filter.local_window`。精确检索＋区间这一组不拒绝请求，改成取回后按「标题是否含检索词」二次核对：`total` 只报复核后的条数，接口原报条数留在 `source_total`，成因写成 `_date_note` 交界面照抄。格式校验统一走 `tax_search.check_iso_date`，四条分支（NPC 单源、聚合、chinatax、fgk）非法日期一律 400 | 2026-10-03 本机实测：只给上界「增值税」45→38 条（区间内最大 2020-08-04；修前是 45 条、首条 2025-12-25，与不带日期逐条一样）；聚合「增值税」2024-01-01—2026-12-31 从 16 条里有 4 条越界收到 9 条 0 越界；精确检索「中华人民共和国增值税法」起 2026-01-01 的 88 条复核后留 0 条（88 留在 `source_total`），同一个词不带日期是 2 条。**没带日期的条目保留而不删**，只另计 `no_date`——搜狗、公众号与税屋那三路都只有部分条目带日期，按越界处理会整源消失，再把空清单读成"该源在这个区间里没有内容"。盯它的是 `test_date_window.py` 的 `test_only_to_uses_floor`、`test_overfetch_inherits_the_range`、`test_drops_off_topic_and_notes_it`、`test_forwards_to_both_server_sources`、`test_undated_items_are_kept_and_counted`、`test_aggregated_bad_date_is_400`、`test_npc_single_source_bad_date_is_400`、`test_local_counts_are_rendered` |
| 法规库正文里的表格被摊平，税率档位与产品范围对不上 | `tax_fgk._table_lines` 把 `<table>` 单独切段铺成 Markdown 行，`_text_of` 按"文字段—表—文字段"的顺序拼；`colspan` 补空列、`rowspan` 把值带到它盖住的每一行、行宽不齐补到最宽、只有一行时不插分隔行 | 摊平丢的是行列对应，形态分两种：Word 粘贴的表每格包着 `<p>`，摊平成"一格一行"（c5204270 的税则表 191 个单元格、152 个 `<p>`，摊平 152 行 vs 铺表 49 行，表头四格成了 '序号'/'商品名称'/'税则号列'/'备注' 四行）；另一种是包着图片的单格表（c5211628、c5208804 各两张），摊平与铺表都回空。单元格不带 `<p>` 时才会整行首尾相接成一串字——这 8 篇里没有这种形态。**合并格留空比留错更坏**，所以 `rowspan` 的延续行填同一个文本而不是空串。真实占比：2026-10-04 本机扫两批共 75 篇详情页（检索词取自「税目税额表」「出口退税率」等），8 篇正文含 `<table>`（35 篇那批 6 篇、40 篇那批 2 篇），现行公告多为"表在附件里"（见下一行）。对账口径：那 8 篇里的 6 篇（c5194303、c5204270、c5204332、c5203630、c5211628、c5208804）逐篇比 `expiry_of`/`period_start_of` 与文号抽取，摊平与铺表两种输出 0 处差异；表内摊平行共 642 行，逐行在新输出里都找得到，0 行丢失。副作用一并钉住：跨格的「自X年Y月Z日 / 至…」现在被 `" \| "` 分开，不再被 `tax_evidence._EXPIRY_RANGE`（只容空白间隔）拼成一段假执行期限——这 8 篇里没有出现两端分格的表，所以那是正则层面的风险而不是已发生的缺陷。盯它的是 `test_tax_fgk.py::test_table_lines_markdown_shape`、`test_text_of_mixes_prose_and_tables_in_order`、`test_table_cell_split_does_not_fabricate_a_period` |
| 正文是图片/视频的条目只回一句"无文字内容"，读者拿不到看原文的入口 | `tax_fgk._media_urls` 从正文容器抠 `img/video/audio/source` 的 `src`，按详情页 URL 拼成绝对地址（相对、站根 `/`、协议相对 `//`、已带协议四种写法），跳过 `data:` 占位、去重；`fetch_fgk_body` 在 `media_only` 那一路带出 `media_urls`，`search_fgk(with_body=True)` 透到条目上，命令行在"该条正文是视频/图片"下面逐行印「原文素材」 | 2026-10-04 实测：`--all-labels` 搜「研发费用加计扣除」翻 3 页取正文，5 条 `media_only` 全部带出素材地址（4 个 `.mp4`、1 个 `.jpg`），抽直链各回 HTTP 200（`video/mp4` 19712228 字节、`image/jpeg` 971707 字节）。这一栏**不进清单缓存**（它是正文层字段，缓存里只留清单）；取不到 `src` 时不带这个键，不编造地址。盯它的是 `test_media_urls_absolute_dedup_and_placeholders`、`test_media_only_body_carries_asset_urls` |
| 随文的税率表、减免税清单常常只做成附件，正文容器里根本没有这张表 | `tax_web_search._attachments` 把接口 `appendix` 栏收成 `[{name,type,url}]`（名或链缺一项的丢掉、`appendixType` 归一成小写），`search_chinatax` 落成条目 `attachments`，`tax_fgk._scan_list` 逐条透传，命令行印「附件: 名称（链接）」 | 不接这一栏，答案只能说"正文没有表"，指不到"表在附件《X》"。实测检索「消费税 成品油」前 10 条里 6 条带附件（`.xls` 税率表、`.doc` 纳税申报表），`appendixUrl` 是绝对地址、`appendixContent` 恒为空串；附件是清单层字段，跟着清单进缓存。盯它的是 `test_search_fgk_forwards_attachments_and_media` |
| 政策文件库清单的索引只有一份，`sync --channel` 换栏目时集合 SHA1 只对同栏算 diff，于是"无更新（集合未变）"顶替了重建，磁盘上留着上一栏那批记录 | `tax_gov_list.sync` 在调 `ListSynchronizer.sync` 前读磁盘索引的 `栏目` 字段，与本次请求栏目不同就置 `force=True`；读不出来（文件损坏或缺字段）也按不一致处理 | 2026-10-04 实测：先 `sync --channel 税务部门规章`（86 条），再 `sync --channel 行政法规`，回显"无更新（集合未变）"，而 `stats` 仍报 税务部门规章 86 条——`lookup`/`seed-cache` 用的都不是刚请求那一栏。修后本机按同序重跑：规章栏 86 条 → 行政法规栏 65 条（集合 SHA1 `7f00d35b…`）都各自落到索引上，再跑第三次同栏（`sync --channel 税务规范性文件`，索引已在该栏）仍走集合 diff 报"无更新（集合未变）"，force 没有变成每次爬全。盯它的是 `test_gov_list.py::TestChannelSwitch` 的两条（`test_stale_index_from_other_channel_forces_rebuild`、`test_same_channel_does_not_force`），后者保证同栏重跑仍走集合 diff，不把人家的 force 变成白白重爬 |
| 清单出口的时效性没走空值归一，字符串 `"null"` 被当成"这一条有时效标注" | `tax_gov_list.normalize_item` 收完元数据后过 `tax_web_search.aging_of`，与 search5 出口共用 `_AGING_BLANKS` 同一套空值口径 | 2026-10-04 实测「财税文件」栏整栏 1532 条翻到底 0 条真值，其中抽样 150 条（第 1/3/5 页）是 138 空串 + 12 条 `"null"`。不归一，`stats` 的时效性分布多出一档 `"null"`、`build_index` 的「时效性缺失」少报，而这一栏本该记成"官方没填"；`judge_validity` 也会拿 `"null"` 当状态文本去比对。盯它的是 `test_placeholder_aging_counts_as_missing` |
| 栏目注册表的键是本地起的别名，与接口自己报的 `channelName`、条目里的 `效力等级`、search5 的效力等级取值域各叫各的，四者要靠人记住对得上 | `tax_gov_list.CHANNELS` 的键统一取接口自己的 `channelName`（"其他"改成"其他文件"），七栏键逐个落在 `tax_web_search.EFFECT_LEVEL_VALUES` 里；`--channel`、`stats` 的栏目名、索引行的 `channel` 字段因此只需要一个名字 | 值域与 id 形态由 `test_gov_list.py::TestChannelRegistry` 三条钉住：`test_channel_ids_are_distinct_hex32`（32 位十六进制、互不重复、默认键在册）、`test_channel_keys_are_the_api_own_names_and_facet_names`、`test_measured_aging_values_all_judge_known`（用例里那份判级表与 `AGING_VALUES` 键集合相等，逐值经 `judge_validity` 不落 `unknown`；七栏整栏分布记在 `CHANNELS` 注释）。加新栏要先过这条，取值域没登记就不收 |
| 离线 `lookup --aging` 是精确等值比对，写成"有效"筛出 0 条，与"这一栏没有现行有效的文件"长得一样 | `tax_gov_list.lookup` 在筛之前按 `tax_web_search.AGING_VALUES` 校验，域外值 `SystemExit` 并把五种取值逐个列进报错；与检索面 `tax_web_search.build_filters` 同一口径（域外值在动手筛/发请求之前就报） | 2026-10-04 对本机 1925 条索引实测：`lookup 增值税 --aging 有效` 退出码 1、报错写出「全文有效、已修改、全文失效、全文废止、尚未生效」；`--aging 全文有效` 命中 2 条；`--aging 尚未生效` 是域内值，命中 0 条也只报"命中 0 条"不报错——域内筛空是结果，用法错不是。盯它的是 `test_gov_list.py::TestLookupFilterDomain` 两条 |
| 政策法规库各栏目的可浏览页看着能按 `<c码>/listflfg.html` 拼，实际页面名不统一——按模板拼出来的「税务部门规章」`c100011/listflfg.html` 回 404 | `tax_gov_list.CHANNEL_PAGES` 把七条 URL 逐条录成数据（不拼接），`build_index` 落进索引顶部的 `栏目页`，`stats` 与 `--json` 都带出；未登记栏目落空串而不是拼一个看着像的链接 | 2026-10-04 本机对七条 URL 逐个 GET：六条 `listflfg.html` 全 200，规章那条 `c100011/list.html` 200，而 `c100011/listflfg.html` 404。静态 HTML 里栏目名由 JS 渲染，页面自身只在导航与脚本里回显本栏 c 码，所以只能逐条量。`sync --force` 重建 1925 条索引后 `stats` 印出 `栏目页：https://fgk.chinatax.gov.cn/zcfgk/c100012/listflfg.html`；索引里没有这一格时 `stats` 明写"这份索引没带栏目页，重跑 sync 即补齐"，不印空串冒充有链接。盯它的是 `test_gov_list.py::TestChannelPages` 四条（键集合与 `CHANNELS` 相等且都在政策法规库域下、规章那一条的页面名不与其余六栏同质、未登记栏目落空串、索引缺这一格时 `stats` 写明缺在哪而不是印空串） |
| 中国税网检索的翻页只认 `pageNo`（0 与 1 都回第一页），`start`／`page`／`pageNum`／`p`／`offset`／`currentPage` 六枚别名被站点忽略后落回起始位 | `tax_ctax.search_ctax` 的翻页循环只改 `pageNo` 这一枚；`tests/test_source_defects.py::TestCtaxSearchContract::test_page_aliases_are_never_sent` 用打桩会话收下发出的参数，断言这六枚别名一枚都不出现 | 发别名时 `foundNum` 与条数全都照常，只是每一页都是第一页——"按 total 取全"会退化成"把首屏取 N 遍"，站点不会报，只有这条断言拦得住。镜像落点（`SEARCH_ENDPOINTS` 两条同内容）由 `test_mirror_endpoint_is_tried_when_the_domain_fails` 覆盖，两条都失败时那句"不是限流"由 `test_both_endpoints_failing_is_named_as_a_failure` 钉 |
| 同一枚 `q` 会把位置参数盖掉：`q=` 与 `title=` 一起提交时 `foundNum` 仍只按 `q` 那一份回 | `search_ctax` 里的 `tax_ctax.MATCH_POSITIONS` 把「匹配域」实现成"提交哪一枚参数名"，`match` 只选中一枚下发；`match` 落在这四枚之外时不发请求，`_status` 写明不在值域并把四枚列全（换成 `q` 发出去的那一路读数和全文一样，看着像限定生效了）；`test_default_match_sends_only_q`、`test_match_title_sends_only_the_title_slot`、`test_match_outside_the_four_slots_is_refused` 分别钉住默认、`match=title` 与拼错三种写法实际发出的参数集 | 「只匹配标题」若写成 q 与 title 双发，取回的是全文命中的那一摞而标题限定形同未发，条数还看着对。取样口径（省略 channel 时「增值税」`q=` 46,889、`title=` 11,911、`keyword=` 908）记在 `subskills/ctaxnews/NOTE.md`；命令行那一侧 `--match` 另有 argparse 的 `choices`，两处取值都取自 `MATCH_POSITIONS` |
| `channel` 给 0、3、20、-1 这四枚回的全是同一份 37 字节空结构，与"这一词站内没有命中"长得一模一样 | `search_ctax` 把 0 当"不限"——整个参数不下发（走网站与触屏两端并集），1 与 2 照常下发，这三枚之外的值（实测过 3、20、-1）不发请求，`_status` 里写出站点那一路回的是 37 字节空结构；`test_channel_zero_omits_the_param`、`test_channel_outside_the_sites_domain_is_refused` | 把那个空结构读成判空，会把有结果的词报成 0 条；这一档与"库里真没有"那一份逐字节相同（`{"start":0,"foundNum":0,"article":[]}`，口径见 `subskills/ctaxnews/NOTE.md`「触屏端与整站规模」），两种成因在返回值里分不开，所以只能拦在下发处 |
| 日期区间只认 `yyyy-mm-dd`，毫秒或斜杠分隔这一档越界直接回 HTTP 500，看着像站点挂了 | `search_ctax` 在拼参数前逐枚 `re.fullmatch` 校验，起始与截止各判各的，域外写法带"没走成：…不是 yyyy-mm-dd 形态"退出；`test_bad_date_is_refused_before_the_request`、`test_one_sided_date_sends_only_that_side` | 校验排在发出请求之前，用例断言写坏日期时一枚参数都没发出去；只给一侧时另一枚日期参数不下发，单边区间照常收。空词且没限栏目那一类用法同样停在发出前（`test_empty_keyword_without_column_is_refused`）——空词只在限定栏目时才是"数整栏条数"的合法写法 |
| 检索中途翻页失败会被读成"这一词就这么多条"，第 2 页起取不到的那一段静默丢掉 | `search_ctax` 把首页失败与后续页失败分成两条出口：首页失败回 `_status` 加 `_error`（一条没有），第 2 页起失败把"停在已取的 N 条"写进 `_status` 并保留已取行；`test_second_page_failure_is_not_rewritten_as_zero_rows` | 首页失败当判空会把"站点挂了"读成"站内没这份文件"；用例断言第二种出口的 `_status` 带着页码与已取条数，且已取行数不为零 |
| 站点确实有重名栏目，按栏目名限定会静默选中其中一个 nodeID | `tax_ctax.column_lookup` 回 ok／ambiguous／unknown 三态，`search_ctax` 与 `list_column` 都在非 ok 时收手：ambiguous 把候选行逐个写进 `_status` 并给 `_error: ambiguous column`，unknown 明写"没有把半截限定发出去"并指向 `--update-columns`；`nodeid_of` 只对 ok 回值 | 两种非 ok 都是零请求退出——`TestCtaxColumnScope` 的 `test_ambiguous_name_is_refused_without_a_half_limit`、`test_missing_name_tells_to_rebuild_the_table` 断言打桩会话一条请求都没收到；三态本身由 `test_three_states_of_the_lookup` 按 4 行样例表逐态钉，`test_nodeid_of_returns_empty_for_both_non_ok_states` 钉住"判不了就不给 id" |
| 父栏目 nodeID 照常能被检索接口接受，但检索按叶节点收，父节点整片回 0 条 | `search_ctax` 不拒绝父栏目（nodeID 照发），只在 `get_column` 报 `parent=True` 时补一条 `_filter_note`，写明这一档回 0 是因为限到了父节点、要限定这一类请指名子栏目；`test_parent_column_is_sent_but_flagged`、`test_leaf_column_carries_no_parent_note` | 与 `tax_search`／`tax_web_search` 的 `_filter_note` 同一口径：维度拼窄导致的 0 不写成"站内没有"。叶节点不带这条说明；数字 nodeID 跳过栏目表（`test_numeric_node_skips_the_table`），栏目表缺这一行时不会拦住合法的 id |
| 一稿多发：政策文同时挂几个栏目，检索按栏出行，同一篇就出好几行 | `tax_ctax.dedup_rows` 按「标题＋发布日期＋文号」三项并行，其余栏目名排序去重收进 `also_in_columns`；`search_ctax` 的收满判据与末尾截断都走它，合并掉的行数写进 `_status` | 判据不用 `articleid`——实测「财政部 税务总局公告2026年第29号」`foundNum=4`，四行只有 `nodesearchname` 不同而 id 各一枚，站点就是把同一份稿子登了四篇（取样口径见 `subskills/ctaxnews/NOTE.md`）。`TestCtaxDedupCountsArticles` 四条：四栏合 1 篇并带出另 3 栏名、判据双向（改任一项就不合）、`--size` 按篇不按行、篇数不够时第二页照发。**第四条钉的是翻页上限**：`cap_pages` 若按 `want` 换算行数而不是取 `SEARCH_MAX_PAGES`，合稿重的栏目会在第一页就收手并交出不够篇数的结果——这条用例是把 `cap_pages` 改回按行数换算才报红的 |
| `--max-rows` 在帮助里写的是行数上限，翻到底那一档过去只认页数上限；`--size` 给 0 白发一次请求再交空清单，给负数时末尾截断把最后一行悄悄丢掉 | `search_ctax` 把两枚上限分开执行：`size` 管合并后的**篇**，`max_rows` 管站点给的**行**，两档（翻到底与不收满）都收口，撞到行数上限时把行裁到那一行并在 `_status` 写「已到 max_rows=N 行的上限…不是这一词只有这些」；`max_rows` 只收紧，`want` 取 `size` 与 `max_rows` 里小的那一份，反过来顶不开 `--size`；`size` 非正（不收满时）、`max_rows` 为负、`list_column` 的 `pages` 非正都在发出之前退出，回「没走成」并把该枚参数的值域写进那句 | 打桩会话（每页 50 行、`foundNum=100000`，零真请求，读数只用于比对代码执行的是哪一枚参数）：`fetch_all` 配 `max_rows=5` 改前发 40 次交回 2000 行、改后发 1 次交回 5 行；`size=1` 配 `max_rows=3` 改前交回 3 行、改后 1 篇；`size=0` 改前 1 发 0 行、改后 0 发；`size=-1` 改前交回 49 行（第 50 行被截断丢掉）、改后 0 发；`find_by_doc_number` 走的就是 `fetch_all` 加 `max_rows=200`，同一份打桩下从 40 次请求降到 4 次。盯它的是 `TestCtaxSearchContract` 的 `test_fetch_all_stops_at_max_rows`、`test_max_rows_only_tightens_and_never_expands_size`、`test_non_positive_size_is_refused_unless_paging_to_the_end`、`test_negative_max_rows_is_refused` 四条与 `TestCtaxColumnPageRows::test_pages_below_one_is_refused_instead_of_reading_one_page`，与本节 `MATCH_POSITIONS` 那行的 `test_match_outside_the_four_slots_is_refused`、`channel` 那行的 `test_channel_outside_the_sites_domain_is_refused` 合起来共七条。七处写法退回在隔离副本树里逐处打红各自那一条：越界 `channel` 照样发出去、越界 `match` 换成 `q` 发出去、非正 `size` 照发一次请求、负 `max_rows` 不拦、翻到底不看行数上限、`max_rows` 顶开 `size`、非正 `pages` 读成一页；未变异的控制组把整份用例跑绿 |
| 2023-02 改版前的老行（域名后直接日期、三段路径、http）在 https 与 http 两路都取不到正文，目录段反推不出来 | `tax_ctax._address_state` 只按路径段数判（`len(segs) < 4` 即不可达），`fetch_ctax` 与 `read_articles` 都在发请求前用它，不可达的原因挂在行上的 `_url_state`；`read_articles` 不丢行 | 取样 2,617 行里 1,834 行属这一类（日期止于 2023-01-03），余下 783 行是四段 https 路径、逐条可达；对同一行试原址、换 https、再补五枚目录名共七路，全回 500 加一份 572 字节错误页（取样口径见 NOTE.md）。`TestCtaxAddressState` 七条覆盖两协议都拒、四段放行、无地址拒、老行一发不出、可达行不带标记、行字段映射（非日期 `author` 留在 `author`、日期形态转 `draft_date`）、两枚摘要只作展示；`TestCtaxReadKeepsUnreachableRows` 钉住"取不到正文的老行留在原位并把原因写在行上" |
| 正文页响应头不带 charset，requests 按 ISO-8859-1 解出乱码且不报错 | `tax_ctax._http_get` 只在响应头没声明 charset 且当前编码是 `iso-8859-1`／`latin-1` 时改写为 utf-8；站点自己声明了编码的一律不动 | `TestCtaxForcedUtf8` 三条：无声明按 utf-8 读、声明 `charset=gbk` 时不改写、默认值不是 latin 系时不改写。`Search.do` 那一路请求头自带 UTF-8 声明，走的是"不动"这一支，所以这条只保护正文页 |
| 政策页与专题页的正文标记 `enpcontent` 写成两份（桌面／移动），新闻页只有一份；取哪份、两份不一样怎么办原先没写 | `fetch_ctax` 取 `bodies[0]`：两份逐字节相同时 `_note` 写"两份相同，取第一份"，不同时 `_note` 换成"两份不一致、只取第一份、去 NOTE.md 复测第 6 项"；单份页面不带 `_note` | 缺标记时回 `_error` 加 `_raw_len`，不交空正文冒充"这篇没内容"；标记在而正文为空判成图片型正文（取样里 图解税收 栏目 1/1 如此）；错误地址这一站回 500 而不是 404，`fetch_ctax` 那句写明"读成地址不存在，不重试"。`TestCtaxBodyBlocks` 七条（相同／不一致／单份／元信息字段／图片型／缺标记／500）逐一可达 |
| 剥标签与段落换行两步先后一颠倒，整篇正文粘成一行且不报错 | `tax_ctax._to_text` 先用 `_PARA_RE`（块级结束标签与 `<br>`）换成换行，再用 `_TAG_RE` 剥剩下的标签，最后逐行去空白、压掉空行；顺序写在函数说明里 | `_TAG_RE` 把 `</p>` 一起剥掉之后 `_PARA_RE` 再也找不到边界。三条用例按段落数断言（`test_identical_twins_take_the_first_and_say_so`、`test_single_block_page_gets_no_note`、`test_old_row_keeps_its_place_with_the_reason` 都要求两个正文段落之间是换行），把这两行 `sub` 调换顺序三条一起报红 |
| 检索行里那两枚摘要（`enpcontent` 截断与编辑写的 `Abstract`）看着像正文，接进抽取等于拿摘要当条文 | `_row_from_json` 只把它们落成 `snippet`／`abstract` 两个展示字段，正文一律回正文页取；`test_snippet_and_abstract_stay_display_only` | 200 行取样里 `enpcontent` 195 行有值、只有 120 与 123 两档长度、194 行带收尾截断标记；`Abstract` 174 行有值、9 到 193 字（逐字段读数表见 `subskills/ctaxnews/NOTE.md`「检索行的字段与值域」）。一枚是正文截断、一枚是编辑摘要，两者都不是原文 |
| 栏目页的行若按容器类名找会漏会串：站点实际出现的容器有三枚（`div.newsList-item`、`ul.onlyTitleList`、`ul.ztNews-list-main`），且 `newsList-item` 写成 `newsList-item clearfix` 时按精确类名 14 页全读 0 行；侧栏还铺着别的栏目的推荐位 | `_page_rows` 按地址形状认行（`_ARTICLE_HREF_RE`：`/<目录段>/<yyyy-mm>/<dd>/content_<id>.html`），`_own_region` 先按 `layout-380`／`footer` 切掉侧栏与页脚，再用两个计数器分别记留在本栏段内的外来行数（`off_col`）与切不出本栏标题时不在本栏检索集里的行数（`off_set`）；`_row_title` 只在纯日期单独占一行时丢掉它，两行标题两行都留 | 归属判据是地址而不是类名，所以没有类名的页面照样取到行、目录段不是本栏的一律扣进 `off_col`；切不出标题的那一类页面（实测三例：按别的栏铺块的落地页 node_13、只铺一块相关文号列表的短页 node_8030、父栏目页 node_24）同目录兄弟栏长得一样，只能拿本栏最新一批 `articleid`（`_column_id_set`，`pageSize` 上限 5000、只发 `sort=date`，取到的是按发布日期倒序的最新 5000 行）交一次。同一篇的配图链接与标题链接同址，按地址并成一行、标题取非空那枚，并完仍无文字的行丢掉。`TestCtaxColumnPageRows` 八条覆盖这六道，夹具外壳用的类名 `tabList` 是合成的（站点 14 份栏目页副本里 0 处命中），故意挑一枚站点没发过的名字才验得出 `_page_rows` 不读类名；其中 `test_sidebar_rows_are_cut_before_scanning` 与 `test_off_directory_rows_inside_the_region_are_counted` 是一对——把侧栏那块摆进 `layout-380` 之前，前一者的 `off_col` 立刻从 0 变 1 |
| 子技能两份文档里写的符号名与探针请求数，改脚本时容易只改一侧 | `TestCtaxSubSkillDocHooks` 三条：`test_named_symbols_exist` 把 `subskills/ctaxnews/` 两份文档里出现的 `tax_ctax.<符号>` 逐个 `hasattr` 核对；`test_probe_item_costs_match_the_note_table` 从 `subskills/ctaxnews/probe_ctaxnews.py` 的语法树里读 `COST` 表，与 NOTE.md「探针项与请求数」小节逐行比对，断言两个合计（全项 209、去掉第 10 项后 107），再把这对合计在六处文字里逐处核对且各只许出现一次——NOTE 的复测入口那一行、表下面那一行与子技能 SKILL 命令行那两条注释按数字解析比对，NOTE 单轮耗时那一行（`--fast` 数与全项数各一条整串）、README 目录树那一行、这一行本身按整串比对；`test_the_route_is_wired_into_the_aggregator` 断言这一站在 `DEFAULT_SOURCES`、`SOURCE_RANK`、`SOURCE_LABELS`、`SOURCE_IMPACT`、`SOURCE_PLAIN`、`DATE_SERVER_SOURCES` 六处登记一处不缺（四张源表的键集合两两相同），日期已下推给服务端、没有再在窗口内补筛一遍，并要求 SKILL.md 里有一行同时写着中国税网与源键 `ctax` | 文档写了一个不存在的函数名、探针改了请求数而 NOTE/README/台账没跟着改、有人把这一站从默认名单里摘掉（六处登记漏一处）或把它的日期退回本地补筛，三种都会报红。2026-10-20 在隔离副本树跑十二处变异全打红（先取一份未变异副本的失败集当基线，只把新增失败算检出）：探针 `COST` 第 5 项改一档、NOTE 表第 5 行改一档、NOTE 复测入口那行的全项数、NOTE 表下面那行的 `--fast` 数、SKILL 全项复测那行、SKILL `--fast` 那行、README 那行的全项数、这一行的两个合计、NOTE 单轮耗时那行的 `--fast` 数、同一行的全项数、`_is_date_line` 把三段日期只认两段、父栏目提醒把「父栏目」改成「上级栏目」。打向 README、这一行与 NOTE 单轮耗时那一行的四处变异是这一轮才打红的：第一遍副本排除 `data/` 时另有一条无关用例先红，README 与这一行被它冒充成检出；改成基线差集后这两处露出漏检，说明原门禁只核对 NOTE 与 SKILL，补上整串复述核对才打红 |

## 限流记录的实测台账

限流与风控是站点随时间改的东西，而闸与退避是一次写下的代码。一条"某年并行被打挂过"
的记录如果没有再量的日期，读的人就分不清它是在说今天的站点还是说当时的站点——所以
**凡是作为建闸或退避理由的限流记录，都要在这里占一行，并带齐三个字段**：最近实测日期、
实测方法（连发几次 + 几线程 × 每线程几次）、当时结果（按形态逐项计数）。三字段缺任何
一格，这行就是没有证据；`tests/test_rate_limit_ledger.py` 逐格查，代码里新装一把闸而台账
里没有对应行也会报红。

复跑走 `python tests/probe_rate_limit.py --target npc|sogou|fgk-list
[--burst N] [--threads M] [--per-thread K]`。探针把生产路径实际要发的那一份请求抓出来
重放（不手写副本，副本会随 payload 改版失真），并跳过串行闸——它要量的就是并行。
单次运行的请求总量卡在 `MAX_REQUESTS = 60`：这是查询式访问的复测，不是压测。

| 记录原话 | 入口 | 现在由什么挡着 | 最近实测日期 | 实测方法 | 当时结果 |
|---|---|---|---|---|---|
| NPC 限流，并行必现；形态有三种——断连、HTTP 200 带 `<noscript>` 挑战页、5xx，都不回 429（`commands.md`「NPC 限流」） | `POST flk.npc.gov.cn/law-search/search/list`，详情与下载同站同闸 | `tax_search.npc_gate`（闸落成代码 2026-09-28）＋ `_MIN_INTERVAL` 0.6 秒 ＋ `max_retries` 4 按 2/4/8 秒退避 | 2026-10-04 | 三趟共 104 次：连发 15 + 8 线程×2、连发 15 + 8 线程×2、连发 10 + 16 线程×2，0 间隔、不经闸 | `200-JSON`×102、`ReadTimeout`×2（两趟各一次，都落在并发段，15 秒读上限，生产路径按退避重试）；`挑战页`×0、`429`×0、`5xx`×0、`断连`×0、`非JSON正文`×0——记录里那三种形态在 16 路并发下都没复现 |
| 搜狗并发加压触发反爬（跳 `/antispider/`；检索与链接还原须共用一个 Session，脱会话即判爬虫） | `GET weixin.sogou.com/weixin?type=2` | `tax_wechat.sogou_gate`（闸落成代码 2026-09-28）＋ `_SOGOU_MIN_INTERVAL` 1.0 秒 | 未复测 | `--target sogou` | 没有当次数据。这一行只说明闸还在、记录还没再量过，不等于"限流仍然存在"，也不等于"可以拆闸" |
| 网页搜索桌面历史：连续请求 302 到 `/antispider/`，跟随后 HTTP 200、约 5.4KB、无结果块，冷却窗口内重发仍跳同页 | 历史 `GET www.sogou.com/web?query=`（非当前入口） | 现有保护保留在 `tax_sogou._sogou_gate`：`SEARCH_MIN_GAP` 3 秒、`BUDGET_MAX` 4 次／`BUDGET_WINDOW` 10 分钟、`BLOCK_SECONDS` 10 分钟熔断；这是保护配置，不是站点保证 | 2026-10-23 | 1 线程连发 6 个检索词（0 间隔，前轮已触发反爬后的复测；探针未收录网页搜索） | `200-反爬页`×6（5422–5458 字节、`blocks=0 rows=0`）、`200-结果区`×0——只证成当时桌面冷却窗口内重发无效；首次触发所需请求数未量到，不是手机阈值 |
| 手机正常入口已观察过验证页；本轮只做本地 HTML 回放，不测联网阈值 | `GET m.sogou.com/web/searchList.jsp?keyword=`（`tax_sogou.SEARCH_URL`） | 同一串行闸、间隔、预算与熔断；冷却立即报错、不发请求，反爬不重试、不跟随；不以代理池或端点轮换绕过。整页结果元数据缓存 30 分钟，键不含 size，size 只截输出 | 未复测 | 本地 HTML 回放；`probe_rate_limit --target sogou` 只测微信入口，未收录手机网页搜索 | 没有当次数据。已观察过手机验证页，但没有新的测量日期、次数或阈值；桌面旧读数不得移作手机证据，保护配置不保证站点放行 |
| `mp.weixin.qq.com` 的正文读取**没测到**与搜狗同样的阈值，所以只按 `READ_INTERVAL` 2.0 秒拉开间隔、不入闸 | `GET mp.weixin.qq.com/s?...` | 只有间隔，没有闸 | 从未取过阈值 | 探针未收录这一路（要收就先在 `TARGETS` 里加一条，别在台账里写没量过的数字） | 没有当次数据。这条记录的内容本身就是"没验证过"，因此不存在过期问题；它是边界，不是闸的存废待定 |
| 总局清单接口连发不限流（`tax_gov_list` 模块文档那句"实测 20 次连发不限流"） | `POST www.chinatax.gov.cn/getFileListByCodeId` | 没有闸，靠整栏分页爬全时天然串行 | 2026-10-04 | 连发 30 次（0 间隔）+ 6 线程 × 每线程 3 次，共 48 次，耗时 2.2 秒 | `200-JSON`×48、`反爬字样`×0、`异常`×0——与记录一致，量级比原记录（20 次连发）高一档且加了并发 |

读这张表的三条用法：

- **闸的存废按这一张表判，不按主表那句记录**。要把某个入口放宽成并行，先复跑探针，
  让台账里那一行的最近实测日期晚于改动日期；日期是"未复测"的行不能作为拆闸的依据，
  也不能作为保留的理由。今日这轮把 NPC 的量级验到 16 路并发、共 104 次请求，
  记录里那三种形态一个都没出现——这削弱的是"并行必现"这四个字，没有削弱到闸本身：
  单次复测只说明这一天这个出口 IP 上没触发，更高量级与别的时段未探测（`MAX_REQUESTS`
  卡在那儿），闸的代价是排队等待，不是失败，所以本轮不动闸、只把记录改成可复查的。
- **真被限的那天，把形态原样抄进「当时结果」**。探针已经把失败形态归成标签并留一份
  原文（状态码、字节数、`Content-Type`、正文首 120 字），抄的是那一行，不是"又被限了"。
  主表与 `commands.md` 里那些形态描述（38,499 字节的挑战页、约 5KB 的访问异常页）都是
  这样留下的；形态变了就直接改这一格，别把新形态写进旧句子。
- **限流与代码坏了要分开**。等锁超过 `TAX_NPC_LOCK_TIMEOUT` / `TAX_SOGOU_LOCK_TIMEOUT`
  抛的 `TimeoutError` 是本机另一个进程在排队，探针不产这一种形态；`ReadTimeout` 也不是
  被拦，它计入"当时结果"但不改判记录的成立与否。判据在主表那句：形态对得上才算复现。

## 案例通道的实测台账

类案检索（`scripts/tax_cases.py`）整条链路都站在税务总局 search5 的现场行为上：
全站能不能召回案例、哪一维收得窄、日期排序可不可信、案例与文件怎么判别。这些都会
随对方改版而变，所以按限流台账同一把标尺登记：**每行带齐三字段**（最近实测日期、
实测方法、当时结果），缺任一格就是没有证据。复跑走
`python tests/probe_case_channel.py --dry-run`（只看要发哪几条）与
`python tests/probe_case_channel.py`（真发 20 条，回吐下面这些数字）。这一张表
由 `tests/test_cases.py::LedgerContract` 逐格查，规则与限流台账一致。

| 判据（谁在用） | 入口 | 最近实测日期 | 实测方法 | 当时结果 |
|---|---|---|---|---|
| 全站能召回案例，案例与新闻混排、没有独立"案例"栏目（`tax_cases.ADVERSE_WORDS` 那一轮取词） | `GET search5/search/s`，`label=''`、`column=''` | 2026-10-20 | 四组案例词各 1 次（探针第 1–4 发），逐组读 `searchTotal` 全 10 行 | total×命中词：`重大税收违法案件`×83、`税收违法 曝光`×104、`骗取出口退税`×189、`虚开发票 查处`×834；首屏判为案例（URL 带案例子栏目代号且不带文号）1、3、5、10 条；首屏带文号 4、0、0、0 条。与既有记录的差异：`税收违法 曝光` 2026-10-04 那轮 103、本轮 104；`虚开发票 查处` 更早记录 785、2026-10-04 那轮 828、本轮 834。四组的首屏判为案例条数两轮全同 |
| 栏目维收得窄（`tax_cases` 固定发 `column=5741`） | 同一接口，`column` 参数 | 2026-10-20 | 同一检索词发两次对照：不带 column 与带 `column=5741`（新闻发布）——探针第 5–8 发 | `骗取出口退税`×189→119（首屏判为案例 5→4 条）、`重大税收违法案件`×83→30（1→0 条）。收窄后的两个数与 2026-10-04 那轮一字不差。既有记录里"税收政策栏仅 7、互动 8"两栏**未复测**（那两个栏目号没取到，探针里没有它们那一条） |
| `label` 这一维收的是**站点真实标签名**（响应分面 `labelList` 那 159 桶里的名字），发不存在的名字回 0 而不回基线；但回 0 反不出"这枚名字不存在"——真实名配一个该标签下没条目的主题同样回 0。案例那一路因此发空 label（`tax_web_search.search_chinatax(file_only=False)`） | 同一接口，`label` 参数 | 2026-10-20 | 第一批三个非白名单名各 1 次（2026-10-04：`新闻`、`稽查`、`曝光台`）；第二批只改 `label` 五发（2026-10-20，逐发间隔 2.5 秒）：`新闻`／`新闻发布` 配「增值税」，`视频政策解读`／`图片政策解读`／`FILE_LABELS` 配「研发费用加计扣除」；第三批走本案列探针第 9–12 发，检索词固定「骗取出口退税」，`label` 依次 `新闻`／`稽查`／`曝光台`／`新闻发布` | 第一批 `total=0`×3、清单 0 条×3。第二批：`新闻`=0、**`新闻发布`=2（取回 2 行，行内 `label` 都是「新闻发布」）**、`视频政策解读`=46、`图片政策解读`=24、白名单=62。**旧记录那句"标签维对案例不可用、非十类文件名一律回 0"作废**：站点管那一类叫「新闻发布」，真实标签名收窄得出来，回 0 的是 `labelList` 里根本不存在的名字。同一枚「新闻发布」在 `columnList` 那一路是 12,924（全站基数），栏目与标签是两套数，所以案例走 `column` 而不走 `label` 的依据是覆盖，不是"标签只认那十枚"。第三批四发全回 HTTP 200、`total=0`、清单 0 条——含真实名「新闻发布」那一发，它与「骗取出口退税」配不上，这一格因此只能证成"这一主题在这枚标签下没有条目"，不能再用来判标签名是否存在。逐档读数与四条读法在 `subskills/chinatax/NOTE.md` 第三节 |
| 日期序可用来做增量与时效排序（`tax_cases` 的 `recent` 模式） | 同一接口，`orderBy=1` + `column=5741` | 2026-10-20 | 同一检索词翻 2 页（探针第 13–14 发），逐行读 `pubDate`，页内与跨页都判单调 | 每页 10 个日期，页内单调递减 True×2；合并 20 个日期单调递减 True，区间 `2026-09-28 … 2024-05-31`——两个边界与 2026-10-04 那轮同。单调判的是不递增，不是不重复：`2026-02-27` 一天连着占 5 行、横跨那两页的接缝 |
| 案例与文件的判别只能用主机名，不能看路径（`tax_cases.classify_row` 的排除支） | 同上，四组检索词共读 125 行结果 | 2026-10-04 | 逐行取 URL 的栏目代号（6 位）与正文 id（7 位），跨请求计数 | 带文号的行 9 条，全部落在 `fgk.chinatax.gov.cn`；但 `/zcfgk/` **不区分文件与案例**——案例条目 `c5247913`（`c102439` 曝光栏）、`c5248283`（`c103098` 合规小课堂）都在 `www.chinatax.gov.cn/zcfgk/` 下。既有记录那句"案例条目是 `/chinatax/n810…/`、法规库含 `/zcfgk/`"作为判据不成立，改用 `tax_web_search.FGK_MARKER` 判主机。2026-10-20 那一轮（探针 20 发）复现了同一批 125 行与同样 9 条带文号，但探针只打印行数、带文号行数与跨主机重复组，不打印逐行主机与路径，所以"全部落在 `fgk`"与 `/zcfgk/` 那两句仍以 2026-10-04 的逐行统计为准 |
| 同一条正文会在两台主机各回一遍，台账要先去重（去重键 `tax_cases.content_id`） | 同上，同一批 125 行 | 2026-10-20 | 按 URL 末段的 7 位正文 id 聚合，看它挂过几台主机（探针汇总，跨 20 发的 14 组检索） | 双主机重复 3 组：`c5247913`、`c5248283`、`c5245915`，每组都是 `fgk` 与 `www` 各一行、标题与日期相同。这三组与 2026-10-04 那轮同 id，本轮没有新增。不去重，同一起案件会占掉两个候选位 |
| 三个案例子栏目代号各自是什么（`tax_cases.SUBTYPES`） | 同上，同一批 125 行 | 2026-10-20 | 逐行取 URL 里 6 位栏目代号，跨请求计数（探针汇总打印「未出现的」清单） | `c102025`×46（各地查处/曝光通报）、`c102439`×11（典型案件曝光）、`c102435`×2，三个代号本轮全部出现、未出现清单为空；三个数与 2026-10-04 那轮一字不差。`c102435` 那 2 行是「重大税收违法案件信息公布（2014年10月30日）」这类**逐月汇总公布页**，正文摘要只有一句"链接：重大税收违法案件信息公布栏"，不是逐案清单页——这句是按行读正文得出的，探针只回代号计数，所以内容描述仍以 2026-10-04 那轮为准。它召回得少，不能当成主通道 |
| 案例栏目没有直连清单页，只能走 search5（`tax_gov_list` 那套栏目直连在这里用不上） | `GET .../n810215/c102435/common_list.html` 与 `.shtml` | 2026-10-20 | 两种后缀各 1 次（探针第 15–16 发） | HTTP 404×2，响应体各 144,122 字节，起头是 `<!doctype html> <html lang="en">`，即首页 HTML 而不是清单页。既有记录写的是 400，2026-10-04 那轮量为 143,971 字节的 404，本轮量为 144,122 字节的 404——首页 HTML 长大了 151 字节，判定没变 |
| 检索词形态决定这一趟是不是案例（`tax_cases` 的三轮式取词不用"案例/公布"字样） | 同一接口，四组对照 | 2026-10-20 | 两组"案情词 + 案例/公布"、两组"案情词 + 处理结果词"（探针第 17–20 发），各读全 10 行 | 「骗取出口退税 案例」total×1、「重大税收违法案件 公布」total×4，两组首屏判为案例 0 条；「虚开发票 依法查处 罚款」total×511 首屏判为案例 10 条、「偷税 案件」加 `column=5741` total×292 首屏判为案例 6 条。往检索词里加"案例"两个字反而把案例筛掉。与 2026-10-04 那轮比：前两组同样 1、4 条，后两组 508→511、292→292，判为案例的 10、6 条没变 |
| 发布方栏只有一半的行带，来源等级因此必须两处一起读（`tax_cases.issuer_of`、`tax_cases.source_grade`） | 同一接口，`column=5741` 两主题共 50 行，原样落盘在 `tests/_fixture/case_channel_rows.json` | 2026-10-04 | 逐行数 `publisher` 非空的行；空的那一半取标题冒号前那一段，看有没有机关字形 | 50 行里 `publisher` 非空 25 行。非空的 25 行中媒体署名 10 行（新华社×3、经济日报×2，法制日报、南宁日报、中国新闻社、中国税务报、税务总局新媒体各 1），机关署名 15 行（国家税务总局办公厅×10、国家税务总局×4、广西壮族自治区地方税务局×1）。`publisher` 为空的 25 行里，16 行靠标题段读出机关（"河南省税务部门查处……"这一类，带行政区划的 15 行判 A2、"全国税务部门组织税收收入情况"1 行判 A1），余下 9 行整段读不出发布方——所以这一档不给默认值：读不出机关又不在官方案例栏目的落 C，在这三个栏目里的按栏目名义算本级。与既有记录的差异：旧判据把这类行一律记成 A2「各地税务机关通报」，本轮量为不实 |
| 窄主题下三个案例栏目可能一条不给，候选全落在「其他新闻」（`tax_cases.OTHER_SUBTYPE`、`tax_cases.LEVEL_BY_SUBTYPE`） | 同一接口，走脚本本体：`python scripts/tax_cases.py "研发费用加计扣除" --element 混岗工时 --element 辅助账 --mode recent`，共 3 次请求 | 2026-10-05 | 读台账的「子类型」栏分布 | 候选 25 条，子类型全是「其他新闻」（C4），c102025/c102439/c102435 三个案例栏目 0 条；其中媒体署名 2 条判 C。这不等于"官方没查过研发领域的同类案件"——它说的是这一档检索式在案例栏目里没召回，答案那一栏要写成"这一层还没查到"并附检索式，不能写成"没有先例"。换主题（虚开发票、骗取出口退税）时同一条链路能取到案例栏目条目，见上面 2026-10-04 那几行 |

读这张表的三条用法：

- **这一趟查的是通道行为，不是某一起案件**。要改的是 `tax_cases` 走哪一维、按什么
  判案例，不是"某年某月曝光过什么"。哪一天 `column=5741` 收不动了、或 `label=新闻`
  开始回条数，重跑探针就看得见，不必照旧句子猜。
- **判据换了就要同时改代码**。第五行那条（用主机名判文件/案例）与第六行（先按正文 id
  去重）都是 `tax_cases` 里真在跑的规则，台账与代码哪天对上不上，
  `tests/test_cases.py` 会报红；只改文档不改代码，改的就是一个没人执行的句子。
- **`case_support: "无"` 是这一通道正常输出的一种**。第 9 行说明检索词形态能一句话
  把命中从 511 打到 1，所以零结果既可能是"官方没公布过这类案"，也可能是"问法不对"，
  两种都不写成"没有类案"，写法见 `references/output_templates.md` 的「类案支持」块。

## 两个值得单独说明的细节

- **税屋的空结果归因看它自己回报的 `_channels`，不再靠推断**。`tax_shui5.search_shui5`
  有两路：搜狗的 `site:shui5.cn`（`channel="sogou"`，`tax_sogou.sogou_search`）与站内原生检索
  （`channel="native"`，`POST /e/search/index.php`），默认 `auto` 先走搜狗，它被反爬或 0 条才换站内。
  两条通道各按自己的排序取数，2026-10-23 同一关键词「研发费用加计扣除」站内首屏 30 篇、
  搜狗首屏 8 篇，按文章 ID 归一后**交集 2 篇**——所以"这一路 0 条"读不成"另一路也 0 条"。
  `tax_aggregator._build_gaps` 按回报的 `站内` 状态分两种写法：这一轮站内那一路没走通
  （`没走成`/`未走`）时，"搜狗被反爬而税屋 0 条"才是被搜狗连带的，标 `blocked_by: sogou`；
  那一路走通了却还是 0 条，就不标 `blocked_by`，缺口写成"两条通道各按自己的排序取数，
  这一轮各 0 条只说明没取到，不代表税屋没有这个主题的解读"。盯它的是
  `tests/test_source_defects.py` 的 `TestAggregatorShui5Attribution`。
  站内那一路自己回报的 0 条也带着归因：`search_native` 撞到「没有搜索到相关的内容」时，
  `_status` 写明这一页两种成因分不开（站内确实没有 / 这一档参数站点不接受），上层不翻文档
  也不会把参数问题读成"这一站没有内容"。
  搜狗手机端日期取自 `citeurl` 引用行（旧桌面为 `citeLinkClass`），不是每条都有；母技能 ④ 按取回条目补筛
  （`DATE_LOCAL_SOURCES`），缺日期的行只在结果窗口内参与。
  站点机制与实测数字在 `subskills/shui5/NOTE.md`。
- **`low` 现在是一道闸门，不是一条现状**。仓库里真正会打标记的只有三处
  （`tax_search` 全文检索、`tax_fgk` 深页、`tax_aggregator` 源级传播），三处都只出
  `medium`。`low` 这一档当前没有源在打，它是为"将来某个源真的跑偏"和"外部把
  带标结果喂进来"预留的判定档位。所以**结果里出现 `low` 本身就是个信号**。

## 量过之后决定不改的三件事

改与不改都按同一把标尺量过，记在这里是为了让下一轮不必重新提议：

- **垃圾词过滤不加"拦掉几条"的计数行**。`tax_server._is_garbage_result` 与
  `_BLOCKED_TITLE_WORDS` 在四处过滤点各跑一遍，实测下来内容相关性那道筛子拦掉 0 条
  真结果。界面常驻一行"已拦 N 条"，绝大多数时候 N 是 0，读起来像"系统什么都没拦"，
  反而不如现在这样只在有缺口时说话（`degraded_note`）。
- **不给"题面点名了文件"这个形态加判型分**。`confidence=0.30` 那一档只剩 18 题，其中
  带书名号点名的只有 1 题；而 0.30 只被打印出来，没有任何一条代码路径按它改行为。
  点名文件需要的处理在检索与分层那一层，已由 `tax_terms.title_identity` 与 `cited_note` 接管。
- **「解读」不进判型词表**。评测集 1001 题只有 1 题含这两个字，它不量任何一类题的内容；
  两个真实提问（"请解读《X》公告""请解读修订草案"）判出的 `lookup` 本来就对，缺的不是
  判型而是草案覆盖边界。

## 主线动作归属

SKILL.md ② 末尾指向这一节。它回答两个问题：某一格动作**现在是脚本在做、还是 Agent
在做**；做错了**在本机怎么发现**。用途是改代码前先定位——把本该 Agent 判断的那一格写
进脚本，等于把词表外的表达永久判死（如 `detect_context_gaps` 只认得词表里的措辞）；
反过来把已经由代码保证的那一格重新交给人记，等于没有约束（本文件前半部分整篇都是
这类事故）。表中代码名与测试名均可 grep，只列已核实为真的。

| 主线动作 | 归属 | 代码落点 | 做错了在本机怎么发现 |
|---|---|---|---|
| ① 判型 | 脚本 | `tax_analyze.classify` | `python tests/probe_classify.py --stats --real --oracle`（离线） |
| ① 把握 0.30 时重判题面 | Agent | 仅 `confidence` 被打印，无代码路径按它改行为 | `python scripts/tax_analyze.py "<原话>"` 读把握值 |
| ② 决定追问哪几根轴 | 脚本给候选，Agent 取舍 | `tax_analyze.detect_context_gaps` / `pick_probes` | `python scripts/tax_analyze.py "<原话>" --probes 3` |
| ② 题面自相矛盾时指出冲突 | **Agent，脚本不判** | 无（`tax_analyze` 全文不含冲突判定） | 只能人工读题面，见 ⑦ 第 23 条 |
| ② 规则陈述类不追问个案 | 脚本 | `tax_analyze.RULE_STATEMENT_TYPES` / `RULE_AXES`，产出 `rule_note` | 命令行【适用边界】那一栏有没有这句 |
| ②⑧ 这类题必须有什么、现在缺哪一项 | 脚本判状态，Agent 定夺要不要接着答 | `tax_coverage.assess` / `load`，注册表 `data/evidence_requirements.json` | `python scripts/tax_coverage.py liability --question "<原话>"`；离线用例 `tests/test_evidence_coverage.py` |
| ⑥ 前 站在检查人员一侧盘缺口 | 脚本出问询单元与三类缺口，Agent 决定问哪几格、怎么把「现有回答」对上 | `tax_inspect.build` / `select` / `signals` / `scenarios` / `covered_by` / `check_pairs` / `caliber_gap`，注册表 `data/inspection_domains.json`；红线在 `tax_inspect.validate` 载入时扫 `FORBIDDEN_REMEDIATION` 与 `PREDICTION_PHRASES` | `python scripts/tax_inspect.py --list`；`--answers` 里名字对不上的数进「核对未用」而不是消失；未询问要点逐条列在【覆盖】里；离线用例 `tests/test_inspect.py` |
| ⑥ 前 把检查缺口转成制度条文 | 脚本出九要素文本与缺口匹配，Agent 决定摆进答案哪一段 | `tax_control.build` / `intake` / `matches` / `pick` / `validate`，注册表 `data/control_activities.json`；门槛在 `tax_control.NeedsDiagnosis`，红线词表沿用 `tax_inspect.FORBIDDEN_REMEDIATION` 与 `PREDICTION_PHRASES` | `python scripts/tax_control.py --list`；不给诊断也不给假设时退码 2 并回吐取诊断的那两条命令；`--assume` 出来的形态是通用模板；离线用例 `tests/test_control.py` |
| ⑥ 前 拿结构化账套行比账上的数 | 脚本逐条跑判据、出八要素条目与三类判不动，Agent 决定跑哪几条规则、哪一条摆进「风险自检专用输出」 | `tax_ledger.scan` / `run_rule` / `pending` / `render` / `validate` / `_steps` / `_compare` / `_row_mark`（账套没给行号那一列时按提交顺序顶上序），禁数字的格子由 `tax_ledger.DIGIT_FREE_CELLS` 声明并与注册表逐字比对，注册表 `data/ledger_rules.json`；没给账套在 `tax_ledger.NeedsLedger`，四态沿用 `tax_coverage.STATES`，缺口与红线词表沿用 `tax_inspect.GAP_MISSING`、`tax_inspect.GAP_CALIBER`、`tax_inspect.FORBIDDEN_REMEDIATION`、`tax_inspect.PREDICTION_PHRASES`——前一份只扫『建议』那一格（词表里的『回填』指往历史账上补数，而本层的『参数回填』指把 ③ 检回的现行值填进本次参数表，按整段输出扫等于把自己的说法判死），后一份扫『查什么』与『建议』 | `python scripts/tax_ledger.py --list`；`--pending` 出那份取数工作清单；参数没给来源时整条规则落『待核』而不是拿旧值算；逐行判时未命中行与判不动行分两格计；离线用例 `tests/test_ledger.py` |
| ② 前 收齐一次交来的几份合同并按维度对账 | 脚本逐单元比条款与履行、出十一格差异条目与五种成因，Agent 决定确认合同链上的哪几条关系（`--confirm`）、把哪一条差异摆进「风险自检专用输出」 | `tax_intake.intake` / `reconcile` / `build_chain` / `superseded_cells` / `_compare_element` / `_verdict` / `time_points` / `historical_hits` / `framework_sections` / `validate`，落点值域在 `tax_intake.LANDINGS`，成因到结论与缺口类别的两张表在 `tax_intake.CAUSE_VERDICT`／`CAUSE_GAP`，禁数字的格子由 `tax_intake.DIGIT_FREE_CELLS` 声明并与注册表逐字比对，注册表 `data/contract_intake_template.json`；一条合同都没给在 `tax_intake.NeedsContracts`，四态沿用 `tax_coverage.STATES`，核对四值／三类缺口与动作／两条红线词表沿用 `tax_inspect`，② 的追问句沿用 `tax_analyze.CONTEXT_AXES`，历史称谓不得撞的现行税种名沿用 `tax_search.TAX_TYPE_KEYWORDS`，每维的『风险指标落点』必须指到 `references/tax_risk_framework.md` 里真实存在的风险指标小节 | `python scripts/tax_intake.py --list`；`--blank` 出那张空白填表；没 `--confirm` 的替代边不作废任何条款，确认后也只作废后一份合同也带着的那几格；抽取的值缺出处那一格整条落『待核』而不进比对；履行侧没记录进「无法确认条目」并落成证据缺口，可喂 `python scripts/tax_inspect.py --answers`；离线用例 `tests/test_intake.py` |
| ⑥ 前 liability 出数 | 脚本 | `tax_calc.run` / `inputs` / `SKELETONS`（骨架零硬编码，值与档表由 ③ 检回后经 `--set`/`--src` 喂进） | `python scripts/tax_calc.py --list`；缺参数退码 2 并列整份缺口；离线用例 `tests/test_calc.py` |
| ③④ 决定取数轮次与候选词 | 脚本给计划，Agent 放宽 | `tax_answer.build_plan`，回显在 `rounds_done` | `python scripts/tax_answer.py "<原话>" --plan` |
| ③ L2 要"整栏横截面"（这一栏现行文件都有哪些） | 脚本 | `tax_gov_list.sync` / `lookup` / `stats`，栏目与栏目页两张表 `CHANNELS`、`CHANNEL_PAGES` | `python scripts/tax_gov_list.py stats` 读栏目/条目数/时效性分布/栏目页；离线用例 `tests/test_gov_list.py` |
| ③ L4 报刊登载层（中国税网）检索、一稿多发合并与逐篇取正文 | 脚本 | `tax_ctax.search_ctax` / `dedup_rows` / `fetch_ctax` / `read_articles` / `column_lookup` / `list_column`，栏目表 `data/ctax_columns.json`；复测探针 `subskills/ctaxnews/probe_ctaxnews.py`（发真请求，逐项请求数记在 NOTE.md） | `python scripts/tax_ctax.py "增值税" --size 3` 读 `_status` 里的"合并同一篇挂多栏的行 N 行"与 `_pages`；离线用例 `tests/test_source_defects.py` 里 `TestCtax` 打头的十组（组名 grep 得到），全程打桩会话不发一次真请求 |
| ④ 六源聚合与取数失败留名 | 脚本 | `tax_answer.gather`，产出 `rounds_done[].failed` | `tests/test_source_defects.py::TestRoundFailureIsNotSilent` |
| ⑧ 实务口径援引的文号回官方库核对 | 脚本 | `tax_answer.check_practice_citations`（在 `gather` 里排在 `grade_all` 之前），产出 `plan["citation_check"]` 与每条的 `official_status` | `tests/test_source_defects.py::TestPracticeCitationCheck`、`TestCitationCheckReachesTheAnswer`；命令行看【执行口径与实务认定】末尾那句核对统计 |
| ⑧ 层级、时效、主题对应三轴定级 | 脚本 | `tax_evidence.LEGAL_RANK` / `rank_of` / `judge_validity` / `on_topic_of` / `grade` | `tests/test_judge_validity.py` |
| ⑧ 角色分层与主依据挑选 | 脚本 | `tax_evidence.role_of` / `pick_primary` / `_order_key`，理由写进 `_why` | `test_legislation_lane_items_cannot_become_primary`、`TestConstitutionIsNotTheHeadline` |
| ⑧ 没取到法定依据时收口转「依据不足时」 | 脚本提示，Agent 换形态 | `compose` 的 `statutory` 为空时产出 `evidence_gap`，`_print_answer` 打"⚠ …"那一行 | 命令行读这一行是否出现 |
| ⑥ 组织最终答案 | Agent | 骨架在 `references/output_templates.md` | 对照模板逐段，缺段即不合格 |
| ⑥ 命令行排版 | 脚本 | `tax_answer._print_answer`；单源卡片走 `tax_formatter` | 网页那条另见 ⑫ |
| 优惠叠加与择一核验 | Agent | 无——原文措辞判不了；要求写在 ⑦ 第 22 条 | 答案里【优惠交互与限制】段在不在 |
| 优惠全集穷举 | 脚本 | `subskills/tax-preference/preference.py` 的 `sync` / `query` | `tests/test_preference.py` |
| 闸外的事：复测一条限流记录 | 脚本给证据，人决定闸的存废 | `tests/probe_rate_limit.py`，回显就是台账那三字段 | `python tests/probe_rate_limit.py --target npc --dry-run` 看要发的请求；`tests/test_rate_limit_ledger.py` 钉台账三字段齐全、且代码里每把闸在台账占一行 |

## 仍然是边界的几件事

- **闸管串行，不管快慢，也不管等多久**。等锁超过 `TAX_NPC_LOCK_TIMEOUT` /
  `TAX_SOGOU_LOCK_TIMEOUT`（默认各 180 秒）会抛 `TimeoutError`，报错里直说
  "另有进程正在打 NPC / 搜狗"。遇到它先找自己另一个会话——那是在排队的提示，
  不是代码坏了。
- **串行闸各有自己的入口与证据**：`tax_search.npc_gate` 保护 NPC，
  `tax_wechat.sogou_gate` 保护微信检索，`tax_sogou._sogou_gate` 保护手机网页搜索。
  `mp.weixin.qq.com` 正文只按 `READ_INTERVAL` 拉开间隔，不入闸。
  证据见[限流记录的实测台账](#限流记录的实测台账)：NPC 最近为 2026-10-04，
  微信与手机网页阈值未复测，桌面 2026-10-23 数据只是历史；保护保留，不保证站点放行。
- **缺口说明是"这次没取到"，不是"该层没有内容"**。程序只把这句话递过去，
  不替你重试、也不替你换源补缺；用什么口径回答仍然由上层决定。
- **文号检索不出来有两种，本层只收掉了一种**。`_citation_phrase` 补前缀只在文号
  左侧紧挨着机关名与文件类型时成立（"国家税务总局公告2021年第5号"）。文章写成
  "《财政部税务总局关于……的公告》(2022年第15号" 时，文号左边是括号，补不出前缀，
  只能拿裸短形去查——实测这样一趟给 2 条命中，取回的清单里没有一份文号对得上，
  于是报 `not_in_library`，并把"用的哪个检索词、接口给了几条命中"一起写进提醒。
  这一格要真正收掉得再按文件名对一遍，本层还没做。
- **法院判决与复议决定这一层没接进来**。类案检索（`scripts/tax_cases.py`）只走
  税务总局站内的查处通报与曝光典型，`中国裁判文书网`（`wenshu.court.gov.cn`）与
  `人民法院案例库`（`rmfyalk.court.gov.cn`）都未实现：前者要登录与验证码且近年
  公开量骤减，后者整个库在登录墙后。**这两个入口本机一次请求都没发过**，所以这里
  不写它们的反爬形态与阈值——没有量过就没有记录。后果写进输出模板：类案支持的
  强度上限是"官方处理口径"（税务机关怎么查、怎么罚、怎么公布），不是"司法裁判口径"
  （法院怎么判）；要的是后者时这一层是空的，得说明缺口，不能拿查处通报冒充判决。
- **稽查模拟的覆盖面就是 `data/inspection_domains.json` 里那几域，域外的事项等于没问**。
  这张表现在装的是研发与高企那条线（人员与工时归集、账证与辅助账一致性、项目实质与
  立项管理、领料与耗用、设备无形资产与场地、委托研发与关联交易），加上跨税种共用的
  申报、优惠资格与发票那一域。土地增值税清算、资源税、环境保护税、契税与房产税、
  跨境数字服务的预提所得税这些事项一格都没写。所以 `tax_inspect.build` 报的
  `complete` 只在这张表范围内成立：表外的事项既不会进【未询问】，也不会让命令行报错，
  读的人别把它当成"全税种都问过了"。要加一格得连 `一致性核对` 的两端与 `支撑材料`
  一起补齐——只写一句问询会被 `tax_inspect.validate` 当场拒掉，缺哪一格它就报哪一格。
- **内控生成的覆盖面就是 `data/control_activities.json` 里那十一环，两处判据管得住表、
  管不住企业**。正向门槛在输入侧（`tax_control.NeedsDiagnosis`：没有缺口也没有假设就
  拒生成），反向门槛在载入时判（每个检查要点的三类缺口都要有对着它的活动，少一格
  `tax_control.validate` 报错并逐格列出）。这两条管的是"缺口都有处方"，管不到"企业
  正在按这条制度做"——脚本读不到企业的执行记录，输出的每一条都要企业对着自己的岗位
  与流程确认一遍才谈得上落地。两类内容由别的层负责，不在本表：政策口径不清的缺口，
  本表只给"找到现行口径并落到那一年"的流程，口径本身回 ⑧ 定级与 ⑨ 类案取；保存期限
  那一格写的是管理下限，法定年限要按 ⑧ 挑出的文件另行核对，`tax_control.RETENTION_RE`
  只保证那一格真写出了一个期限，不保证那个期限是对的年限。
- **账套巡检的覆盖面就是 `data/ledger_rules.json` 里那六条规则，判的是数不是业务实质**。
  表表现在装企业所得税两条（业务招待费双维限额、研发加计测算与申报数一致性）与增值税
  四条（税率档、申报期限、简易计税抵扣进项、加计抵减），四层检查里落地的只有 `row` 与
  `cross_row` 两层，`calc` 与 `global` 两层引擎实现了而表里一条都没写——`--list` 会把
  这两层连同零一起打出来。土地增值税清算、资源税、环境保护税、契税与房产税这些事项的
  账套判据一格都没写，所以「命中 0 条」只说这六条没检出，不说这一本账经得起核。
  另一种边界在取值侧：规则比的那两个数一律来自 ③ 检回并带来源的参数，检不回、没给
  来源、或检回的表里没有被判定那一行所属那一档时，规则整条或该行判不动。本层不联网、
  不推算节假日顺延、也不拿邻近档的税率顶上来——顶上来就是把 ③ 的缺口写成账上的结论。
- **多合同受理的覆盖面就是 `data/contract_intake_template.json` 那九维度十八要素，判的是
  「合同写的那一格」与「实际做的那一格」对不对得上，不判该缴多少、不下风险等级、也不预测
  检查结果**。该缴多少走 ⑥ 与 `tax_calc`，账上的两个数对不上走 `tax_ledger`，风险等级落在
  「风险自检专用输出」那一式里判。这一层的边界有三处：**要素没写进表就比不动**——表外那些
  约定（交付节点的技术标准、竞业与保密的税务后果）本层一格都不判，所以「差异条目 0 条」只
  说这十八要素没检出矛盾，不说这几份合同经得起核；**合同链只做到候选**——边的状态默认
  『待用户确认』，未确认的替代关系不作废任何条款，两侧的值都摆出来并给『先确认这一条边』的
  动作，确认后也只作废后一份合同也带着的那几格，没有整份失效这种判定；**出处定不到位就不
  比**——抽取来的值缺 文件／位置／摘录 任一格，整条落『待核』、不进比对，因为拿一段对不上
  原文的转述去比履行，等于让本层自己造一份条款再拿去核对。填表来的值不要求出处，但每一处
  引用它的输出都带「［填表，未经抽取核对］」，读者看得见这一格还没跟原件核过。
- **法规库那一轮"没命中"仍会被 ④ 写成"取数失败"**。`tax_fgk.search_fgk` 零命中时
  写一句 `_error` 说明（翻完几页、共几条命中、范围限于文件类标签），而检索层的
  `_rows_and_error` 认的是"`_error` 非空即失败"，`gather` 于是把这一轮记进
  `errors` 与 `rounds_done[].failed`。文号核对那一路（`_doc_number_in_library`）
  已经改成认 `_fetch_failed`／`_empty_reason` 标记，检索层这一路还没跟着改：其余五个
  源不产这两个标记，把同一规则套上去会把它们真正的故障也读成"没命中"，比现在的
  假"取数失败"更难发现。收掉它要先给六个源统一标记契约，**这一步尚未实现**，读到这一条时
  按"未收口"对待。
- **废止日期这一栏接口给不出，别指望它替正文说话**。`xxgk_abolishDate` 按
  `xxgkAging` 取「全文废止」「已修改」「全文有效」三路各 10 条（2026-10-04 实测，
  `orderBy=5` 成文日期倒序，检索词「增值税」），30 条里这一栏的取值只有两种：空串与
  字符串 `"null"`——后者与 `xxgk_aging` 没填时同一个写法，经 `tax_web_search.aging_of`
  那套空值归一（`_AGING_BLANKS`）后仍是空。**一个真实日期都没给出过**，判"哪一天被废止"
  只能读正文或走现行/废止版配对（`tax_fgk.fetch_associations`）。这一栏因此不接进输出：
  接口没给过一个真日期，接进去的永远是空值；也不要因为条目里没有
  废止日期就写成"该库漏录了废止时间"——按现状这一栏根本没有录入。
- **会计口径缺口靠词表认，认不到就没有第二道防线**。`tax_analyze.ACCOUNTING_SIGNALS`
  只收多字短语，词表外的说法不会报出来；子技能也不在 ④ 的聚合与 ⑧ 的定级里，
  `docNo` 与条文版次不一致这件事程序判不了，只能按硬规矩引"准则名＋条款号"。
- **总局检索的效力等级与时效两维不能同时发**。检索词「增值税」下，
  `xxgkEffectLevel=财税文件` 单发 636 条、`xxgkAging=全文有效` 单发 252 条，两个一起发
  是 0 条，把时效换成"已修改"同样是 0；而同一根时效轴配 `xxgkSonTaxPolicy=增值税`
  正常给 159 条，说明不是 AND 语义坏了。0 的成因在分面里直接读得出来：加上
  `xxgkEffectLevel=财税文件` 之后 `agingList` 只剩空串 468 条与字符串 `"null"` 192 条
  两桶——「财税文件」这一栏根本不录时效，与本文件前半部分
  `tax_answer.corroborate_validity_from_target` 那条是同一件事的两侧：那条管取回之后
  怎么把状态补上，这条管发出去之前别把两维拼一起。程序拦不住谁去拼这条必然为零的
  查询，而 0 条与"库里确实没有"在结果里长得一模一样。参数与分面的实测数字见
  `subskills/chinatax/NOTE.md`「二、search5 的可发参数与分面字段」。
- **中国税网这一路的定级不认站点身份，只认标题形态**。`tax_evidence.PRACTICE_SOURCES`
  里没有这一站，`rank_of` 先按标题形态定档再按来源名兜底，所以一份报纸转载的现行公告
  按**原文件**定档：实测「财政部 税务总局公告2026年第29号」这一条标题配上本站来源名
  回 `normative`（`by` 写的是标题形态），`role_of` 给 `direct`——
  这一行会作为法定依据进答案，而它是不是全文、有没有节选，代码判不了，只能引文回原站
  核对。标题以「解读」收尾的那一类才落到 `technical`（在 `PRACTICE_RANKS` 内），角色
  `practice`；标题形态也认不出的落 `unknown`／`unmatched`。这一格现在只登记不改判据：
  唯一现成的把手是往 `PRACTICE_SOURCES` 加这一站，加之后本站所有政策转载一并进实务层，
  把"全文转载的现行公告"与"媒体解读"混成同一档，比现在的读数更坏。
  `TestCtaxGradingDoesNotGuardThisRoute` 三条钉住现状（这一站不在名单里、转载按原文件
  定档、只有「解读」标题到实务档），谁加进去第一条就报红。另一侧由
  `TestCtaxSubSkillDocHooks::test_the_route_is_wired_into_the_aggregator` 钉住这条接线：
  这一路进的是默认名单，不用调用方点名：
  `tax_aggregator.DEFAULT_SOURCES`、`SOURCE_RANK`、`SOURCE_LABELS`、`SOURCE_IMPACT`、`SOURCE_PLAIN` 与
  `DATE_SERVER_SOURCES` 六处都登记了它，日期按发布日期下推、不再落回本地补筛；单站取数仍走 `scripts/tax_ctax.py`。
