---
name: shui5
description: |
  税屋（www.shui5.cn）站点子技能。当需要**从税屋取回一篇文章、按栏目翻它的清单、
  判断税屋正文为什么是挑战页或为什么短了一截**，或需要重测这个站的放行机制、
  站内搜索表单、栏目分页与正文容器时使用。触发词：税屋、shui5、实务解读、地方口径文章、
  税务问答、税屋正文、税屋取不到、WAF、acw_sc__v2、arg1、站内检索、栏目翻页。
  本子技能管"这个站怎么取"，母技能管"取回来的东西在答案里算什么"；全程只发 HTTP 请求、
  只驱动本机已装浏览器，**不调用任何模型、不消耗任何额度**。
---

# 税屋（shui5.cn）站点子技能

税屋是实务解读与答疑站，属母技能 ③ 的 L4 一层。它的取数难点不在"有没有这份内容"，
而在**同一个 URL 在没凭据时返回的是另一份页面**：没放行凭据时，首页、文章页、
`robots.txt`、甚至一个不存在的路径都返回同一份 23,682 字节的挑战页，且 HTTP 都是 200，
所以"请求成功"与"取到内容"在这个站是两件事。本子技能把这条边界上的全部实测写清楚，
母技能只保留判据，不再重复站点机制。

## 职能分工

**留在母技能，不在这里改**：税屋是五源之一（`tax_aggregator.DEFAULT_SOURCES` 的
`shui5`）、L4 必做、角色 `practice` 进答案与论证、不得顶替被解读的那份文件当依据
（⑦ 第 6 条）、空结果归因到 360（`blocked_by: so360`）、取回条目回 ⑧ 按层级·时效·
角色定级。这几条判据的措辞与用例都在母技能，本文件只写它们落到税屋这个站上时
需要知道的事实。

**归本子技能**：WAF 挑战面与凭据寿命、两条检索入口（站内原生搜索与 360 `site:` 检索）、
栏目地图与分页参数形态、正文容器与失真点、站点的 robots 允许范围、实测台账、
`probe_shui5.py` 复跑工具、改进方案。

## 工作流

**第一步 定这一趟要什么**——下面四行的入口与耗时不同，先选对再动手：

| 要什么 | 走哪条 | 需要什么凭据 |
|---|---|---|
| 按关键词找文章（母技能的主用法） | 360 `site:shui5.cn` 检索（`tax_so360.so360_search`） | 不需要；只在随后要正文时过一次 WAF |
| 按关键词找文章，且要日期/摘要/所属栏目 | 站内原生搜索 `POST /e/search/index.php`（改进项 一，尚未接进脚本） | 需要一份放行 cookie |
| 读某一栏目的全部文章（"税屋问答都讨论过什么"） | 栏目列表页 `/article/<栏目>/<classid>_<页>.html` | 需要一份放行 cookie |
| 只读已知 URL 的正文 | `tax_shui5.fetch_shui5` / `read_articles` | 需要一份放行 cookie |

**第二步 取放行凭据**：`tax_browser.BrowserSession` 用 `executable_path` 驱动本机已装
浏览器（`find_installed_browsers` 按 Edge → Chrome → Brave → 360 → Firefox 的顺序找）
跑一次挑战，把 cookie 交给 `requests.Session` 连读，一个浏览器内核都不安装。
实测 5.9–7.0 秒过挑战；`acw_sc__v2` 单独就能放行，寿命约 60 分钟，`acw_tc` 与
`ssxmod_itna`/`ssxmod_itna2` 单独都不放行（约 30 分钟）。**批量读多篇的正确入口是
`read_articles`：WAF 只过一次**，之后每篇 0.2 秒级，逐篇重启浏览器是把 6 秒乘以篇数。

**第三步 检索**：两条入口覆盖的是不同的排序，不是彼此的替代——同一个词「研发费用加计扣除」
按文章 ID 归一后比首屏结果：站内 30 篇、360 10 篇，**交集 0 篇**、只在一路出现的 40 篇；
360 只给链接与标题，站内搜索每行还带日期、摘要与所属栏目，并给出这一词的总条数
（实测 433）。比对由 `probe_shui5.py` 的第 5 项直接算出来，不必手工核。

**第四步 取正文并按分页续**：正文容器是 `div.arcContent`，元信息在 `articleResource`
（作者、时间）与 `articleDes`（摘要）。地址形如 `/article/<两位十六进制>/<ID>.html`
的中间段是哈希分桶，不是栏目，不能按它推断这篇属于哪一栏。**长文有 `$N` 续页**
（`/article/<分片>/<ID>$2.html`）：抽样 20 篇（实务解读栏目首页的前 20 条）里 2 篇有续页，
第 70138 篇三页各 999／1,117／1,513 字，只读第 1 页丢七成；第 9212 篇两页各 7,233／6,691 字。
试探下一页直接按地址递增：`<ID>$2.html`、`<ID>$3.html`……**站点回 404 且只有 16 字节
就是到底了**，不必解析页面内容。

**第五步 回母技能定级**：取回的正文按角色 `practice` 进答案，写口径时标明原文待核。
这一步的判据在母技能 ⑧ 与 `references/evidence_grading.md`，本子技能不参与。

## 命令

```bash
# 检索并按篇取正文（一次过 WAF）
python scripts/tax_shui5.py "研发费用 加计扣除 口径" --size 5 --read
python scripts/tax_shui5.py "<关键词>" --size 5 --json   # 只要链接与标题，不启浏览器
python scripts/tax_browser.py --check                    # 探测本机已装浏览器，不下载内核
python subskills/shui5/probe_shui5.py                    # 全量复测（过 WAF 后逐项实测八项内容）
python subskills/shui5/probe_shui5.py --no-browser       # 离线项：挑战面 + 360 通道
python subskills/shui5/probe_shui5.py --keyword 留抵退税 --column ShuiWuChouHua
```

`probe_shui5.py` 的输出逐项是：挑战面四路径的字节数与判同、过通道与耗时、每枚 cookie
的到期与单独放行判定、站内检索的落点/总条数/行数/字段齐全行/样例、360 通道条数、
栏目地图（主栏目·地方·专栏）、栏目页的分页控件与下一页回访、正文形态（容器命中、
页数、截断风险、尾部混入、时间字段、附件名）。它只读，约 15 个请求，
cookie 只在内存里，不落盘。

## 站点指纹与允许范围

- 站点是帝国 CMS（EmpireCMS），由 `robots.txt` 的 `/e/class/`、`/e/update/` 等目录暴露。
- `robots.txt` 禁的是 `/d/`、`/e/class/`、`/e/config/`、`/e/data/`、`/e/enews/`、`/e/update/`；
  **`/e/search`、`/e/tags` 与 `/article/` 都不在禁止之列**，所以站内检索与标签页是站点
  自己留给抓取的路。
- `sitemap.xml` 是 404（16 字节），没有可用的站点地图。
- 首页零 XHR/fetch，纯服务端渲染，**没有 JSON 接口可挖**；首页可见的搜索表单指向
  `zhannei.baidu.com/cse/site`，那是百度站点搜索，不是站内结果。
- `m.shui5.cn` 不解析；http 与 https 返回的挑战页一致，换成 http 绕不开。

## 站内原生搜索的表单（改进项一的基础，先记这里）

结果页自己带得出真实表单，照抄即可：`POST https://www.shui5.cn/e/search/index.php`，
字段 `tbname=article`、`show=title,keyboard,smalltext`、`tempid=2`、`keyboard=<词>`、
`Submit=站内搜税`，302 跳到 `/e/search/result/?searchid=N`，返回 80,161 字节。
行容器 `div.xwt2`，五个字段分别落在 `xwt2_a`（标题，`title` 属性是完整标题）、
`xwt2_c>p.p1`（摘要）、`xwt2_d>p.p1`（链接）、`p.p3`（日期）、`p.p4>a`（栏目）。
每页 30 行，翻页用 `/e/search/result/index.php?page=N&searchid=M`，**`page` 从 0 起**。
两种写法实测不通：`tempid=1` 只回"N 条关于…"而 `div.left2c` 是空的；
`show=title` 单值直接落进 1,222 字节的"信息提示"页。多词之间用空格是 AND，
「高新技术企业认定 条件」这类组合实测就落进"信息提示"页（1,222 字节），
所以短词比长词稳。

## 栏目地图与栏目分页

首页的 `li.mainlevel` 只给 11 个主栏目（这一块的项按路径筛掉"首页"那一条）：中央法规
`/article/NianDuCaiShuiFaGui/`、地方法规 `DiFangCaiShuiFaGui`、实务解读 `ShiWuJiDu`、
税收优惠 `ShuiShouYouHui`、税务合规 `ShuiWuChouHua`、税务稽查 `ShuiWuJiChaAnLi`、
纳税评估 `NaShuiPingGu`、税会差异 `NaShuiTiaoZhen`、问答 `ShuiWuWenDa`、
财税表单 `CaiShuiBiaoDan`、资讯与投稿 `Bulletin`。**30 个地方栏目目录与 7 个专栏不在
`li.mainlevel` 里**，它们是首页其他位置的链接，只按 mainlevel 解析会得出"地方栏目 0 条"
（探针早期就踩过这一条，现在两路分开取）。地方目录含省、直辖市与计划单列市
（`AnHuiShengCaiShuiFaGui`、`ZhongQingShiCaiShuiFaGui`、`DaiLianShiFaGui` 等），
专栏是 `/s/kjsw`、`qysdssw`、`grsdssw`、`hhqycssw`、`yhssw`、`fcssw`、`qssw`。
两个取值例外要知道：山西的目录名是 `category_181` 而不是拼音，部分省级目录名被截断
（如 `GuangDongShengCaiShuiFaGu`），所以**栏目目录名只能从页面链接里取，不能由拼音拼**。

栏目列表页的分页形态是 `/article/<栏目>/<classid>_<页>.html`（例：
`/article/ShiWuJiDu/12684_2.html`），控件在 `div.pageWrap > div.pagelist`，实务解读这一栏
报"共 396 页 12268 条记录"，问答那一栏另一次读数是"共 323 页"。列表页 52–88KB、
去重后的文章链接 158–182 条（按栏目而异）。三种常见猜法都无效：
`index_2.html`、`list_2.html` 为 404，`?page=2` 返回第 1 页原样（参数被忽略，
不是错误）。**按 `?page=` 翻栏目的人会拿到第 1 页的全部内容并以为翻完了。**

标签页 `/e/tags/?tagname=<真实标签>` 可用（增值税法 81,741 字节、社保基数 86,591 字节），
标签名不存在时返回 1,207 字节的"信息提示"页——这一路适合把检索词换成站点自己在用的标签。

## 边界：这一层不给什么

1. **不给法定依据**：税屋的文章是第三方解读，被它解读的那份文件才是依据（母技能 ⑦ 第 6 条）。
2. **不给地方口径的替代**：省局原文在母技能 ③ L3 的 360 站内那一路，税屋的"地方法规"栏目
   是转载与解读，引地方规定仍要回官方站点核对原文。
3. **不给日期当时效判据**：正文区的「时间：2026-10-15」这类字段是站点自己的发布日期，
   不是文件的成文或废止日期。
4. **不承诺正文完整**：`$N` 续页存在、附件只在正文里以文件名＋链接出现（抽样 20 篇中 2 篇有
   pdf/doc/xls），这两类内容当前脚本都不取。
5. **不并发加压**：`READ_INTERVAL = 2.0` 秒是站点对高频请求的容忍度，探针也用 1 秒间隔。

## 改进方案

三条改进按实测证据排序，都**尚未实施**，逐条的数字与判据在 `NOTE.md`；
实施时同步改 `references/source_defects.md` 对应条目，不能只留在文档里。

1. **把站内原生搜索接成第二检索入口**（价值最高）：现在税屋的链接只能从 360 取，
   360 一挂这一层就是空的（`tax_aggregator` 因此给税屋单记一条 `blocked_by: so360` 的缺口）。
   站内搜索用自己的凭据走另一条路，命中还多带日期、摘要与栏目两个 360 给不出的字段。
2. **`read_articles` 按 `$N` 续页读到末页**：抽样 20 篇里 2 篇有续页，第 70138 篇
   第 1 页 999 字对三页合并 3,629 字（丢七成），第 9212 篇 7,233 字对 13,924 字。
3. **修 `tax_shui5._ARC_RE` 的收口边界**：`arcContent` 结束标记未命中时，正文尾部会混入
   「点赞（）微信 微博 × 用微信扫码二维码 分享至好友和朋友圈」这类分享控件文案
   （抽样 20 篇容器全部命中，探针取的那两篇都检出尾部混入）；
   同时"相关阅读"区平均给到 170 条链接（20 篇累计 3,399 条去重链接），属噪音，不能算正文引用。
