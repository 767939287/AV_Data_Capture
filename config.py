# -*- coding: utf-8 -*-
import os
import re
import sys
import configparser
import time
import typing
from pathlib import Path
from urllib.parse import urlsplit

G_conf_override = {
    # index 0 save Config() first instance for quick access by using getInstance()
    0: None,
    # register override config items
    # no need anymore
}


def getInstance(path: str = None):
    if isinstance(G_conf_override[0], Config):
        return G_conf_override[0]
    return Config(path) if path else Config()


class Config:
    def __init__(self, path: str = ".vscode/config.ini"):
        path_search_order = (
            Path(path),
            Path.cwd() / ".vscode/config.ini",
            Path.cwd() / "config.ini",
            Path.home() / "mdc.ini",
            Path.home() / ".mdc.ini",
            Path.home() / ".mdc/config.ini",
            Path.home() / ".config/mdc/config.ini"
        )
        ini_path = None
        for p in path_search_order:
            if p.is_file():
                ini_path = p.resolve()
                break
        if ini_path:
            self.conf = configparser.ConfigParser()
            self.ini_path = ini_path
            try:
                if self.conf.read(ini_path, encoding="utf-8-sig"):
                    if G_conf_override[0] is None:
                        G_conf_override[0] = self
            except UnicodeDecodeError:
                if self.conf.read(ini_path, encoding="utf-8"):
                    if G_conf_override[0] is None:
                        G_conf_override[0] = self
            except Exception as e:
                print("ERROR: Config file can not read!")
                print("读取配置文件出错！")
                print('=================================')
                print(e)
                print("======= Auto exit in 60s ======== ")
                time.sleep(60)
                os._exit(-1)
        else:
            print("ERROR: Config file not found!")
            print("Please put config file into one of the following path:")
            print('\n'.join([str(p.resolve()) for p in path_search_order[1:]]))
            # 对于找不到配置文件的情况，还是在打包时附上对应版本的默认配置文件，有需要时为其在搜索路径中生成，
            # 要比用户乱找一个版本不对应的配置文件会可靠些。这样一来，单个执行文件就是功能完整的了，放在任何
            # 执行路径下都可以放心使用。
            res_path = None
            # pyinstaller打包的在打包中找config.ini
            if hasattr(sys, '_MEIPASS') and (Path(getattr(sys, '_MEIPASS')) / 'config.ini').is_file():
                res_path = Path(getattr(sys, '_MEIPASS')) / 'config.ini'
            # 脚本运行的所在位置找
            elif (Path(__file__).resolve().parent / 'config.ini').is_file():
                res_path = Path(__file__).resolve().parent / 'config.ini'
            if res_path is None:
                os._exit(2)
            ins = input("Or, Do you want me create a config file for you? (Yes/No)[Y]:")
            if re.search(r'n', ins, re.I):
                os._exit(2)
            # 用户目录才确定具有写权限，因此选择 ~/mdc.ini 作为配置文件生成路径，而不是有可能并没有写权限的
            # 当前目录。目前版本也不再鼓励使用当前路径放置配置文件了，只是作为多配置文件的切换技巧保留。
            write_path = path_search_order[2]  # Path.home() / "mdc.ini"
            write_path.write_text(res_path.read_text(encoding='utf-8'), encoding='utf-8')
            print("Config file '{}' created.".format(write_path.resolve()))
            input("Press Enter key exit...")
            os._exit(0)
            # self.conf = self._default_config()
            # try:
            #     self.conf = configparser.ConfigParser()
            #     try: # From single crawler debug use only
            #         self.conf.read('../' + path, encoding="utf-8-sig")
            #     except:
            #         self.conf.read('../' + path, encoding="utf-8")
            # except Exception as e:
            #     print("[-]Config file not found! Use the default settings")
            #     print("[-]",e)
            #     os._exit(3)
            #     #self.conf = self._default_config()

    def set_override(self, option_cmd: str):
        """
        通用的参数覆盖选项 -C 配置覆盖串
        配置覆盖串语法：小节名:键名=值[;[小节名:]键名=值][;[小节名:]键名+=值]  多个键用分号分隔 名称可省略部分尾部字符
        或 小节名:键名+=值[;[小节名:]键名=值][;[小节名:]键名+=值]  在已有值的末尾追加内容，多个键的=和+=可以交叉出现
        例子: face:aspect_ratio=2;aways_imagecut=1;priority:website=javdb
        小节名必须出现在开头至少一次，分号后可只出现键名=值，不再出现小节名，如果后续全部键名都属于同一个小节
        例如配置文件存在两个小节[proxy][priority]，那么pro可指代proxy，pri可指代priority
        [face]  ;face小节下方有4个键名locations_model= uncensored_only= aways_imagecut= aspect_ratio=
        l,lo,loc,loca,locat,locati...直到locations_model完整名称都可以用来指代locations_model=键名
        u,un,unc...直到uncensored_only完整名称都可以用来指代uncensored_only=键名
        aw,awa...直到aways_imagecut完整名称都可以用来指代aways_imagecut=键名
        as,asp...aspect_ratio完整名称都可以用来指代aspect_ratio=键名
        a则因为二义性，不是合法的省略键名
        """
        def err_exit(str):
            print(str)
            os._exit(2)

        sections = self.conf.sections()
        sec_name = None
        for cmd in option_cmd.split(';'):
            syntax_err = True
            rex = re.findall(r'^(.*?):(.*?)(=|\+=)(.*)$', cmd, re.U)
            if len(rex) and len(rex[0]) == 4:
                (sec, key, assign, val) = rex[0]
                sec_lo = sec.lower().strip()
                key_lo = key.lower().strip()
                syntax_err = False
            elif sec_name:  # 已经出现过一次小节名，属于同一个小节的后续键名可以省略小节名
                rex = re.findall(r'^(.*?)(=|\+=)(.*)$', cmd, re.U)
                if len(rex) and len(rex[0]) == 3:
                    (key, assign, val) = rex[0]
                    sec_lo = sec_name.lower()
                    key_lo = key.lower().strip()
                    syntax_err = False
            if syntax_err:
                err_exit(f"[-]Config override syntax incorrect. example: 'd:s=1' or 'debug_mode:switch=1'. cmd='{cmd}' all='{option_cmd}'")
            if not len(sec_lo):
                err_exit(f"[-]Config override Section name '{sec}' is empty! cmd='{cmd}'")
            if not len(key_lo):
                err_exit(f"[-]Config override Key name '{key}' is empty! cmd='{cmd}'")
            if not len(val.strip()):
                print(f"[!]Conig overide value '{val}' is empty! cmd='{cmd}'")
            sec_name = None
            for s in sections:
                if not s.lower().startswith(sec_lo):
                    continue
                if sec_name:
                    err_exit(f"[-]Conig overide Section short name '{sec_lo}' is not unique! dup1='{sec_name}' dup2='{s}' cmd='{cmd}'")
                sec_name = s
            if sec_name is None:
                err_exit(f"[-]Conig overide Section name '{sec}' not found! cmd='{cmd}'")
            key_name = None
            keys = self.conf[sec_name]
            for k in keys:
                if not k.lower().startswith(key_lo):
                    continue
                if key_name:
                    err_exit(f"[-]Conig overide Key short name '{key_lo}' is not unique! dup1='{key_name}' dup2='{k}' cmd='{cmd}'")
                key_name = k
            if key_name is None:
                err_exit(f"[-]Conig overide Key name '{key}' not found! cmd='{cmd}'")
            if assign == "+=":
                val = keys[key_name] + val
            if self.debug():
                print(f"[!]Set config override [{sec_name}]{key_name}={val}  by cmd='{cmd}'")
            self.conf.set(sec_name, key_name, val)

    def main_mode(self) -> int:
        try:
            return self.conf.getint("common", "main_mode")
        except ValueError:
            self._exit("common:main_mode")

    def source_folder(self) -> str:
        return self.conf.get("common", "source_folder").replace("\\\\", "/").replace("\\", "/")

    def failed_folder(self) -> str:
        return self.conf.get("common", "failed_output_folder").replace("\\\\", "/").replace("\\", "/")

    def success_folder(self) -> str:
        return self.conf.get("common", "success_output_folder").replace("\\\\", "/").replace("\\", "/")

    def actor_gender(self) -> str:
        return self.conf.get("common", "actor_gender")

    def link_mode(self) -> int:
        return self.conf.getint("common", "link_mode")

    def scan_hardlink(self) -> bool:
        # 未找到配置选项,默认不刮削
        return self.conf.getboolean("common", "scan_hardlink", fallback=False)

    def failed_move(self) -> bool:
        return self.conf.getboolean("common", "failed_move")

    def auto_exit(self) -> bool:
        return self.conf.getboolean("common", "auto_exit")

    def translate_to_sc(self) -> bool:
        return self.conf.getboolean("common", "translate_to_sc")

    def multi_threading(self) -> int:
        return self.conf.getint("common", "multi_threading")

    def del_empty_folder(self) -> bool:
        return self.conf.getboolean("common", "del_empty_folder")

    def nfo_skip_days(self) -> int:
        return self.conf.getint("common", "nfo_skip_days", fallback=30)

    def sort_order(self) -> str:
        """刮削顺序([common] sort_order)。

        控制扫描到的视频文件的处理先后顺序：
          - none(默认)  : 不排序，保持 Path.glob('**/*') 的原生遍历顺序(与升级前行为一致)；
          - name        : 按文件名(不含路径)自然排序(数字按数值大小，如 2<10)；
          - name_desc   : 按文件名自然排序的倒序；
          - mtime       : 按文件修改时间(旧→新)；
          - mtime_desc  : 按文件修改时间(新→旧)；
          - size        : 按文件大小(小→大)；
          - size_desc   : 按文件大小(大→小)；
          - path        : 按完整路径字符串排序；
          - path_desc   : 按完整路径字符串倒序。
        缺失或非法值一律按 none 处理，保证默认行为不变。
        """
        try:
            v = self.conf.get("common", "sort_order", fallback="none")
        except Exception:
            return "none"
        v = (v or "").strip().lower()
        valid = ("none", "name", "name_desc", "mtime", "mtime_desc",
                 "size", "size_desc", "path", "path_desc")
        return v if v in valid else "none"

    def skip_nfo_check_in_subtitle_mode(self) -> bool:
        """只下载字幕模式(main_mode=4)是否跳过 nfo_skip_days 检查。

        [common] skip_nfo_check_in_subtitle_mode:
          - 1（默认）：跳过 nfo_skip_days 检查，对源目录下所有视频都尝试下载字幕；
          - 0：执行 nfo_skip_days 检查，跳过最近 nfo_skip_days 天内修改过 .nfo 的视频。
        段/键缺失或非法时返回 True，保持默认行为（全部尝试下载字幕）。
        """
        try:
            return self.conf.getboolean("common", "skip_nfo_check_in_subtitle_mode", fallback=True)
        except Exception:
            return True

    def ignore_failed_list(self) -> bool:
        return self.conf.getboolean("common", "ignore_failed_list")

    def failed_list_separator(self) -> str:
        """每次刮削任务开始时写入列表文件的分隔行开关（[common] failed_list_separator）。

        默认 "===="。同时作用于 failed_list.txt 与 unrecognized_list.txt，
        用于把「每次运行记录的文件」分隔开，便于分格/分批任务识别批次。
        留空则关闭自动分隔（不写入任何分隔行）。
        仅当文件已存在且非空、且最后一行不是该分隔行时才会追加，避免文件开头出现孤立分隔行或连续重复。
        """
        try:
            v = self.conf.get("common", "failed_list_separator", fallback="====")
        except Exception:
            return "===="
        return v if isinstance(v, str) else "===="

    def _resolve_list_path(self, key: str, default_filename: str):
        """解析列表文件路径（[common] key）。

        规则（失败/成功列表通用）：
          - 留空：返回 None（由调用方决定各自默认：failed 沿用失败目录，success 表示不生成）；
          - 若为目录（无 .txt 后缀或以分隔符结尾）：返回 <目录>/<default_filename>；
          - 若以 .txt 结尾：视为完整文件路径，直接返回。
        """
        try:
            raw = self.conf.get("common", key, fallback="")
        except Exception:
            return None
        raw = (raw or "").strip().replace("\\\\", "/").replace("\\", "/")
        if not raw:
            return None
        if raw.lower().endswith(".txt"):
            return Path(raw)
        return Path(raw) / default_filename

    def failed_list_file(self) -> Path:
        """失败列表(failed_list.txt)的最终路径（[common] failed_list_path）。

        用于记录“刮削/下载过程中出错”的文件（有番号、有数据，但中途失败）。
        留空 → failed_folder/failed_list.txt（向后兼容）；
        填目录 → <目录>/failed_list.txt；填 *.txt 全路径 → 直接使用该文件。
        """
        p = self._resolve_list_path("failed_list_path", "failed_list.txt")
        if p is None:
            return Path(self.failed_folder()) / "failed_list.txt"
        return p

    def unrecognized_list_file(self) -> Path:
        """无法识别列表(unrecognized_list.txt)的最终路径（[common] unrecognized_list_path）。

        记录“没能进入正常刮削流程”的文件：番号识别失败(文件名提取不出番号) +
        数据源查无数据(not found)。与真正的刮削失败(failed_list)分开，便于人工排查番号。
        留空 → failed_folder/unrecognized_list.txt（向后兼容）；
        填目录 → <目录>/unrecognized_list.txt；填 *.txt 全路径 → 直接使用该文件。
        """
        p = self._resolve_list_path("unrecognized_list_path", "unrecognized_list.txt")
        if p is None:
            return Path(self.failed_folder()) / "unrecognized_list.txt"
        return p

    def download_only_missing_images(self) -> bool:
        return self.conf.getboolean("common", "download_only_missing_images")

    # ---------- 映射表配置读取 ----------
    # 优先新位置 [mapping_table] 段；找不到时回退旧位置（[common] / [update]），
    # 保证旧 config.ini 无需修改也能继续工作。
    def _mapping_first(self, candidates):
        """按顺序尝试 (section, key)，返回 (value_str, section, key)；都没有返回 (None, None, None)。"""
        for sec, key in candidates:
            try:
                if self.conf.has_option(sec, key):
                    return (self.conf.get(sec, key) or "").strip(), sec, key
            except Exception:
                continue
        return None, None, None

    def _mapping_bool(self, candidates, default=False) -> bool:
        v, sec, key = self._mapping_first(candidates)
        if v is None:
            return default
        try:
            return self.conf.getboolean(sec, key)
        except Exception:
            return default

    def _mapping_int(self, candidates, default=0) -> int:
        v, sec, key = self._mapping_first(candidates)
        if v is None:
            return default
        try:
            return self.conf.getint(sec, key)
        except Exception:
            return default

    def mapping_table(self) -> bool:
        """是否使用映射表的总开关（[mapping_table] switch）。

        0 = 完全不使用映射表（不读取、不下载，演员/标签/标题映射均不生效）；
        1 = 使用（默认）。
        兼容旧配置 [common] mapping_table；缺失/非法时返回 True。
        """
        return self._mapping_bool(
            [("mapping_table", "switch"), ("common", "mapping_table")], True)

    def mapping_table_validity(self) -> int:
        """映射表有效期(天)（[mapping_table] validity）。兼容旧键 [common] mapping_table_validity。"""
        v = self._mapping_int(
            [("mapping_table", "validity"), ("common", "mapping_table_validity")], 7)
        return v if v >= 0 else 7

    def mapping_table_folder(self) -> Path:
        """映射表(MappingTable)存放目录（[mapping_table] folder）。

        目录内包含 mapping_actor.xml / mapping_info.xml / c_number.json 三个文件。
        留空或缺失 → ~/.local/share/mdc（默认，Windows 即
        C:\\Users\\<用户名>\\.local\\share\\mdc）；
        填目录     → 使用该目录（支持相对/绝对路径与 ~）。
        兼容旧键 [common] mapping_table_folder。
        """
        raw, _sec, _key = self._mapping_first(
            [("mapping_table", "folder"), ("common", "mapping_table_folder")])
        if not raw:
            return Path.home() / '.local' / 'share' / 'mdc'
        raw = raw.replace("\\\\", "/").replace("\\", "/")
        return Path(raw).expanduser()

    def mapping_table_url(self) -> str:
        """映射表下载地址（[mapping_table] url）。

        留空或缺失 → 官方 GitHub raw 地址；
        填基础 URL → 程序在末尾拼上文件名（mapping_actor.xml 等），自动补 '/'；
        填含 {file} 的模板 → 直接把文件名替换进 {file}，适合自建镜像。
        兼容旧键 [common] mapping_table_url。
        """
        default = ("https://raw.githubusercontent.com/yoshiko2/Movie_Data_Capture/"
                   "master/MappingTable/")
        v, _sec, _key = self._mapping_first(
            [("mapping_table", "url"), ("common", "mapping_table_url")])
        return v or default

    def jellyfin(self) -> int:
        return self.conf.getint("common", "jellyfin")

    def actor_only_tag(self) -> bool:
        return self.conf.getboolean("common", "actor_only_tag")

    def sleep(self) -> int:
        return self.conf.getint("common", "sleep")

    def anonymous_fill(self) -> bool:
        return self.conf.getint("common", "anonymous_fill")

    def stop_counter(self) -> int:
        return self.conf.getint("advenced_sleep", "stop_counter", fallback=0)

    def rerun_delay(self) -> int:
        value = self.conf.get("advenced_sleep", "rerun_delay")
        if not (isinstance(value, str) and re.match(r'^[\dsmh]+$', value, re.I)):
            return 0  # not match '1h30m45s' or '30' or '1s2m1h4s5m'
        if value.isnumeric() and int(value) >= 0:
            return int(value)
        sec = 0
        sec += sum(int(v) for v in re.findall(r'(\d+)s', value, re.I))
        sec += sum(int(v) for v in re.findall(r'(\d+)m', value, re.I)) * 60
        sec += sum(int(v) for v in re.findall(r'(\d+)h', value, re.I)) * 3600
        return sec

    def is_translate(self) -> bool:
        return self.conf.getboolean("translate", "switch")

    def is_trailer(self) -> bool:
        return self.conf.getboolean("trailer", "switch")

    def is_watermark(self) -> bool:
        return self.conf.getboolean("watermark", "switch")

    def is_extrafanart(self) -> bool:
        return self.conf.getboolean("extrafanart", "switch")

    def extrafanart_thread_pool_download(self) -> int:
        try:
            v = self.conf.getint("extrafanart", "parallel_download")
            return v if v >= 0 else 5
        except:
            return 5

    def watermark_postion(self) -> int:
        return int(self.conf.get("watermark", "water"))

    def get_uncensored(self):
        try:
            sec = "uncensored"
            uncensored_prefix = self.conf.get(sec, "uncensored_prefix")
            # uncensored_poster = self.conf.get(sec, "uncensored_poster")
            return uncensored_prefix

        except ValueError:
            self._exit("uncensored")

    def get_extrafanart(self):
        try:
            extrafanart_download = self.conf.get("extrafanart", "extrafanart_folder")
            return extrafanart_download
        except ValueError:
            self._exit("extrafanart_folder")

    def get_translate_engine(self) -> str:
        return self.conf.get("translate", "engine")

    def get_target_language(self) -> str:
        return self.conf.get("translate", "target_language")

    # def get_translate_appId(self) ->str:
    #     return self.conf.get("translate","appid")

    def get_translate_key(self) -> str:
        return self.conf.get("translate", "key")

    def get_translate_delay(self) -> int:
        return self.conf.getint("translate", "delay")

    def translate_values(self) -> str:
        return self.conf.get("translate", "values")

    def get_translate_service_site(self) -> str:
        return self.conf.get("translate", "service_site")

    def proxy(self):
        try:
            sec = "proxy"
            switch = self.conf.get(sec, "switch")
            proxy = self.conf.get(sec, "proxy")
            timeout = self.conf.getint(sec, "timeout")
            retry = self.conf.getint(sec, "retry")
            proxytype = self.conf.get(sec, "type")
            iniProxy = IniProxy(switch, proxy, timeout, retry, proxytype)
            return iniProxy
        except ValueError:
            self._exit("common")

    def cacert_file(self) -> str:
        return self.conf.get('proxy', 'cacert_file')

    def media_type(self) -> str:
        return self.conf.get('media', 'media_type')

    def sub_rule(self) -> typing.Set[str]:
        return set(self.conf.get('media', 'sub_type').lower().split(','))

    def naming_rule(self) -> str:
        return self.conf.get("Name_Rule", "naming_rule")

    def location_rule(self) -> str:
        return self.conf.get("Name_Rule", "location_rule")

    def max_title_len(self) -> int:
        """
        Maximum title length
        """
        try:
            return self.conf.getint("Name_Rule", "max_title_len")
        except:
            return 50

    def image_naming_with_number(self) -> bool:
        try:
            return self.conf.getboolean("Name_Rule", "image_naming_with_number")
        except:
            return False

    def number_uppercase(self) -> bool:
        try:
            return self.conf.getboolean("Name_Rule", "number_uppercase")
        except:
            return False

    def number_regexs(self) -> str:
        try:
            return self.conf.get("Name_Rule", "number_regexs")
        except:
            return ""

    def update_check(self) -> bool:
        """程序版本检查开关（[update] update_check）。

        仅控制是否访问 api.github.com 检查新版本；与映射表下载完全解耦。
        [update] 段缺失/被注释或值非法时返回 False（不检查）。
        """
        try:
            return self.conf.getboolean("update", "update_check")
        except (ValueError, configparser.NoSectionError, configparser.NoOptionError):
            return False

    def mapping_table_update(self) -> bool:
        """映射表联网更新开关（[mapping_table] update）。

        仅控制是否根据 [mapping_table] url / folder 下载、更新映射表；与版本检查解耦。
        兼容旧位置 [common] mapping_table_update 与更旧的 [update] mapping_table_update，
        均未配置时回退 [update] update_check。
        总开关 [mapping_table] switch=0 时本项恒为 False。
        """
        if not self.mapping_table():
            return False
        v, sec, key = self._mapping_first([
            ("mapping_table", "update"),
            ("common", "mapping_table_update"),
            ("update", "mapping_table_update"),
        ])
        if v is not None:
            try:
                return self.conf.getboolean(sec, key)
            except Exception:
                pass
        return self.update_check()

    def sources(self) -> str:
        return self.conf.get("priority", "website")

    def priority_dmm_image(self) -> bool:
        """跨源 DMM 高清封面开关（位于 [priority] 段）。

        1 开启：即使用别的刮削器(javbus/javdb 等)，也按番号去 DMM 取高清封面替换；
        0 关闭（默认）：仅使用数据源自身封面。
        段/键缺失或非法时返回 False，保证默认行为不变。
        """
        try:
            return self.conf.getboolean("priority", "dmm_image", fallback=False)
        except Exception:
            return False

    def priority_dmm_image_debug(self) -> bool:
        """DMM 高清封面日志开关（位于 [priority] 段，键名 dmm_image_debug）。

        1 开启：打印 DMM 封面升级的成功结果与具体失败原因；
        0 关闭：静默（默认）。
        未配置该键时跟随 [debug_mode] switch，保持升级前的使用习惯。
        """
        try:
            return self.conf.getboolean("priority", "dmm_image_debug")
        except Exception:
            return self.debug()

    def priority_tag_source(self) -> str:
        """跨源标签来源刮削器（位于 [priority] 段，键名 tag_source）。

        设为某个刮削器名（如 javbus），则无论主数据源是什么，都会额外调用
        该刮削器按番号取标签(tag)，并与主源标签合并去重后使用。
        留空或缺失时禁用（默认），保证默认行为不变。
        多个来源可用逗号分隔，按顺序合并。
        """
        try:
            v = self.conf.get("priority", "tag_source", fallback="")
        except Exception:
            return ""
        return (v or "").strip()

    def priority_tag_mode(self) -> str:
        """跨源标签合并模式（位于 [priority] 段，键名 tag_mode）。

        merge（默认）：主源标签 + 跨源标签，去重合并；
        replace      ：仅用跨源标签替换主源标签（跨源为空时保留主源标签）。
        非法值按 merge 处理。
        """
        try:
            v = (self.conf.get("priority", "tag_mode", fallback="merge") or "").strip().lower()
        except Exception:
            return "merge"
        return v if v in ("merge", "replace") else "merge"

    def escape_literals(self) -> str:
        return self.conf.get("escape", "literals")

    def escape_folder(self) -> str:
        return self.conf.get("escape", "folders")

    def debug(self) -> bool:
        return self.conf.getboolean("debug_mode", "switch")

    def get_direct(self) -> bool:
        return self.conf.getboolean("direct", "switch",fallback=True)

    def dmm_image_switch(self) -> bool:
        """DMM/FANZA 高清图升级开关。

        [dmm_image] switch: 1 开启（尝试把低清封面/海报替换为 DMM 高清 CDN），
                            0 关闭（默认，行为与升级前完全一致）。
        段/键缺失或非法时返回 False，保证关闭时零副作用。
        """
        try:
            return self.conf.getboolean("dmm_image", "switch", fallback=False)
        except Exception:
            return False

    def cloudflare(self) -> "CloudflareConf":
        """ 读取 [cloudflare] 段配置（外部 Cloudflare 挑战绕过服务）

        :return: CloudflareConf 实例；缺失段/键或取值异常时回退到「已禁用」默认配置。

        采用 fallback 防御式读取，保证旧 config.ini 无 [cloudflare] 段时零副作用：
        switch=0 时 enable 为 False，程序行为与改动前完全一致。
        """
        try:
            sec = "cloudflare"
            switch = self.conf.getboolean(sec, "switch", fallback=False)
            service = self.conf.get(sec, "service", fallback="flaresolverr")
            host = self.conf.get(sec, "host", fallback=CloudflareConf.DEFAULT_HOST)
            timeout = self.conf.getint(sec, "timeout", fallback=CloudflareConf.DEFAULT_TIMEOUT)
            sites = self._parse_host_list(self.conf.get(sec, "sites", fallback=""))
            trusted = self._parse_host_list(self.conf.get(sec, "cf_trusted_hosts", fallback=""))
            return CloudflareConf(switch, service, host, timeout, sites, trusted)
        except Exception:
            # 任何读取异常都不应影响主流程，直接返回「已禁用」默认值
            return CloudflareConf(False, "flaresolverr", CloudflareConf.DEFAULT_HOST,
                                  CloudflareConf.DEFAULT_TIMEOUT, [], [])

    def javdb_channel(self) -> "JavdbChannelConf":
        """ 读取 [javdb_channel] 段（JavDB 三通道选择）

        :return: JavdbChannelConf 实例；段/键缺失或取值异常时回退 service='web' 默认配置

        采用 fallback 防御式读取，保证旧 config.ini 无 [javdb_channel] 段时零副作用：
        service='web' 时程序行为与升级前逐行等价。
        """
        def _split(raw):
            # 逗号分隔列表解析：保持原样（镜像地址不做小写化），并丢弃空白项
            return [x.strip() for x in str(raw or "").split(",") if x.strip()]
        try:
            sec = "javdb_channel"
            service = self.conf.get(sec, "service", fallback=JavdbChannelConf.DEFAULT_SERVICE)
            order = _split(self.conf.get(sec, "fallback_order", fallback=""))
            decrypt = self.conf.get(sec, "decrypt_app_image", fallback="1")
            mirrors = _split(self.conf.get(sec, "api_mirrors", fallback=""))
            cookie = self.conf.get(sec, "cookie", fallback="")
            return JavdbChannelConf(service, order, decrypt, mirrors, cookie)
        except Exception:
            # 任何读取异常都不应影响主流程，回退到与升级前一致的 web 通道
            return JavdbChannelConf(JavdbChannelConf.DEFAULT_SERVICE, [], "1", [], "")

    @staticmethod
    def _parse_host_list(raw: str) -> list:
        """ 把逗号分隔的域名串解析为去空白的小写列表

        :param raw: 原始字符串（如 javdb.com, javdbapi.com）
        :return: 清理后的域名列表（自动忽略空白项）
        """
        if not raw:
            return []
        return [item.strip().lower() for item in str(raw).split(",") if item.strip()]

    def dmm_api_switch(self) -> bool:
        """DMM 官方 Affiliate API 开关。

        [dmm_api] switch: 1 开启（fanza 源优先走官方 API，失败回退详情页），
                          0 关闭（默认，fanza 仅爬详情页）。
        段/键缺失或非法时返回 False，保证关闭时零副作用。
        """
        try:
            return self.conf.getboolean("dmm_api", "switch", fallback=False)
        except Exception:
            return False

    def dmm_api_id(self) -> str:
        """DMM Affiliate API 的 api_id（[dmm_api] api_id）。缺省时由 dmm_api.py 用默认值。"""
        try:
            return self.conf.get("dmm_api", "api_id", fallback="")
        except Exception:
            return ""

    def dmm_affiliate_id(self) -> str:
        """DMM Affiliate API 的 affiliate_id（[dmm_api] affiliate_id）。缺省时用默认值。"""
        try:
            return self.conf.get("dmm_api", "affiliate_id", fallback="")
        except Exception:
            return ""

    def is_storyline(self) -> bool:
        try:
            return self.conf.getboolean("storyline", "switch")
        except:
            return True

    def debug_storyline(self) -> bool:
        return self.conf.getboolean("storyline", "debug")

    def storyline_site(self) -> str:
        try:
            return self.conf.get("storyline", "site")
        except:
            return "fanza,avno1,airavwiki"

    def storyline_timeout(self) -> dict:
        """各剧情简介站点的请求超时(秒)。[storyline] timeout。

        写法（逗号分隔，单位秒，仅接受正整数）：
          - 直接写数字:          timeout = 15              # 所有站点统一 15 秒(存入 'default')
          - 单独指定某站点:       timeout = fanza=20,airav=10 # 只覆盖列出的站点
          - 数字 + 单站点混写:    timeout = 15,fanza=20      # 默认 15，fanza 单独 20
        说明：不带 '=' 的纯数字视为所有站点的默认超时(键名 'default')，无需写 default=。
        未配置 timeout 项时返回空 dict（各站点使用代码内默认超时，行为与升级前一致）。
        返回值示例: {'default': 15, 'fanza': 20, 'airav': 10}
        """
        result = {}
        try:
            raw = self.conf.get("storyline", "timeout", fallback="")
        except Exception:
            return result
        if not isinstance(raw, str) or not raw.strip():
            return result
        for item in raw.split(","):
            item = item.strip()
            if not item:
                continue
            if "=" not in item:
                # 纯数字 → 所有站点的默认超时
                if item.isdigit() and int(item) > 0:
                    result["default"] = int(item)
                continue
            site, _, val = item.partition("=")
            site = site.strip().lower()
            val = val.strip()
            if site and val.isdigit() and int(val) > 0:
                result[site] = int(val)
        return result

    def storyline_censored_site(self) -> str:
        try:
            return self.conf.get("storyline", "censored_site")
        except:
            return "fanza,airav,xcity"

    def storyline_uncensored_site(self) -> str:
        try:
            return self.conf.get("storyline", "uncensored_site")
        except:
            return "58avgo"

    def storyline_show(self) -> int:
        v = self.conf.getint("storyline", "show_result", fallback=0)
        return v if v in (0, 1, 2) else 2 if v > 2 else 0

    def storyline_mode(self) -> int:
        return 1 if self.conf.getint("storyline", "run_mode", fallback=1) > 0 else 0

    def storyline_prefer_jp(self) -> bool:
        """是否优先使用靠前站点(如fanza)的日文剧情简介"""
        try:
            return self.conf.getboolean("storyline", "prefer_jp")
        except:
            return False

    def storyline_both_langs(self) -> bool:
        """是否同时保留中文与日文剧情简介（[storyline] both_langs）。

        0（默认）：沿用原行为，只按 prefer_jp 规则选取**一个**站点的简介；
        1         ：同时保留“最佳日文简介”和“最佳中文简介”，两者拼接成一个 outline
                    （日文在前、中文在后；若某种语言缺失则只返回另一种）。
        段/键缺失或非法时返回 False，保证默认行为与升级前完全一致。
        """
        try:
            return self.conf.getboolean("storyline", "both_langs", fallback=False)
        except Exception:
            return False

    def storyline_both_langs_sep(self) -> str:
        """日文与中文剧情简介拼接时的分隔符（[storyline] both_langs_sep）。

        默认使用换行符 '\\n'。仅当 both_langs=1 时生效。
        支持在 ini 中原样书写转义序列（如 \\n 表示换行，\\t 表示制表符），程序会将其解释为真实字符。

        关于 <br>：.nfo 的 <plot> 写在 CDATA 中，且元数据里的 '<'/'>' 会被
        special_characters_replacement() 转义，HTML 标签不可能生效(前端只会显示字面文本)，
        因此这里把 <br> / <br/> / <br /> / br 统一当作真实换行处理，避免用户踩坑。

        注意：configparser 不解析引号，`both_langs_sep = "\\n"` 的值会带引号；
        为容错，本项会自动去掉成对的单/双引号。
        """
        try:
            v = self.conf.get("storyline", "both_langs_sep", fallback="\\n")
        except Exception:
            v = "\\n"
        if not isinstance(v, str):
            v = "\\n"
        # configparser 不会解析引号：写成 both_langs_sep = "\\n" 时，值里会包含引号本身。
        # 这里做容错：若整个值被成对的单/双引号包住，自动去掉这对引号。
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in ('"', "'"):
            v = v[1:-1]
        # HTML 换行标签在 CDATA 中不会生效，等价映射为真实换行
        if v.strip().lower() in ("<br>", "<br/>", "<br />", "br"):
            v = "\\n"
        # 支持用户在 ini 中书写 \n / \t / \u2028 等转义序列；非法转义时原样返回。
        # 注意：不能直接用 utf-8 编码后 unicode_escape 解码，那样非 ASCII 字符
        # (如 ｜ U+FF5C) 会按 latin-1 误解码成乱码；
        # backslashreplace 会把非 latin-1 字符转成 \\uXXXX，再由 unicode_escape 还原。
        try:
            return v.encode('latin-1', 'backslashreplace').decode('unicode_escape')
        except Exception:
            return v

    def cc_convert_mode(self) -> int:
        v = self.conf.getint("cc_convert", "mode", fallback=1)
        return v if v in (0, 1, 2) else 2 if v > 2 else 0

    def cc_convert_vars(self) -> str:
        return self.conf.get("cc_convert", "vars",
            fallback="actor,director,label,outline,series,studio,tag,title")

    def javdb_sites(self) -> str:
        return self.conf.get("javdb", "sites", fallback="38,39")

    def javlibrary_domain(self) -> str:
        """javlibrary 主页/CDN 域名（[javlibrary] domain）。

        默认 https://www.javlibrary.com；镜像失效时可随时改为主站或当前镜像，
        例如 https://www.a95i.com。返回值去掉末尾斜杠。
        """
        try:
            v = self.conf.get("javlibrary", "domain", fallback="")
        except Exception:
            v = ""
        v = (v or "").strip().rstrip("/")
        return v or "https://www.javlibrary.com"

    def javbus_domain(self) -> str:
        """javbus 主页/CDN 域名（[javbus] domain）。

        默认 https://www.javbus.com；镜像失效时可随时改为当前可用镜像。
        返回值去掉末尾斜杠。
        """
        try:
            v = self.conf.get("javbus", "domain", fallback="")
        except Exception:
            v = ""
        v = (v or "").strip().rstrip("/")
        return v or "https://www.javbus.com"

    def javbus_mirror(self) -> str:
        """javbus 备用镜像域名（[javbus] mirror）。

        当主站(domain)请求异常时使用的兜底镜像，默认 https://www.buscdn.casa。
        返回值去掉末尾斜杠；留空则回退默认镜像。
        """
        try:
            v = self.conf.get("javbus", "mirror", fallback="")
        except Exception:
            v = ""
        v = (v or "").strip().rstrip("/")
        return v or "https://www.buscdn.casa"

    def face_locations_model(self) -> str:
        return self.conf.get("face", "locations_model", fallback="hog")

    def face_uncensored_only(self) -> bool:
        return self.conf.getboolean("face", "uncensored_only", fallback=True)

    def face_aways_imagecut(self) -> bool:
        return self.conf.getboolean("face", "aways_imagecut", fallback=False)

    def face_aspect_ratio(self) -> float:
        return self.conf.getfloat("face", "aspect_ratio", fallback=2.12)

    def jellyfin_multi_part_fanart(self) -> bool:
        return self.conf.getboolean("jellyfin", "multi_part_fanart", fallback=False)

    def download_actor_photo_for_kodi(self) -> bool:
        return self.conf.getboolean("actor_photo", "download_for_kodi", fallback=False)

    @staticmethod
    def _exit(sec: str) -> None:
        print("[-] Read config error! Please check the {} section in config.ini", sec)
        input("[-] Press ENTER key to exit.")
        exit()

    @staticmethod
    def _default_config() -> configparser.ConfigParser:
        conf = configparser.ConfigParser()

        sec1 = "common"
        conf.add_section(sec1)
        conf.set(sec1, "main_mode", "1")
        conf.set(sec1, "source_folder", "./")
        conf.set(sec1, "failed_output_folder", "failed")
        conf.set(sec1, "success_output_folder", "JAV_output")
        conf.set(sec1, "link_mode", "0")
        conf.set(sec1, "scan_hardlink", "0")
        conf.set(sec1, "failed_move", "1")
        conf.set(sec1, "auto_exit", "0")
        conf.set(sec1, "translate_to_sc", "1")
        # actor_gender value: female or male or both or all(含人妖)
        conf.set(sec1, "actor_gender", "female")
        conf.set(sec1, "del_empty_folder", "1")
        conf.set(sec1, "nfo_skip_days", "30")
        conf.set(sec1, "skip_nfo_check_in_subtitle_mode", "1")
        conf.set(sec1, "ignore_failed_list", "0")
        conf.set(sec1, "failed_list_path", "")
        conf.set(sec1, "unrecognized_list_path", "")
        conf.set(sec1, "download_only_missing_images", "1")
        conf.set(sec1, "jellyfin", "0")
        conf.set(sec1, "actor_only_tag", "0")
        conf.set(sec1, "sleep", "3")
        conf.set(sec1, "anonymous_fill", "0")

        sec_mapping = "mapping_table"
        conf.add_section(sec_mapping)
        conf.set(sec_mapping, "switch", "1")
        conf.set(sec_mapping, "validity", "7")
        conf.set(sec_mapping, "folder", "")
        conf.set(sec_mapping, "update", "1")
        conf.set(sec_mapping, "url", "")

        sec2 = "advenced_sleep"
        conf.add_section(sec2)
        conf.set(sec2, "stop_counter", "0")
        conf.set(sec2, "rerun_delay", "0")

        sec3 = "proxy"
        conf.add_section(sec3)
        conf.set(sec3, "proxy", "")
        conf.set(sec3, "timeout", "5")
        conf.set(sec3, "retry", "3")
        conf.set(sec3, "type", "socks5")
        conf.set(sec3, "cacert_file", "")

        sec4 = "Name_Rule"
        conf.add_section(sec4)
        conf.set(sec4, "location_rule", "actor + '/' + number")
        conf.set(sec4, "naming_rule", "number + '-' + title")
        conf.set(sec4, "max_title_len", "50")
        conf.set(sec4, "image_naming_with_number", "0")
        conf.set(sec4, "number_uppercase", "0")
        conf.set(sec4, "number_regexs", "")

        sec5 = "update"
        conf.add_section(sec5)
        conf.set(sec5, "update_check", "1")

        sec6 = "priority"
        conf.add_section(sec6)
        conf.set(sec6, "website", "airav,javbus,javdb,fanza,xcity,mgstage,fc2,fc2club,avsox,jav321,xcity")
        conf.set(sec6, "dmm_image", "0")
        conf.set(sec6, "dmm_image_debug", "0")
        conf.set(sec6, "tag_source", "")
        conf.set(sec6, "tag_mode", "merge")

        sec7 = "escape"
        conf.add_section(sec7)
        conf.set(sec7, "literals", r"\()/")  # noqa
        conf.set(sec7, "folders", "failed, JAV_output")

        sec8 = "debug_mode"
        conf.add_section(sec8)
        conf.set(sec8, "switch", "0")

        sec9 = "translate"
        conf.add_section(sec9)
        conf.set(sec9, "switch", "0")
        conf.set(sec9, "engine", "google-free")
        conf.set(sec9, "target_language", "zh_cn")
        # conf.set(sec8, "appid", "")
        conf.set(sec9, "key", "")
        conf.set(sec9, "delay", "1")
        conf.set(sec9, "values", "title,outline")
        conf.set(sec9, "service_site", "translate.google.cn")

        sec10 = "trailer"
        conf.add_section(sec10)
        conf.set(sec10, "switch", "0")

        sec11 = "uncensored"
        conf.add_section(sec11)
        conf.set(sec11, "uncensored_prefix", "S2M,BT,LAF,SMD")

        sec12 = "media"
        conf.add_section(sec12)
        conf.set(sec12, "media_type",
                 ".mp4,.avi,.rmvb,.wmv,.mov,.mkv,.flv,.ts,.webm,iso")
        conf.set(sec12, "sub_type",
                 ".smi,.srt,.idx,.sub,.sup,.psb,.ssa,.ass,.usf,.xss,.ssf,.rt,.lrc,.sbv,.vtt,.ttml")

        sec13 = "watermark"
        conf.add_section(sec13)
        conf.set(sec13, "switch", "1")
        conf.set(sec13, "water", "2")

        sec14 = "extrafanart"
        conf.add_section(sec14)
        conf.set(sec14, "switch", "1")
        conf.set(sec14, "extrafanart_folder", "extrafanart")
        conf.set(sec14, "parallel_download", "1")

        sec15 = "storyline"
        conf.add_section(sec15)
        conf.set(sec15, "switch", "1")
        conf.set(sec15, "site", "fanza,avno1,airavwiki")
        conf.set(sec15, "censored_site", "fanza,airav,xcity")
        conf.set(sec15, "uncensored_site", "58avgo")
        conf.set(sec15, "prefer_jp", "1")
        conf.set(sec15, "both_langs", "0")
        conf.set(sec15, "both_langs_sep", "\\n")
        conf.set(sec15, "show_result", "0")
        conf.set(sec15, "run_mode", "1")
        conf.set(sec15, "cc_convert", "1")

        sec16 = "cc_convert"
        conf.add_section(sec16)
        conf.set(sec16, "mode", "1")
        conf.set(sec16, "vars", "actor,director,label,outline,series,studio,tag,title")

        sec17 = "javdb"
        conf.add_section(sec17)
        conf.set(sec17, "sites", "33,34")

        sec18 = "face"
        conf.add_section(sec18)
        conf.set(sec18, "locations_model", "hog")
        conf.set(sec18, "uncensored_only", "1")
        conf.set(sec18, "aways_imagecut", "0")
        conf.set(sec18, "aspect_ratio", "2.12")

        sec19 = "jellyfin"
        conf.add_section(sec19)
        conf.set(sec19, "multi_part_fanart", "0")

        sec20 = "actor_photo"
        conf.add_section(sec20)
        conf.set(sec20, "download_for_kodi", "0")

        return conf


class IniProxy():
    """ Proxy Config from .ini
    """
    SUPPORT_PROXY_TYPE = ("http", "socks5", "socks5h")

    enable = False
    address = ""
    timeout = 5
    retry = 3
    proxytype = "socks5"

    def __init__(self, switch, address, timeout, retry, proxytype) -> None:
        """ Initial Proxy from .ini
        """
        if switch == '1' or switch == 1:
            self.enable = True
        self.address = address
        self.timeout = timeout
        self.retry = retry
        self.proxytype = proxytype

    def proxies(self):
        """
        获得代理参数，默认http代理
        get proxy params, use http proxy for default
        """
        if self.address:
            if self.proxytype in self.SUPPORT_PROXY_TYPE:
                proxies = {"http": self.proxytype + "://" + self.address,
                           "https": self.proxytype + "://" + self.address}
            else:
                proxies = {"http": "http://" + self.address, "https": "https://" + self.address}
        else:
            proxies = {}

        return proxies


class CloudflareConf():
    """ [cloudflare] 段配置聚合对象

    封装外部 Cloudflare 挑战绕过服务（TRAWL / FlareSolverr）的开关、服务类型、
    服务地址、超时与站点白名单，并提供白名单命中判断。
    字段缺失或取值异常时一律回退到「关闭」的安全默认值，保证零副作用。
    """

    DEFAULT_HOST = "http://127.0.0.1:8191"
    DEFAULT_TIMEOUT = 60

    def __init__(self, switch, service: str, host: str, timeout: int,
                 sites: list, trusted_hosts: list):
        """ 保存原始配置值

        :param switch: 原始开关值（'0'/'1'/0/1 等）
        :param service: 服务类型原始字符串
        :param host: 服务地址原始字符串
        :param timeout: 超时秒数
        :param sites: 生效站点 host 列表（已小写、去空白）
        :param trusted_hosts: 落地域名校验列表
        """
        # switch 兼容 bool（getboolean）与 '0'/'1' 字符串两种来源
        self.enable = switch == '1' or switch == 1
        # service 归一化：仅识别 trawl，其余（含空值）一律按 flaresolverr 处理
        service_lo = (service or "").strip().lower()
        self.service = "trawl" if service_lo == "trawl" else "flaresolverr"
        # host 归一化：去尾部斜杠；缺少协议时强制补 http://（回环地址用 https 会 TLS 握手失败）
        host_str = (host or "").strip().rstrip("/")
        if host_str and "://" not in host_str:
            host_str = "http://" + host_str
        self.host = host_str or self.DEFAULT_HOST
        try:
            self.timeout = int(timeout)
        except (TypeError, ValueError):
            self.timeout = self.DEFAULT_TIMEOUT
        if self.timeout <= 0:
            self.timeout = self.DEFAULT_TIMEOUT
        self.sites = list(sites or [])
        self.trusted_hosts = list(trusted_hosts or [])

    def is_whitelisted(self, url: str) -> bool:
        """ 判断 URL 的 host 是否命中生效站点白名单

        :param url: 待判断的请求 URL
        :return: 命中白名单返回 True，否则 False

        匹配规则：取 URL 的小写 hostname，与白名单项做「精确相等」或
        「后缀匹配(.域名)」判断，使白名单填 javdb.com 时也能命中 www.javdb.com。
        """
        if not url or not self.sites:
            return False
        try:
            hostname = (urlsplit(str(url)).hostname or "").lower()
        except Exception:
            return False
        if not hostname:
            return False
        for site in self.sites:
            # 白名单项允许带端口，比较时只取主机名部分
            site_host = site.split(":")[0].strip().lower()
            if not site_host:
                continue
            if hostname == site_host or hostname.endswith("." + site_host):
                return True
        return False


class JavdbChannelConf():
    """ [javdb_channel] 段配置聚合对象

    决定 JavDB 刮削器使用哪条数据通道（web/api/app/auto）以及 auto 模式下的降级顺序。
    段/键缺失时回退到 service='web'，保证默认行为与升级前完全一致。
    """

    SUPPORT_SERVICE = ("web", "api", "app", "auto")
    DEFAULT_SERVICE = "web"
    DEFAULT_ORDER = ("app", "api", "web")
    DEFAULT_MIRRORS = (
        "https://javdb573.com",
        "https://javdb574.com",
        "https://javdb575.com",
    )

    def __init__(self, service: str, fallback_order: list, decrypt_app_image, mirrors: list, cookie: str):
        """ 保存并归一化配置值

        :param service: 通道名原始字符串（web/api/app/auto），非法值回退 web
        :param fallback_order: auto 模式的尝试顺序列表
        :param decrypt_app_image: 是否解密 App CDN 图片（兼容 '0'/'1'/bool）
        :param mirrors: api 通道镜像站列表（为空时使用内置默认）
        :param cookie: 附加 Cookie 字符串（可为空）
        """
        service_lo = (service or "").strip().lower()
        self.service = service_lo if service_lo in self.SUPPORT_SERVICE else self.DEFAULT_SERVICE
        # fallback_order 过滤非法通道并保序去重；结果为空时用默认顺序
        order = []
        for item in (fallback_order or []):
            name = str(item).strip().lower()
            if name in ("web", "api", "app") and name not in order:
                order.append(name)
        self.fallback_order = order or list(self.DEFAULT_ORDER)
        # decrypt_app_image 兼容 '0'/'1'/0/1/False/True 等多种来源
        self.decrypt_app_image = not (decrypt_app_image in ('0', 0, False, 'false', 'False'))
        self.mirrors = [m.rstrip('/') for m in (mirrors or []) if str(m).strip()] or list(self.DEFAULT_MIRRORS)
        self.cookie = (cookie or "").strip()

    def channel_list(self) -> list:
        """ 返回需要依次尝试的通道名列表

        :return: service 非 auto 时仅返回该通道；auto 时返回 fallback_order 副本
        """
        if self.service == "auto":
            return list(self.fallback_order)
        return [self.service]


if __name__ == "__main__":
    def evprint(evstr):
        code = compile(evstr, "<string>", "eval")
        print('{}: "{}"'.format(evstr, eval(code)))

    config = Config()
    mfilter = {'conf', 'proxy', '_exit', '_default_config', 'ini_path', 'set_override'}
    for _m in [m for m in dir(config) if not m.startswith('__') and m not in mfilter]:
        evprint(f'config.{_m}()')
    pfilter = {'proxies', 'SUPPORT_PROXY_TYPE'}
    # test getInstance()
    assert (getInstance() == config)
    for _p in [p for p in dir(getInstance().proxy()) if not p.startswith('__') and p not in pfilter]:
        evprint(f'getInstance().proxy().{_p}')

    # Create new instance
    conf2 = Config()
    assert getInstance() != conf2
    assert getInstance() == config

    conf2.set_override("d:s=1;face:asp=2;f:aw=0;pri:w=javdb;f:l=")
    assert conf2.face_aspect_ratio() == 2
    assert conf2.face_aways_imagecut() == False
    assert conf2.sources() == "javdb"
    print(f"Load Config file '{conf2.ini_path}'.")