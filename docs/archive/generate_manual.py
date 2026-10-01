#!/usr/bin/env python3
"""已废弃 —— 本脚本不再生成 Word 用户手册。

为什么停用
    它原先把整份项目说明硬编码进一个 .docx：定位、架构、模块表、数据源、
    版本记录、FAQ 全部另抄一遍。代码一改就要多处同步，实际已经漂移——
    文档里还留着早已删除的目录、旧版本号，以及"法规库正文取不到"这类
    已被推翻的结论。它还额外引入 python-docx 依赖，而检索主链路完全不需要它。

现在文档在哪
    README.md   项目总览（面向人：定位 / 架构 / 用法 / 目录）
    SKILL.md    Agent 操作手册，也是版本号的唯一来源

保留本文件只为兼容历史引用；运行它只会打印这段说明并以非 0 退出码结束。
本文件置于 docs/archive/ 目录：scripts/ 只留在用的脚本，免得照目录列表挑脚本的人
挑到一个只会报错的入口。
"""

import sys

_MESSAGE = """\
generate_manual.py 已废弃（位于 docs/archive/），不再生成 Word 手册。

项目文档请直接看：
  README.md   项目总览（面向人）
  SKILL.md    Agent 操作手册（版本号唯一来源）

本脚本硬编码的内容与上述两份文档重复，已多次漂移，故停用。
"""


def main() -> int:
    print(_MESSAGE, file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
