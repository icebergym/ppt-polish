# MiSans 必备依赖

使用此 Skill 时，会默认下载安装字体 MiSans，否则无法使用。已安装且检查通过时直接复用，不重复下载。此技能使用 MiSans 字体；字体文件由字体官方提供，使用遵循 [官方下载页](https://hyperos.mi.com/font/zh/download/) 与 [许可协议](https://hyperos.mi.com/font-download/MiSans%E5%AD%97%E4%BD%93%E7%9F%A5%E8%AF%86%E4%BA%A7%E6%9D%83%E8%AE%B8%E5%8F%AF%E5%8D%8F%E8%AE%AE.pdf)。安装和使用字体适用该协议。技能仓库只保存脚本和来源清单，不另行分发字体包。

## 必跑入口

用当前环境可用的 Python 3，在技能目录运行：

```text
python3 scripts/ensure_misans.py
```

无需第三方 Python 包。脚本检查十个静态字重：Thin、ExtraLight、Light、Normal、Regular、Medium、Demibold、Semibold、Bold、Heavy。读取字体内部标识，并检查系统是否可以识别。只有 JSON `status` 为 `ready` 且退出码为 0 才能继续。

缺失时下载官方 MiSans 包及许可，验证固定 SHA-256、文件大小及每个字体身份，只安装缺失字重。字体安装到当前用户目录，不覆盖不同的已有字体；许可保留在字体目录。官方包约 228 MB，第一次下载可能需要数分钟；缓存校验通过后复用。

| 系统 | 当前用户字体位置 | 验证方式 |
|---|---|---|
| macOS | `~/Library/Fonts` | CoreText 可用字体清单 |
| Linux | `$XDG_DATA_HOME/fonts/MiSans`，默认 `~/.local/share/fonts/MiSans` | Fontconfig，要求 `fc-cache`、`fc-list` |
| Windows | `%LOCALAPPDATA%/Microsoft/Windows/Fonts` | 用户字体注册与 GDI 加载 |

macOS 已在本次环境验证；Linux、Windows 分支仍需在实际机器验收，不能据此声称已经跨平台实测。其他平台明确停止。诊断时可用 `--check-only`，缺失即失败，不代替正常使用时的自动安装入口。

## 成功与阻塞的处理

- 系统检查成功后，还要确认实际作者工具支持所用 MiSans 字重。文字、表格、图表分别核对；必要时加载字体文件、刷新字体缓存或重新开启作者/渲染进程。
- 下载、校验、安装或系统识别失败，脚本输出 `blocked` 并以非零码退出。停止此技能的制作流程，说明具体失败项；不能改用其他字体、伪造检查通过或跳过校验继续交付。
- 仅受沙箱或系统权限限制时，按宿主权限机制申请必要的网络/当前用户字体目录权限；已有用户授权时不额外询问相同的业务决定。用户拒绝所需权限则保持阻塞。
- macOS 沙箱可能阻止 CoreText 访问字体服务，即使字体文件齐全也会验证失败。先按宿主机制申请查询系统字体所需权限后重试，不据此重复下载或静默放行。
- 官方包内容变化导致校验失败时，从官方下载页重新核验来源和字体身份，经确认后更新 [来源清单](misans-source.json)，不自动接受新哈希或第三方镜像。
- 字体不随 PPT 或 GitHub 仓库单独打包；转移到新电脑时重新运行同一检查。不能把系统有字体等同于接收方已有字体。
