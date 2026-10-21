"""上传类硬上限的单一来源。

技能包（zip / tar.gz / tgz）与工作空间文件是两条独立链路：后者按
`WORKSPACE_UPLOAD_MAX_MB` 配置、流式落盘；技能包由管理员或普通用户手工导入，
一次性读进内存再解压，所以上限写死在这里，两个端点模块共用同一份数字。

放核心层而不是端点层，是为了让 `skills.py` 与 `personal_skills.py` 不必互相
导入（端点模块互相 import 容易绕出循环依赖）。
"""

#: 技能包上传大小上限（MB）。100MB 足够装下一套带脚本、模板与少量资源的技能；
#: 再大更适合走工作空间或 Git 仓库，而不是塞进一次表单上传。
SKILL_PACKAGE_MAX_MB = 100

#: 同上，字节形式，供 `len(content) > ...` 直接比较。
SKILL_PACKAGE_MAX_BYTES = SKILL_PACKAGE_MAX_MB * 1024 * 1024
