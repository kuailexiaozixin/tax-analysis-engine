# 源缺陷：已由代码保证的，与仍是边界的

> 本文件是 `SKILL.md` ⑩ 的下沉版。凡是修完的源缺陷，都从"要人记着的注意事项"
> 落成代码里的一个判定——不必再靠人记，程序自己会拦、会标、会把话说清楚。这里逐条
> 登记"由哪段代码保证、保证到什么程度"，最后列出**真正剩下的边界**（程序做不到、
> 也不该假装做到的）与**量过之后决定不改的**。用法见 SKILL.md 各工作流小节。

## 目录

- [由代码保证的源缺陷](#由代码保证的源缺陷)
- [两个值得单独说明的细节](#两个值得单独说明的细节)
- [量过之后决定不改的三件事](#量过之后决定不改的三件事)
- [主线动作归属](#主线动作归属)
- [仍然是边界的几件事](#仍然是边界的几件事)

---

## 由代码保证的源缺陷

| 源缺陷 | 现在由谁保证 | 保证到什么程度 |
|--------|--------------|----------------|
| NPC 限流，并行必现 | `tax_search.npc_gate`（实现为 `tax_http.SerialGate`）：`%TEMP%\tax-analysis-engine-npc.lock` 跨进程文件锁 + 进程内 `threading.Lock` | 多个进程同时开跑会自己排队，不用再记"别并行" |
| 360 被封 → 地方口径与税屋这一层是空的 | `tax_aggregator` 返回的 `gaps` 与 `degraded_note` | 缺了哪层、为什么缺、能不能拿别的源顶，都写成一句可直接照抄的话；网页端渲染成警示条，不再冒充普通的"0 条" |
| fgk 翻得越深相关性越差 | `tax_fgk` 第 2 页起标 `_reliability: medium`，并写上 `FGK_DEEP_NOTE`（`FGK_SHALLOW_PAGES = 1`） | 深页条目留在结果里参与分层，提醒就是那句写明"取自第几页、引用前要回上一级数据库核对上位法"的原文，不降权也不剔除 |
| 公众号并发加压触发反爬 | `tax_wechat._sogou_get`：1 秒最小间隔 + 搜狗专用串行闸（`%TEMP%\tax-analysis-engine-sogou.lock`） | 打 `weixin.sogou.com` 的请求（检索、链接还原）统一进闸；离线并发用例断言同一时刻在跑的请求数峰值为 1 |
| `_reliability: medium` 只能定位 | `tax_evidence.grade` / `_caveats` 解析该标记；`tax_aggregator` 把整源标记连同说明逐条带进聚合结果；`tax_formatter` 的两条渲染路径都会印出来 | 转成一条具体提醒，句子照抄条目自带的 `_reliability_note`，不参与 `pick_primary` 的排队；原先单源渲染路径会把这句吞掉，现在单源与多源归并都会先印警告 |
| 同一个档位在不同来源指不同的事 | 原因由来源自己写（`tax_fgk.FGK_DEEP_NOTE`、`tax_search.RELIABILITY_NOTES`、`tax_answer._legis_round`），定级层、命令行摘要与界面卡面一律照抄，不按档位配固定文案 | `medium` 的三个出处（NPC 全文分词命中、法规库深页排序变松、立法过程件不在收录范围）各得各的提醒；按档位配一句就有一句是假的——全文检索与站内检索都没有"页位"这件事。`test_medium_reminder_is_the_sources_own_sentence`、`test_cli_prints_the_reminder_text_not_a_level_ban`、`test_frontend_prints_the_reminder_not_a_downweight_badge` 钉住 |
| `_reliability: low` 本仓库已无来源产出 | 标记值仍在 `_reliability_of` 的值域里，`tax_search` 那句"不得作为依据引用"随降权模型一起撤掉 | 带 `low` 的条目按同一规则处理：有说明照抄说明，没说明用 `RELIABILITY_CAVEAT["low"]` 那句中性提醒；`test_reliability_does_not_change_the_pick` 与 `test_pick_is_by_role_not_by_marker` 钉住"标记不改挑选结果" |
| 立法过程草案件混进主依据 | `_legis_round` 给草案/审议页条目打 `legislative_process`；`pick_primary` 把它整条挡在主依据之外；`tax_answer` 草案轮覆盖 legis+shui5+wechat 三源 | 草案线索即使唯一召回也不当主依据，`_why` 写明"本组只有立法过程线索"，行本身仍留在【待核对线索】与 `_runners_up` 里；`test_legislation_lane_items_cannot_become_primary` 与两条 `test_draft_round_*` 钉住 |
| 取数失败被记成"这一轮 0 条" | `_rows_and_error` 把源失败与判空分开带出；`gather` 记 `errors` 与 `rounds_done[].failed`；`compose`/`_print_answer` 打【取数失败】 | 主依据静默降级读得出来了；`test_source_defects.py::TestRoundFailureIsNotSilent` 三条（具名失败、干净空结果仍静默、草案轮不得顶位）|
| 总局接口的 `pageNum` 从 0 起算，与本项目对外的页码差一位 | `tax_web_search.search_chinatax` 在发请求处减 1；`tests/test_routing_terms.py::test_search5_page_index_is_zero_based` 打桩断言发出去的 `pageNum` | 基准错时结构照常、条数照常，只是每轮都丢掉相关度最高的首屏。清单缓存键带 `tax_fgk.LIST_KEY_REV` |
| 文号、时效、效力级别原先一律报"未标明" | `tax_web_search` 把录入项 `govDoc.docNum`/`xxgk_aging`/`xxgk_effectLevel`/`cwrq` 落成 `document_number`/`status`/`effect_level`/`publish_date`，`tax_fgk._scan_list` 逐条透传，`tax_evidence.rank_of`/`judge_validity` 按 `effect_level` 定档、按 `status` 判时效 | 主依据一行直接印文号与时效；`status` 带上 `status_from` 说明是哪个录入项给的，判级理由可追溯 |
| 解读件标题整份嵌着原文件名，被认成原文件本身 | `tax_evidence.rank_of` 先于分类与形态规则判「以「解读」收尾」→ 技术性口径；`tax_answer.locate_cited_document` 把原文排在解读之前 | 只取到解读时 `compose` 不把它放主依据，另给 `cited_note`（"原文待核"），答案里必须写这句 |
| "已修改"被当成时效不明 | `tax_evidence.judge_validity` 把"已修改/已修订"判为 `effective`，并在 `note` 里追加"引用须按修改后的版本" | 仍在效的文件留在依据层，该说的话换成"引哪一版" |
| 空清单有四种成因，原先都塌成"未找到/库里没有" | 见下三条：`_fetch_failed`（取数真失败）、`_filter_note`（维度拼窄）、`_empty_reason`（翻页取空）、翻完未筛出的 `_error`；`frontend/index.html` 的 `renderResults` 按"越靠近真相越先看"分流，取数失败排最前 | 越界是翻页用法问题、拼窄要放宽维度、失败要稍后重试，四种都不写成"该库没有这份文件"；盯它的是 `test_source_defects.py::TestFrontendShowsDegradedNote::test_empty_state_keeps_the_causes_apart` |
| 规则陈述类问题被回三条"你的主体/地区/金额未交代" | `tax_analyze.RULE_STATEMENT_TYPES` 与 `RULE_AXES`：`lookup`/`fill_blank` 只保留时点轴，其余缺失转成 `rule_note` | 追问只问会影响规则本身的问题；个案缺失改在答案里以【适用边界】写明 |
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
| 接口对「维度拼窄」与「该库根本没有」回的是同一个 0，用户看不出区别 | `tax_web_search.build_filters` 只放行有实测数字支撑的七组收窄维度（2026-10-01 五组＋2026-10-02 的时效与效力等级），`search_chinatax` 在 `filters` 非空且 `total==0` 时写 `_filter_note`（把发出去的维度一并附在句尾），`tax_fgk._scan_list` 把它从第一页接住并顶替"翻完前 N 页"那句 | 这七组都已接进命令行；盯它的是 `test_search5_zero_with_filters_says_it_might_be_too_narrow` 与 `test_filters_produce_too_narrow_instead_of_libraries_empty`。取值域外（`--aging 有效`）在 `build_filters` 就 `ValueError`，因为接口对域外值是静默回基线 |
| 同一文件在库里有现行版与被废止版两份，正文里却读不到对方的链接 | `tax_fgk.fetch_associations` 走 `queryManuscriptAssociation`（POST 到 www 域、入参是 `article_id_from_url` 从 URL 末段取的 id），关联条目里相对链接一律补 `FGK_HOST` 前缀；`--assoc` 逐条挂载 | 静态详情页 HTML 里那四组是空的，`fetch_fgk_body` 又把标签全剥干净，所以 `<a href>` 一条都抠不出来；接口主机与链接主机相反这件事是本机实测出来的（POST www 200 / fgk 404，链接 fgk 200 / www 404），盯它的是 `test_fetch_associations_parses_measured_payload` 与 `test_article_id_from_url_forms` |
| 界面高级筛选栏的范围／匹配／日期／排序只对 NPC 生效，数据源选到税务总局／法规库就被静默丢掉，控件看着全局实则空转 | `tax_server.api_search` 的 chinatax／fgk 分支经 `tax_web_search.build_filters` 把控件翻成 search5 `filters` 透传（范围→`wordPlace`、匹配→`participleRule`、日期→`cwrqStart`/`cwrqEnd`、时效→`xxgkAging`），排序另走 `tax_server.SORT_TO_ORDER`（日期↓→`orderBy=1`），非法日期与非法时效报 400；「时效」那一栏在 search5 两源换成五个文本值且默认「全部」——白名单基数 1908 条里 1165 条该栏为空，照 NPC 的默认「现行有效」收窄会把现行文件一起筛掉；0 条成因走 `result._filter_note`，深页条目在卡面上单独一行提醒 | 实测两栏都收窄（范围=标题＋时效=全文有效）「增值税」1908→146，单发时效 252；全站基数下同一维是标题 2998、基线 13152；盯它的是 `test_chinatax_maps_ui_filters_into_the_request`、`test_fgk_fulltext_does_not_narrow_to_title`、`test_bad_date_on_chinatax_is_400_not_silent_baseline`、`test_bad_aging_on_chinatax_is_400_not_silent_baseline`、`test_ui_aging_reaches_xxgk_aging_and_blank_sends_nothing`、`test_sort_from_ui_reaches_order_on_both_search5_paths` |
| 「时效」换值域时把「全部」那项写成空数组，浏览器下拉里冒出一个 `undefined` 选项，而 `select.value` 仍回 `""` 看着正常 | `frontend/index.html` 的 `syncFilterControls` 把两档值域都写成 `['值','文案']` 两项数组；`test_frontend_aging_control.py::TestOptionPairsAreNeverIncomplete` 静态扫 `const opts=` 那行，命中空项 `[[]` 就报红 | **这条只有真浏览器读那一栏才看得见**：本机 2026-10-02 打开 `filterStatus` 逐源读出 chinatax／fgk 首项是字面量 `undefined`（`st.value` 却是 `""`，纯静态读源码与后端测试全绿），修回 `['','全部']` 后复读四源选项集：npc／aggregated 两档、chinatax／fgk 「全部」+五档 |
| 归类把 sta 专题自动换源到法规库那条路仍把那几个控件丢掉，只有手选数据源才生效 | `tax_server.api_search` 抽出 `_ui_filters`，sta 专题改查 `search_fgk` 时也走它下推 filters 与 `SORT_TO_ORDER`（非法日期、非法时效同样 400） | 换源那一路以前只透 `body`，界面收窄对专题题静默无效；盯它的是 `test_sta_topic_routed_to_fgk_carries_ui_filters` |
| 取数真失败与"翻了没筛出/库里没有"共用一句 `_error`，界面一律印"未找到" | `tax_web_search._empty_result` 与 `tax_fgk._scan_list` 的 `first_error` 分支各补 `_fetch_failed`，`renderResults` 把它单列成"取数失败…可稍后重试"排在最前，不进未找到分支 | 服务侧故障被读成"换个词重搜就有"是静默降级；盯它的是 `test_request_failure_is_flagged_not_read_as_no_hit`、`test_scan_list_flags_fetch_failure_apart_from_empty`、`test_fetch_failed_flag_survives_into_payload` |
| 检索不发 `label` 时是全站，法规库清单被新闻与各地动态挤满：「转让定价」全站前 3 屏 0 条法规库条目，自适应早停在第 3 页收尾，整趟取回 **0** 条 | `tax_web_search.FILE_LABELS` 是十个文件类标签的白名单，`search_chinatax` 与 `tax_fgk._scan_list` 默认按它发（`file_only=True`，只有 CLI 的
`--all-labels` 能关，界面那一路恒用默认范围）；实测同一词收窄后 18 条全部取回，
「小微企业」4822→184。清单缓存键的 `LIST_KEY_REV` 随之从 `pn0` 改成 `pn0-files`，
非默认范围另走 `tax_fgk.scope_token` | 收窄的代价是漏掉标在「视频政策解读」「图片政策解读」上的法规库条目（实测「研发费用加计扣除」全站前 3 页 7 条），那类回的是 `media_only` 空正文；`_scan_list` 在 0 条那句 `_error` 里把范围写明，不让人把"窗口里没有"读成"库里没有"。盯它的是 `test_file_labels_is_the_default_scope`、`test_all_labels_switch_reopens_the_whole_site`、`test_scan_list_threads_scope_and_order_to_every_page`、`test_scope_and_order_have_their_own_cache_keys`、`test_zero_hit_sentence_names_the_label_scope` |
| 界面「公布日期」那一栏在三条路径上语义不同却长得一样：多源聚合根本没下发（`tax_aggregator.aggregate_search` 的签名里没有 `date_from`/`date_to`）；NPC 只给上界时 `gbrq` 发出去是空数组，等于没收窄；NPC 精确检索（`search_type=1`）带上日期后接口丢掉检索词，只按区间回一叠与本题无关的法律清单 | 聚合分支把区间下推：NPC 缺端补界（`tax_search.DATE_CEIL`/`DATE_FLOOR`，只给上界也发真区间），税务总局经 `tax_web_search.build_filters` 的 `cwrq_from`/`cwrq_to`；360／税屋／微信公众号这一路没有日期参数，改在本轮取回的条目窗口内按条目自带日期补筛（`tax_aggregator.DATE_LOCAL_SOURCES`），逐源计数进 `_date_filter.local_window`。精确检索＋区间这一组不拒绝请求，改成取回后按「标题是否含检索词」二次核对：`total` 只报复核后的条数，接口原报条数留在 `source_total`，成因写成 `_date_note` 交界面照抄。格式校验统一走 `tax_search.check_iso_date`，四条分支（NPC 单源、聚合、chinatax、fgk）非法日期一律 400 | 2026-10-03 本机实测：只给上界「增值税」45→38 条（区间内最大 2020-08-04；修前是 45 条、首条 2025-12-25，与不带日期逐条一样）；聚合「增值税」2024-01-01—2026-12-31 从 16 条里有 4 条越界收到 9 条 0 越界；精确检索「中华人民共和国增值税法」起 2026-01-01 的 88 条复核后留 0 条（88 留在 `source_total`），同一个词不带日期是 2 条。**没带日期的条目保留而不删**，只另计 `no_date`——360 那一路压根没有日期字段，按越界处理会整源消失，再把空清单读成"该源在这个区间里没有内容"。盯它的是 `test_date_window.py` 的 `test_only_to_uses_floor`、`test_overfetch_inherits_the_range`、`test_drops_off_topic_and_notes_it`、`test_forwards_to_both_server_sources`、`test_undated_items_are_kept_and_counted`、`test_aggregated_bad_date_is_400`、`test_npc_single_source_bad_date_is_400`、`test_local_counts_are_rendered` |

## 两个值得单独说明的细节

- **税屋空结果归因到 360，不是税屋自己坏了**。税屋的链接靠 360 的
  `site:shui5.cn` 检索取得，所以 360 挂了、税屋又 0 条时，程序给它单独记一条缺口
  并标 `blocked_by: so360`；只有税屋自己报错（如 `WAF 挑战未通过`）时才归它自己。
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
| ③④ 决定取数轮次与候选词 | 脚本给计划，Agent 放宽 | `tax_answer.build_plan`，回显在 `rounds_done` | `python scripts/tax_answer.py "<原话>" --plan` |
| ④ 五源聚合与取数失败留名 | 脚本 | `tax_answer.gather`，产出 `rounds_done[].failed` | `tests/test_source_defects.py::TestRoundFailureIsNotSilent` |
| ⑧ 层级、时效、主题对应三轴定级 | 脚本 | `tax_evidence.LEGAL_RANK` / `rank_of` / `judge_validity` / `on_topic_of` / `grade` | `tests/test_judge_validity.py` |
| ⑧ 角色分层与主依据挑选 | 脚本 | `tax_evidence.role_of` / `pick_primary` / `_order_key`，理由写进 `_why` | `test_legislation_lane_items_cannot_become_primary`、`TestConstitutionIsNotTheHeadline` |
| ⑧ 没取到法定依据时收口转「依据不足时」 | 脚本提示，Agent 换形态 | `compose` 的 `statutory` 为空时产出 `evidence_gap`，`_print_answer` 打"⚠ …"那一行 | 命令行读这一行是否出现 |
| ⑥ 组织最终答案 | Agent | 骨架在 `references/output_templates.md` | 对照模板逐段，缺段即不合格 |
| ⑥ 命令行排版 | 脚本 | `tax_answer._print_answer`；单源卡片走 `tax_formatter` | 网页那条另见 ⑫ |
| 优惠叠加与择一核验 | Agent | 无——原文措辞判不了；要求写在 ⑦ 第 22 条 | 答案里【优惠交互与限制】段在不在 |
| 优惠全集穷举 | 脚本 | `subskills/tax-preference/preference.py` 的 `sync` / `query` | `tests/test_preference.py` |

## 仍然是边界的几件事

- **闸管串行，不管快慢，也不管等多久**。等锁超过 `TAX_NPC_LOCK_TIMEOUT` /
  `TAX_SOGOU_LOCK_TIMEOUT`（默认各 180 秒）会抛 `TimeoutError`，报错里直说
  "另有进程正在打 NPC / 搜狗"。遇到它先找自己另一个会话——那是在排队的提示，
  不是代码坏了。
- **闸只罩两个已知会限流的入口**。NPC 与搜狗是实测到阈值的；`mp.weixin.qq.com`
  的正文读取没测到同样的阈值，仍按 `READ_INTERVAL` 拉开间隔，没往闸里塞——
  不给自己没验证过的结论。别以为"有闸"就等于"所有站点都被限速保护"。
- **缺口说明是"这次没取到"，不是"该层没有内容"**。程序只把这句话递过去，
  不替你重试、也不替你换源补缺；用什么口径回答仍然由上层决定。
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
  `commands.md`「search5 的可发参数与分面字段」。
