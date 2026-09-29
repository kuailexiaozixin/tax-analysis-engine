# 源缺陷：已由代码保证的，与仍是边界的

> 本文件是 `SKILL.md` ⑩ 的下沉版。凡是修完的源缺陷，都从"要人记着的注意事项"
> 落成代码里的一个判定——不必再靠人记，程序自己会拦、会标、会把话说清楚。这里逐条
> 登记"由哪段代码保证、保证到什么程度"，最后列出**真正剩下的边界**（程序做不到、
> 也不该假装做到的）与**量过之后决定不改的**。用法见 SKILL.md 各工作流小节。

## 目录

- [由代码保证的源缺陷](#由代码保证的源缺陷)
- [两个值得单独说明的细节](#两个值得单独说明的细节)
- [量过之后决定不改的三件事](#量过之后决定不改的三件事)
- [仍然是边界的几件事](#仍然是边界的几件事)

---

## 由代码保证的源缺陷

| 源缺陷 | 现在由谁保证 | 保证到什么程度 |
|--------|--------------|----------------|
| NPC 限流，并行必现 | `tax_search.npc_gate`（实现为 `tax_http.SerialGate`）：`%TEMP%\tax-analysis-engine-npc.lock` 跨进程文件锁 + 进程内 `threading.Lock` | 多个进程同时开跑会自己排队，不用再记"别并行" |
| 360 被封 → 地方口径与税屋这一层是空的 | `tax_aggregator` 返回的 `gaps` 与 `degraded_note` | 缺了哪层、为什么缺、能不能拿别的源顶，都写成一句可直接照抄的话；网页端渲染成警示条，不再冒充普通的"0 条" |
| fgk 翻得越深相关性越差 | `tax_fgk` 第 2 页起标 `_reliability: medium`（`FGK_SHALLOW_PAGES = 1`），`tax_evidence` 认这个标记 | 深页条目仍留在结果里（它是找法规的线索），但 `citation_hint` 明写"仅用于定位法规"，挑主依据时排到无标记条目之后 |
| 公众号并发加压触发反爬 | `tax_wechat._sogou_get`：1 秒最小间隔 + 搜狗专用串行闸（`%TEMP%\tax-analysis-engine-sogou.lock`） | 打 `weixin.sogou.com` 的请求（检索、链接还原）统一进闸；离线并发用例断言同一时刻在跑的请求数峰值为 1 |
| `_reliability: medium` 只能定位 | `tax_evidence.grade` / `pick_primary` 解析该标记；`tax_formatter` 的两条渲染路径都会印出来 | 可引用性分清零、`citation_hint` 换成否决句；同分时排在正常依据之后。原先单源渲染路径会把这句吞掉，现在单源与多源归并都会先印警告 |
| `_reliability: low` 不得当权威依据 | 同上，`pick_primary` 把 low 整组剔除 | 整组都是 low 时干脆不挑主依据，`_why` 写"本组依据全部带 `_reliability: low`，不得作为依据引用" |
| 立法过程草案件混进主依据 | `_legis_round` 给草案/审议页条目打 `legislative_process`；`pick_primary` 把它与 low 同样永久出局；`tax_answer` 草案轮覆盖 legis+shui5+wechat 三源 | 草案线索即使唯一召回也不当主依据；`test_legislation_lane_items_cannot_become_primary` 与两条 `test_draft_round_*` 钉住 |
| 取数失败被记成"这一轮 0 条" | `_rows_and_error` 把源失败与判空分开带出；`gather` 记 `errors` 与 `rounds_done[].failed`；`compose`/`_print_answer` 打【取数失败】 | 主依据静默降级读得出来了；`test_source_defects.py::TestRoundFailureIsNotSilent` 三条（具名失败、干净空结果仍静默、草案轮不得顶位）|
| 总局接口的 `pageNum` 从 0 起算，与本项目对外的页码差一位 | `tax_web_search.search_chinatax` 在发请求处减 1；`tests/test_routing_terms.py::test_search5_page_index_is_zero_based` 打桩断言发出去的 `pageNum` | 基准错时结构照常、条数照常，只是每轮都丢掉相关度最高的首屏。清单缓存键带 `tax_fgk.LIST_KEY_REV` |
| 文号、时效、效力级别原先一律报"未标明" | `tax_web_search` 把录入项 `govDoc.docNum`/`xxgk_aging`/`xxgk_effectLevel`/`cwrq` 落成 `document_number`/`status`/`effect_level`/`publish_date`，`tax_fgk._scan_list` 逐条透传，`tax_evidence.rank_of`/`judge_validity` 按 `effect_level` 定档、按 `status` 判时效 | 主依据一行直接印文号与时效；`status` 带上 `status_from` 说明是哪个录入项给的，判级理由可追溯 |
| 解读件标题整份嵌着原文件名，被认成原文件本身 | `tax_evidence.rank_of` 先于分类与形态规则判「以「解读」收尾」→ 技术性口径；`tax_answer.locate_cited_document` 把原文排在解读之前 | 只取到解读时 `compose` 不把它放主依据，另给 `cited_note`（"原文待核"），答案里必须写这句 |
| "已修改"被当成时效不明压到只能参考 | `tax_evidence.judge_validity` 把"已修改/已修订"判为 `effective`，并在 `note` 里追加"引用须按修改后的版本" | 仍在效的文件留在依据层，该说的话换成"引哪一版" |
| 空清单有两种成因，原先都说成"库里没有" | `tax_web_search.search_chinatax` 写 `_empty_reason`：拿命中数 `total` 与末页比较，分开写"翻过了末页"和"该页在末页之内却没给条目清单"；`tax_fgk._scan_list` 透传 | 越界是翻页参数的用法问题，首屏空才需要换检索词再取一轮；两种都不写成"该库没有这份文件" |
| 规则陈述类问题被回三条"你的主体/地区/金额未交代" | `tax_analyze.RULE_STATEMENT_TYPES` 与 `RULE_AXES`：`lookup`/`fill_blank` 只保留时点轴，其余缺失转成 `rule_note` | 追问只问会影响规则本身的问题；个案缺失改在答案里以【适用边界】写明 |
| 「尚未生效」在传了 `--at` 又没有施行日期时被转正成现行有效 | `tax_evidence.judge_validity`：`at` 分支对 `pending` 单独走一条判定——有施行日期且不晚于观察时点才转正，没日期留在 `pending` | 观察时点那条推荐用法不再把没开始施行的文件送进依据层（`pending` × 规范性文件 = 38.5 分）|
| 「财税文件」那一栏根本不录时效，上位规则被成片压在依据线以下 | `tax_answer.corroborate_validity_from_target` 读点名文件正文首段"根据……规定"列出的制定依据，给状态判不出来的条目打上 `corroborated_by`；`tax_evidence.judge_validity` 认这个字段，按在效计分 | 只有状态"判不出来"的条目会被补，明文废止或未生效的一律不动；只读首段；答案里 `并列依据` 那行下面会印证据来源，要照抄 |
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
