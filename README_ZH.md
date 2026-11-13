<h1 align="center">Movie Data Capture</h1>

![](https://img.shields.io/badge/build-passing-brightgreen.svg?style=flat)
![](https://img.shields.io/github/license/767939287/Movie_data_capture.svg?style=flat)
![](https://img.shields.io/github/release/767939287/Movie_data_capture.svg?style=flat)
![](https://img.shields.io/badge/Python-3.12-yellow.svg?style=flat&logo=python)<br>
[Docker 版本](https://github.com/767939287/docker-mdc)  
![](https://img.shields.io/badge/build-passing-brightgreen.svg?style=flat)
![](https://img.shields.io/github/license/VergilGao/docker-mdc.svg?style=flat)
![](https://img.shields.io/github/release/VergilGao/docker-mdc.svg?style=flat)
![](https://img.shields.io/badge/Python-3.12-yellow.svg?style=flat&logo=python)<br>

**本地电影元数据 抓取工具 | 刮削器**，配合本地影片管理软件 Emby, Jellyfin, Kodi 等管理本地影片，该软件起到分类与元数据（metadata）抓取作用，利用元数据信息来分类，仅供本地影片分类整理使用。  
### 请勿在墙内的社交平台上宣传此项目

# 文档
* [官方教程WIKI](https://github.com/767939287/Movie_Data_Capture/wiki)
* [VergilGao's Docker部署](https://github.com/VergilGao/docker-mdc)

# Cloudflare 绕过（可选）
部分站点（如 JavDB、JavLibrary）受 Cloudflare 防护，直连会被拦截。可自建 [TRAWL](https://github.com/germondai/trawl) 或 [FlareSolverr](https://github.com/FlareSolverr/FlareSolverr) 外部服务，并在 `config.ini` 的 `[cloudflare]` 段开启：

```ini
[cloudflare]
switch = 1
service = flaresolverr   ; 或 trawl
host = http://127.0.0.1:8191
solver_first = 1
solver_reuse_ttl = 600
debug = 1                ; 打印 [CF] 日志，0=静默，留空则跟随 debug_mode
sites = javdb.com,javdbapi.com,javlibrary.com,jav321.com
```

* 默认 `switch = 0`（关闭），保持原有直连行为不变。
* `sites` 白名单内的域名始终走外部 CF 服务，不再先试直连。
* `solver_first = 1`（默认）：未列白名单、但**命中过 Cloudflare 挑战**的站点，后续请求也会自动跳过直连直接走 CF 服务（solver 失败才回退直连）。设 `0` 则只对白名单生效。
* `solver_reuse_ttl`：solver 成功后的 cookie 复用窗口（秒，默认 600）。窗口内该站点先直连并自动带上 solver 解出的 `cf_clearance` 等 cookie，直连又被拦才清窗口、下一次重新进 solver；设 `0` 则每个请求都走 solver。
* `debug`：`[CF]` 日志开关。`1` 打印 solver 调度/复用/重试等日志，`0` 全部静默（即使 `debug_mode` 开着也不打）；**留空或删除该键时跟随 `[debug_mode] switch`**。该开关不影响其它 debug 输出。
* 挑战识别按「403/503 + 挑战页特征」或响应头 `cf-mitigated: challenge`，不会把正常页误判为挑战。
* `service` 支持 `flaresolverr` 与 `trawl` 两种外部服务（TRAWL 亦兼容 FlareSolverr 协议）；TRAWL 原生 `/scrape` 请求会带 `sessionId=adc-{host}`，便于服务端按站点隔离会话。

# 申明
当你查阅、下载了本项目源代码或二进制程序，即代表你接受了以下条款
* 本项目和项目成果仅供技术，学术交流和Python3性能测试使用
* 本项目贡献者编写该项目旨在学习Python3 ，提高编程水平
* 本项目不提供任何影片下载的线索
* 用户在使用本项目和项目成果前，请用户了解并遵守当地法律法规，如果本项目及项目成果使用过程中存在违反当地法律法规的行为，请勿使用该项目及项目成果
* 法律后果及使用后果由使用者承担
* [GPL LICENSE](https://github.com/767939287/Movie_Data_Capture/blob/master/LICENSE)
* 若用户不同意上述条款任意一条，请勿使用本项目和项目成果

# 下载
* [Releases](https://github.com/767939287/Movie_Data_Capture/releases/latest)

# 贡献者
[![](https://opencollective.com/movie_data_capture/contributors.svg?width=890)](https://github.com/767939287/movie_data_Capture/graphs/contributors)

# Star History

[![Star History Chart](https://api.star-history.com/svg?repos=767939287/Movie_Data_Capture&type=Date)](https://star-history.com/#767939287/Movie_Data_Capture&Date)
