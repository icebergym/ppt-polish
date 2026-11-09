# PPT 优化

用于制作和优化可编辑演示文稿的通用技能。版本：**1.1.0**。

支持从提纲制作新稿、对已有材料保真美化，以及在明确授权后调整内容和逻辑。重点处理信息层级、标题表达、图表选择、空间分配、对齐与跨页一致性。

## 安装与使用

在支持技能的工具中安装本仓库，技能目录名为 `ppt-polish`，入口为 [SKILL.md](SKILL.md)。

示例：

- “用 PPT 优化技能美化这份材料，保留所有文字和数字。”
- “做一份五页的汇报，先梳理每页的核心结论。”
- “优化这两页的信息层级，保持页数和数据口径不变。”

只说“优化”时默认保真美化；内容升级需要明确授权。

## 字体依赖

**使用此技能前须具备 MiSans。** 运行以下命令，已安装则检查复用，缺失则从官方来源下载安装；失败时停止，不静默替换字体。

```sh
python3 scripts/ensure_misans.py
```

脚本使用 Python 3 标准库，校验下载包、字体身份及系统可用性。首次字体包下载约 228 MB，字体文件不包含在仓库内。见 [字体安装与验证](references/font-setup.md) 及其中的官方许可。macOS 已验证；其他系统须在目标环境验收。

## 主要文件

| 文件 | 用途 |
|---|---|
| [SKILL.md](SKILL.md) | 模式、流程及边界 |
| [references/brand-layout.md](references/brand-layout.md) | 视觉层级与布局 |
| [references/story-and-pages.md](references/story-and-pages.md) | 标题、叙事与可选页型 |
| [references/editing-and-evidence.md](references/editing-and-evidence.md) | 保真编辑与证据核验 |
| [references/runtime-and-checks.md](references/runtime-and-checks.md) | 制作、导出及检查 |
| [scripts/content_guard.py](scripts/content_guard.py) | PPTX 内容快照与比较 |
| [scripts/version_store.py](scripts/version_store.py) | 技能快照与恢复 |
| [scripts/audit_public_release.py](scripts/audit_public_release.py) | 发布文件白名单与隐私检查 |

仓库仅包含规则、脚本和公开字体依赖信息。演示素材由使用者自行提供；项目文件、客户数据、个人记录及生成的演示稿保留在本地工作目录。
