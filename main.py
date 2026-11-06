# -*- coding: utf-8 -*-
"""微信聊天记录导出工具 · 程序入口

三种启动方式：
    1) 正常启动（双击 exe / python main.py）→ 打开图形界面
    2) 程序.exe --bootstrap-keys    <db_dir> <out_keys.json> <log>
       UAC 提权后由本程序自己执行"扫内存提取密钥"（打包后没有 python 解释器可用了）
    3) 程序.exe --bootstrap-decrypt <db_dir> <out_dir> <keys> <manifest> <log>
       普通权限执行"增量解密"

打包后没有控制台，出错只能靠文件排查：
    设环境变量 WECHAT_EXPORT_TRACE=1 会写 trace.log（启动阶段的关键节点）
    未捕获异常一律追加到 crash.log
"""
import os
import sys
import traceback

# 打包后 sys.executable 才是 exe 路径；源码运行用脚本目录
FROZEN = bool(getattr(sys, 'frozen', False))
ROOT = (os.path.dirname(os.path.abspath(sys.executable)) if FROZEN
        else os.path.dirname(os.path.abspath(__file__)))
# 随包资源目录（打包后是解压临时目录）
BUNDLE = getattr(sys, '_MEIPASS', ROOT)
sys.path.insert(0, ROOT)      # 让 core/ ui/ app_config 能被 import


def _run_bootstrap(mode, args):
    """bootstrap 模式：直接把控制权交给 _bootstrap_*.py 脚本

    用 runpy 而不是 import：脚本里依赖 sys.argv 传参、并且结尾会 sys.exit()，
    按脚本方式执行行为才一致。

    关键点：仓库根的 config.py 在被 import 时会 load_config()，找不到 config.json
    就 sys.exit(1)。打包环境里根本没有仓库根，所以这里先塞一个 stub 模块顶替它。
    """
    import runpy
    import types

    if mode == 'keys':
        script, db_dir = '_bootstrap_keys.py', args[0]
        keys_file, dec_dir = args[1], ''
    else:
        script, db_dir = '_bootstrap_decrypt.py', args[0]
        keys_file, dec_dir = args[2], args[1]

    stub = types.ModuleType('config')
    stub.load_config = lambda: {
        'db_dir': db_dir, 'keys_file': keys_file, 'decrypted_dir': dec_dir,
        'decoded_image_dir': '', 'wechat_process': 'Weixin.exe',
    }
    sys.modules['config'] = stub

    path = os.path.join(BUNDLE, script)
    sys.argv = [path] + list(args)
    runpy.run_path(path, run_name='__main__')


def _trace(msg):
    """打包后无控制台，排查启动问题时设 WECHAT_EXPORT_TRACE=1 写 trace.log"""
    if not os.environ.get('WECHAT_EXPORT_TRACE'):
        return
    try:
        with open(os.path.join(ROOT, 'trace.log'), 'a', encoding='utf-8') as f:
            f.write('%s\n' % msg)
    except Exception:
        pass


def main():
    """GUI 主流程：设置 Qt 全局属性 → 建 QApplication → 装样式 → 起主窗口

    顺序很关键，动过就会出问题的三处：
      1) 三个 setAttribute 必须在 QApplication 实例化之前调用，否则静默失效；
      2) setStyleSheet 必须在 app 建好之后调用，因为样式里要填运行时生成的贴图路径；
      3) excepthook 要在 MainWindow 之前装好，否则窗口构造期的异常没人接。
    """
    _trace('enter main')
    from PyQt5.QtCore import Qt
    from PyQt5.QtWidgets import QApplication, QMessageBox
    os.environ.setdefault('QT_ENABLE_HIGHDPI_SCALING', '1')   # 高分屏不糊
    # 这两行必须在 QApplication 创建之前设置，否则不生效：
    #   AA_EnableHighDpiScaling 按系统缩放比放大界面，AA_UseHighDpiPixmaps 让图标取高清图
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    # 禁掉 Windows 对话框标题栏默认的「?」上下文帮助按钮（Windows 风格下
    # QDialog 会自动加，用户不需要这个功能，还占着标题栏）
    QApplication.setAttribute(Qt.AA_DisableWindowContextHelpButton, True)

    app = QApplication(sys.argv)
    _trace('QApplication ok')
    app.setApplicationName('微信聊天记录导出工具V1.0.0')
    # Fusion 是 Qt 自绘风格，不受 Windows 主题影响，配合 QSS 效果最稳定
    app.setStyle('Fusion')
    app.setFont(app.font())

    # 窗口图标（打包后从 _MEIPASS/assets 取）
    from PyQt5.QtGui import QIcon
    _icon = os.path.join(BUNDLE, 'assets', 'app_icon.ico')
    if os.path.exists(_icon):
        app.setWindowIcon(QIcon(_icon))

    from ui.main_window import MainWindow
    from ui.theme import stylesheet
    _trace('import MainWindow ok')
    # 样式表在 app 建好之后再取：勾选框的白勾是运行时生成的 png，路径要填进去
    app.setStyleSheet(stylesheet())

    def _hook(t, v, tb):
        """兜底异常处理：弹窗告诉用户，并留痕（否则打包后闪退什么也看不到）"""
        _trace('excepthook: %r' % (v,))
        try:
            QMessageBox.critical(None, '程序出错', '%s\n\n%s' % (v, ''.join(
                traceback.format_exception(t, v, tb))[-1200:]))
        except Exception:
            traceback.print_exception(t, v, tb)
    sys.excepthook = _hook

    w = MainWindow()
    _trace('MainWindow ok')
    w.show()
    _trace('show ok')
    sys.exit(app.exec_())


# 样式表已独立到 ui/theme.py：那里集中管理配色、控件外观，
# 并在运行时生成勾选框的对勾贴图（QSS 自身画不出对勾）。


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1].startswith('--bootstrap-'):
        # 子进程模式：先分派，绝不进 GUI
        _run_bootstrap('keys' if sys.argv[1] == '--bootstrap-keys' else 'decrypt',
                       sys.argv[2:])
    else:
        try:
            main()
        except SystemExit:
            raise
        except BaseException:
            # 打包后无控制台，异常写 crash.log 便于回查
            import traceback as _tb
            try:
                with open(os.path.join(ROOT, 'crash.log'), 'a', encoding='utf-8') as _f:
                    _f.write('%s\n\n' % _tb.format_exc())
            except Exception:
                pass
            raise
