# -*- coding: utf-8 -*-
"""全局路径与偏好配置（整个程序唯一的"路径真相来源"）

一、两种运行形态的路径差异
    · 源码运行：ROOT = 本文件所在目录（wechat_exporter/），代码就在这个目录下；
    · PyInstaller 单文件打包：sys.frozen = True，代码被解到临时目录（sys._MEIPASS），
      而"可写数据"必须放在 exe 同级目录，否则程序一关数据就没了。
      所以这里把两类路径严格分开：
        BUNDLE_DIR  —— 只读资源根（图标、_bootstrap_*.py 等随包文件）
        ROOT        —— 可写数据根（WeChatData、导出目录都挂在这下面）

二、磁盘布局
    <ROOT>/                                  源码=wechat_exporter，打包=exe 同级
      WeChatData/                            程序自己的数据（不是微信的原始数据）
        accounts/
          <wxid>/                            一个微信号一个独立文件夹
            account.json                     账号配置（名称 / 微信数据目录）
            keys.json                        密钥缓存（避免每次重新提权提取）
            manifest.json                    增量解密清单（mtime/size/salt）
            counts.json                      每个会话的消息数缓存
            sync.log / keys.log / decrypt.log  三类子进程日志
            db/                              解密后的明文数据库（contact/ message/ session/ ...）
      微信聊天记录导出目录/                   所有导出的 xlsx 都放在这一层，不再分子文件夹

三、可写性兜底
    exe 若被放到 Program Files 这类无写权限的位置，ROOT 下建目录会失败，
    此时自动退化到 %LOCALAPPDATA%\\WeChatData，保证程序仍然可用。
"""
import json
import os
import sys

from PyQt5.QtCore import QSettings

# 是否处于 PyInstaller 打包环境
FROZEN = bool(getattr(sys, 'frozen', False))
# 只读资源根：打包后是解压出来的临时目录 sys._MEIPASS，源码运行是脚本目录
BUNDLE_DIR = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
# 可写数据根：打包后是 exe 所在目录，源码运行是脚本目录
ROOT = (os.path.dirname(os.path.abspath(sys.executable)) if FROZEN
        else os.path.dirname(os.path.abspath(__file__)))
# 仓库根（只有源码运行时才有意义，用于读取根目录的 config.json；打包后退化为 ROOT）
PROJECT_ROOT = ROOT if FROZEN else os.path.dirname(ROOT)


def _pick_data_root():
    """挑选可写的数据根目录：优先 ROOT/WeChatData，不可写则退到 %LOCALAPPDATA%

    注意：这里用"实际建一次目录"来试探可写性，比判断权限位可靠。
    """
    for cand in (os.path.join(ROOT, 'WeChatData'),
                 os.path.join(os.environ.get('LOCALAPPDATA') or
                              os.path.expanduser('~'), 'WeChatData')):
        try:
            os.makedirs(cand, exist_ok=True)
            return cand
        except Exception:
            continue
    # 两个位置都建不出来时仍返回首选路径，让后续操作自然报错，便于定位问题
    return os.path.join(ROOT, 'WeChatData')


# 程序数据根（账号配置 / 密钥 / 解密库都在下面）
DATA_ROOT = _pick_data_root()
# 所有微信号的父目录
ACCOUNTS_ROOT = os.path.join(DATA_ROOT, 'accounts')

# ---------------------------------------------------------------------------
# 程序名：窗口标题、任务栏、以及导出表格第 3 行的「导出工具」都取这一个值，
# 以后改名只改这里，不用到处找
# ---------------------------------------------------------------------------
APP_NAME = '微信聊天记录导出工具V1.0.0'

# ---------------------------------------------------------------------------
# 导出目录：程序里只有这一个导出位置，任何模式下生成的 xlsx 都直接落在这一层
# （不按账号、不按导出批次建子文件夹；同名文件直接覆盖）
# ---------------------------------------------------------------------------
EXPORT_DIR_NAME = '微信聊天记录导出目录'
DEFAULT_EXPORT_DIR = os.path.join(ROOT, EXPORT_DIR_NAME)


def export_dir():
    """返回导出目录的绝对路径，并保证它存在（每次导出前调用，避免目录被手动删掉）"""
    try:
        os.makedirs(DEFAULT_EXPORT_DIR, exist_ok=True)
    except Exception:
        # 实在建不出来就让上层写文件时报错，错误信息里会带上完整路径
        pass
    return DEFAULT_EXPORT_DIR


def default_db_dir():
    """首次运行时尝试从仓库根的 config.json 猜微信数据目录（只有源码模式才有该文件）"""
    try:
        with open(os.path.join(PROJECT_ROOT, 'config.json'), encoding='utf-8') as f:
            db = json.load(f).get('db_dir', '')
        return db if db and os.path.isdir(db) else ''
    except Exception:
        return ''


class Prefs(object):
    """QSettings 的极薄封装（Windows 下落在注册表 HKCU\\Software\\WeChatExport）

    这里只放"界面/导出偏好"这类零散小配置；
    账号、密钥这类结构化数据一律写 JSON 文件（见 core/accounts.py），
    因为需要跟着账号目录一起被删除/备份。
    """

    def __init__(self):
        """固定用同一个 org/app 名打开 QSettings

        名字必须写死：一旦改动，用户之前的界面偏好（上次选的 Tab、导出模式、
        窗口大小…）就会全部读不到，表现成"设置每次都重置"。
        """
        self.qs = QSettings('WeChatExport', 'WeChatExport')

    def get(self, key, default=None):
        """读配置。注意 QSettings 从注册表读回来全是字符串，
        所以当调用方声明的默认值是 bool 时，这里要做一次字符串→bool 的还原。"""
        v = self.qs.value(key)
        if v is None:
            return default
        if isinstance(default, bool) and isinstance(v, str):
            return v.lower() in ('true', '1', 'yes')
        return v

    def set(self, key, value):
        """写配置（key 用 '分区/名字' 的形式，例如 'account/current'）"""
        self.qs.setValue(key, value)
