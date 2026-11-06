# -*- coding: utf-8 -*-
"""一键打包脚本：生成单文件 exe

用法（用本机 Python 3.12 跑，依赖装在它的环境里）：
    C:\\YuerHoo\\Python31210\\python.exe build.py

产物：dist\\微信聊天记录导出工具V1.0.0.exe

打包要点：
    --onefile    单文件：所有依赖压进一个 exe，运行时自解压到临时目录
    --windowed   GUI 程序，不带控制台窗口
    --add-data   把图标和两个 bootstrap 脚本放进包里
                 （bootstrap 脚本按数据文件放，运行时用 runpy 按脚本执行）
    --paths      PyInstaller 从上级仓库根找 find_all_keys / decrypt_db / config
    --hidden-import  这三个是运行时通过 import 动态用到的，静态分析看不到，必须显式声明

数据不随包发布：首次运行会在 exe 同级创建 WeChatData，
每个微信号在 accounts\\<wxid>\\ 下拥有独立配置与解密数据库。
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))     # wechat_exporter/
PROJ = os.path.dirname(HERE)                          # 仓库根（有 find_all_keys.py）
NAME = '微信聊天记录导出工具V1.0.0'


def main():
    """拼出 PyInstaller 命令行并同步执行

    不写 .spec 文件，全部参数都在这条命令里，好处是"改一行就能看出改了什么"；
    坏处是路径变了要同步改 PROJ/HERE 两处常量。
    """
    # --add-data 的分隔符在 Windows 上是分号
    dat = lambda src, dst: ['--add-data', src + os.pathsep + dst]
    cmd = [
        sys.executable, '-m', 'PyInstaller',
        '--noconfirm', '--clean',                 # 覆盖旧产物、清缓存
        '--onefile', '--windowed',
        '--name', NAME,
        '--icon', os.path.join(HERE, 'assets', 'app_icon.ico'),
        # 图标也要在运行时读（设置窗口图标用），所以单独作为数据文件再带一份
        *dat(os.path.join(HERE, 'assets', 'app_icon.ico'), 'assets'),
        # 两个子进程脚本：打包后由 exe 以 --bootstrap-* 参数执行
        *dat(os.path.join(HERE, '_bootstrap_keys.py'), '.'),
        *dat(os.path.join(HERE, '_bootstrap_decrypt.py'), '.'),
        '--paths', PROJ,
        '--hidden-import', 'find_all_keys',
        '--hidden-import', 'decrypt_db',
        '--hidden-import', 'config',
        # 中间产物/输出都放在本目录下，避免污染仓库根
        '--distpath', os.path.join(HERE, 'dist'),
        '--workpath', os.path.join(HERE, 'build'),
        '--specpath', os.path.join(HERE, 'build'),
        os.path.join(HERE, 'main.py'),
    ]
    print(' '.join(cmd))
    # cwd 设为本目录：PyInstaller 的临时目录默认建在当前盘，E 盘空间不足时要在此前切换
    return subprocess.call(cmd, cwd=HERE)


if __name__ == '__main__':
    sys.exit(main())
