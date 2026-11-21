# 更新日志

## [6.70] - 2026-11-20

### 新增

- **新增 7MMTV 刮削器（`7mmtv`）**
  - 覆盖有码原版 / 中文字幕 / 無碼破解三类页面，同番号同时命中时按
    `censored_content` → `chinese_content` → `reducing-mosaic_content` 择优，
    可用 `-ss`/`--specified-url` 指定具体页面。
  - 直连可访问（实测 200，无需 Cloudflare solver）。
  - 已加入 `adult_full_sources` 与 storyline 中文简介站点；详情页无简介时
    自动回退 storyline 多源抓取（带防递归保护）。
  - `config.ini` 的 `[storyline]` 站点列表末尾加入 `7mmtv`；
    `py_to_exe.ps1` 补充 `scrapinglib.7mmtv` 的 hidden-import。
- **`[common] success_list_path` —— 成功列表（纯输出）**
  - **留空（默认）= 不启用**：不生成任何文件，也不影响刮削流程；
    与 `failed_list_path` / `unrecognized_list_path` 不同，**没有**回退到失败目录的默认路径。
  - 每成功处理完一部影片，把该视频文件的完整路径追加一行；已存在则跳过，
    绝不删除/覆盖列表已有内容（`core.record_success`）。
  - **只输出，不参与跳过**：下次运行是否重新处理仍由原有规则决定
    （模式1/2 文件已移走；模式3/4 看 `.nfo` 与 `nfo_skip_days`）。
  - 启用后与另外两张表共用 `[common] failed_list_separator` 的批次分隔行。
- **Telegram 通知补充 emoji**
  - 单部消息：`📄 文件`、`❗ 原因`、`🕒 时间`。
  - 汇总消息：`✅ 成功 / ❌ 失败 / ❓ 未识别 / 📦 合计 / ⏱️ 耗时 / 🚀 开始`，
    累计模式另加 `🔁 本轮` / `🧮 累计`；达到 `cumulative_target` 时标题改为 🎉。
- **`[cloudflare] debug` —— CF 日志独立开关**
  - `1`：打印全部 `[CF]` 日志，包括 solver-first 跳过直连、挑战后强制走 Solver、
    命中挑战/连接失败升级、cookie 复用成功、bypass 失败回退直连、TRAWL 回退 `/v1`、重试轮次。
  - `0`：以上 `[CF]` 日志全部静默，**即使 `[debug_mode] switch = 1` 也不会打印**。
  - 未配置该键时跟随 `[debug_mode] switch`，老配置文件行为不变。
  - 该开关只影响 `[CF]` 相关输出，其余 debug 日志不受影响（`config.CloudflareConf.debug`）。

### 修复

- 移除 `ImageProcessing/baidu-v3.py` 中硬编码的百度云 API 凭据。
- `scrapinglib/madou.py`：`change_number()` 未命中规则时返回三元组，修复解包崩溃。
- `scrapinglib/dlsite.py`：`getExtrafanart` 补传 `htmltree`；检索四轮为空时返回 404。
- `xlog.py`：`log()` 在 `buffer_size>0` 时引用未定义变量 `string`。
- `scrapinglib/avsox.py`：封面与小封面（`posterLarge`/`posterSmall`）颠倒。
- `scrapinglib/tmdb.py`：绝对 `og:image` 被重复拼接站点前缀。
- `docker/docker-compose.yaml`：入口文件名改为 `Movie_Data_Capture.py`。
- `pissplay` / `jav321` / `gcolle` / `iqqtv` / `javmenu`：索引越界、XPath 缺 `@`、
  时长标签多余分号等。
- `getchu` / `fc2`：`eval()` 改为直接调用 / `json.loads`。
- `scrapinglib/parser.py`：`getHtml` 对非字符串响应（`return_type='object'`）做 404 判断保护。
- `subtitles`：所有请求补充超时；`requirements.txt` 补 `face-recognition`、去重复项。
- `Makefile` / `wrapper/*.sh`：打包补 `ImageProcessing/models`、修正 `--hidden-import` 写法。
- `scrapinglib/QueryError.py`：补充 `message` 属性；`storyline`/`number_parser` 边界与拼写修正。

## [6.6.9] - 2026-11-08

### 新增

- **Cloudflare 挑战改走 solver-first**
  - `[cloudflare] solver_first`（默认 `1`）：命中过 Cloudflare 挑战的站点，后续请求
    不再先打直连，直接交给外部 CF 服务（TRAWL / FlareSolverr）；solver 失败才回退直连。
    设 `0` 则恢复「只对 `sites` 白名单生效」的旧行为。
  - `[cloudflare] solver_reuse_ttl`（默认 `600` 秒）：solver 成功后的 cookie 复用窗口。
    窗口内该站点先直连，并自动带上 solver 解出的 `cf_clearance` 等 cookie；直连又被拦
    才清窗口、下一次重新进 solver。`0` = 不复用（每个请求都走 solver）。
  - 各通道直连命中挑战时（`requests` 的 `get`/`post`、session 适配器、cloudscraper、
    mechanicalsoup 表单）同一次调用内改走 solver；solver 不可用时保持旧行为，
    把原响应交回调用方。
  - 挑战识别：状态码 403/503 + 挑战页特征标记，或响应头 `cf-mitigated: challenge`；
    正常页里注入的 `cdn-cgi/challenge-platform` 脚本不会造成误判。
  - TRAWL 原生 `/scrape` 请求新增 `sessionId=adc-{host}`，外部服务可据此按站点隔离会话，
    solver 侧日志也能看出是哪个站触发的。

## [6.6.8] - 2026-10-19

### 新增

- **映射表独立配置段 `[mapping_table]`**
  - `switch`：总开关，`0` 完全不使用映射表（不读取、不下载），`1` 使用（默认）。
  - `validity`：有效期（天），超过则在下次启动时联网刷新。
  - `folder`：存放目录，留空为 `~/.local/share/mdc`，支持相对/绝对路径与 `~`。
  - `update`：联网更新开关，`1` 网络优先、失败回退本地，`0` 仅使用本地。
  - `url`：下载地址，留空为官方 GitHub raw；支持镜像/加速前缀与 `{file}` 占位符模板。
- 新增 `[priority] dmm_image_debug`：`1` 打印 DMM 高清封面成功/失败详情，`0` 静默；
  未配置时跟随 `[debug_mode] switch`。
- 新增映射表文件校验 `_mapping_file_ok()`：
  - XML（`mapping_actor.xml` / `mapping_info.xml`）必须可解析且根节点为 `actor` / `info`；
  - `c_number.json` 必须可解析为 JSON 对象。
- 新增 `[common] nfo_plot_style`：`.nfo` 的 `<plot>` / `<outline>` 支持两种写法切换：
  - `cdata`：`<plot><![CDATA[...]]></plot>`（旧写法）；
  - `plain`：`<plot>...</plot>`，直接把换行写入文本节点（与 `1111.nfo` 一致，
    已在 Jellyfin 12.0 验证，默认配置使用该值）。
  - 未配置该项时仍按 `cdata` 处理，保持旧行为。

### 变更

- **映射表更新流程**
  - 网络优先 → 本地回退 → 两者都没有则放弃使用映射表，刮削继续，不中断。
  - 下载改为先写入 `*.download` 临时文件，校验通过后再原子替换正式文件；
    下载或校验失败时保留本地旧文件，避免坏数据覆盖。
  - 过期文件改为直接覆盖下载，不再预先删除。
  - 本地映射表缺失或损坏时会打印提示；debug 模式下打印具体加载错误。
- **更新检查与映射表更新解耦**
  - `[update] update_check` 仅负责程序版本检查（`api.github.com`）。
  - `[mapping_table] update` 仅负责映射表下载/更新。
  - 版本检查失败不再阻断映射表更新。
- **剧情简介中日文判定**
  - `both_langs=1` 时改为按站点判定语言：
    `fanza` / `mgstage` 为日文，`airav` / `airavwiki` / `avno1` / `xcity` / `58avgo` 为中文；
    未知站点才退回内容检测。
  - 修复中文简介含日文假名时被误判为日文、导致中文简介被丢弃、分隔符不生效的问题。
- **`both_langs_sep` 解析**
  - 支持 `\n`、`\n\n`、`\t`、`\uXXXX` 等转义序列；
  - 非 ASCII 分隔符（如 `｜`）不再乱码；
  - 成对单/双引号自动去除；
  - `<br>` / `<br/>` / `br` 自动按真实换行处理（`.nfo` 的 `<plot>` 位于 CDATA 内，HTML 标签不会生效）。
  - Jellyfin 新版网页端（Markdown 渲染）推荐使用 `\n\n`。
- **DMM 高清封面日志**
  - `[priority] dmm_image` 跨源取图失败时输出具体原因：
    `HTTP 404`、`非图片(Content-Type=...)`、`文件过小(NNNB)`、
    `分辨率不足(宽147<700)`、`请求异常(...)`、`DMM 搜索失败: ...` 等。
  - 成功时输出来源开关与「原封面 → 高清封面」。
  - 日志与 `[storyline] debug` 无关，仅由 `[priority] dmm_image_debug` 控制。
- **失败列表与无法识别列表**
  - 抽出通用 `_write_list_separator()`。
  - `failed_list.txt` 与 `unrecognized_list.txt` 每次启动都会写入同格式的带时间分隔行。
  - 统一由 `[common] failed_list_separator` 控制。
- **`mapping_table_validity`** 缺失或非法时回退为 `7` 天，不再抛异常。
- 所有新增配置均有缺省值，旧配置文件无需修改即可运行。

### 修复

- 修复中文剧情简介因含假名被误判为日文而丢失的问题。
- 修复 `both_langs_sep` 在 `both_langs=1` 时未插入分隔符的问题。
- 修复 `<br>` 被当作普通文本显示、`.nfo` 中无法实现换行的问题。
- 修复映射表下载失败后本地旧文件已被删除、无法回退的问题。
- 修复版本检查失败导致映射表更新被跳过的问题。

### 配置兼容

映射表配置按以下顺序读取，旧配置无需迁移：

1. `[mapping_table]` 新段：`switch` / `validity` / `folder` / `update` / `url`
2. 旧 `[common]` 键：`mapping_table` / `mapping_table_validity` /
   `mapping_table_folder` / `mapping_table_update` / `mapping_table_url`
3. 更旧的 `[update] mapping_table_update`

### 其他

- `config.ini` 注释全面更新。
- `docker/config.ini` 同步新增 `[mapping_table]` 段。
- `config.py` 默认配置生成器同步。
- `scraper.py` 移除未使用的 `Path` 导入。
