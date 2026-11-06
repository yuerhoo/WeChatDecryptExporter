# -*- coding: utf-8 -*-
"""全局样式表（整个程序的视觉基调）

单独成模块的原因：
    1) 勾选框的「勾」必须用运行时生成的 png（QSS 画不出对勾），
       路径依赖用户机器的缓存目录，只能等 QApplication 建好后再拼；
    2) 样式集中在文件里，各窗口只管 setObjectName，避免同一控件在多处写内联样式。

设计约定（改样式前先看这里，避免各处风格打架）：
    · 底色 #F2F5F9 浅灰蓝，卡片纯白 + 1px #E6EAF2 细边 + 圆角 12px
      —— 用「卡片分层」代替老式灰色 GroupBox 描边，界面更透气
    · 主色 #185FA5（蓝），只用在「主操作按钮 / 选中态 / 进度条 / 关键数字」
    · 语义色：成功 #17805B、警示 #B26A00、危险 #C0392B
    · 控件对象名约定（各窗口里通过 setObjectName 引用）：
        primary     主操作按钮（蓝底白字）
        quiet       无边框文字按钮（次要操作）
        danger      危险操作（删除类，红字浅边）
        chip        可选中的胶囊按钮（时间快捷块）
        menubtn     顶栏文字按钮（设置 / 关于）
        card        白色圆角卡片容器
        stepNo      空状态里的步骤序号圆点
        h1 / sub / tip / tipGood / tipWarn / badge / badgeActive / badgeGood / badgeBad
    · 输入框统一：白底、#D5DBE5 边框、圆角 8px、聚焦变主色
"""
from . import icons

QSS_TEMPLATE = """
/* ================= 基础 ================= */
QWidget {
    font-family: "Microsoft YaHei UI", "Microsoft YaHei", "PingFang SC", sans-serif;
    font-size: 13px; color: #1F2430;
}
QMainWindow, QDialog, QStackedWidget { background: #F2F5F9; }
QWidget#page { background: #F2F5F9; }

/* ================= 卡片 ================= */
QGroupBox {
    background: #FFFFFF;
    border: 1px solid #E6EAF2;
    border-radius: 12px;
    margin-top: 16px;
    padding: 18px 16px 16px 16px;
    font-weight: 600;
}
QGroupBox::title {
    subcontrol-origin: margin; left: 14px; padding: 0 5px;
    color: #1B2230; font-weight: 600; background: transparent;
}
QFrame#card, QWidget#card {
    background: #FFFFFF; border: 1px solid #E6EAF2; border-radius: 12px;
}

/* ================= 按钮 ================= */
QPushButton {
    background: #FFFFFF; border: 1px solid #D5DBE5; border-radius: 8px;
    padding: 7px 15px; color: #26303F; min-height: 18px;
}
QPushButton:hover { background: #F4F7FB; border-color: #BCC7D6; }
QPushButton:pressed { background: #E9EFF7; }
QPushButton:focus { border-color: #185FA5; outline: none; }
QPushButton:disabled {
    background: #F6F8FB; border-color: #ECF0F5; color: #B3BAC5;
}

QPushButton#primary {
    background: #185FA5; border-color: #185FA5; color: #FFFFFF; font-weight: 600;
    padding: 7px 18px;
}
QPushButton#primary:hover { background: #1B70C2; border-color: #1B70C2; }
QPushButton#primary:pressed { background: #12497F; border-color: #12497F; }
QPushButton#primary:disabled {
    background: #B7CCE2; border-color: #B7CCE2; color: #F4F8FC;
}

QPushButton#quiet {
    background: transparent; border: none; color: #185FA5; padding: 5px 9px;
}
QPushButton#quiet:hover { background: #EAF1F9; }
QPushButton#quiet:disabled { color: #B3BAC5; background: transparent; }

QPushButton#danger { color: #C0392B; border-color: #EFCFC9; }
QPushButton#danger:hover { background: #FDF3F1; border-color: #E4B2A9; }
QPushButton#danger:disabled { color: #C9CDD4; border-color: #EFEFEF; background: #FAFAFB; }

QPushButton#menubtn {
    background: transparent; border: none; padding: 7px 12px; color: #4B5563;
}
QPushButton#menubtn:hover { background: #F1F5FA; color: #1B2230; }
QPushButton#menubtn:pressed { background: #E7EEF7; }

QPushButton#chip {
    background: #F4F6FA; border: 1px solid #E6EAF2; border-radius: 15px;
    padding: 5px 17px; color: #5B6472;
}
QPushButton#chip:hover { background: #EAF1F9; border-color: #C6D9EE; color: #185FA5; }
QPushButton#chip:checked {
    background: #E8F1FB; border-color: #AFCCEA; color: #185FA5; font-weight: 600;
}

/* ================= 输入类控件 ================= */
QLineEdit, QComboBox, QDateTimeEdit, QSpinBox {
    background: #FFFFFF; border: 1px solid #D5DBE5; border-radius: 8px;
    padding: 7px 10px; selection-background-color: #185FA5;
    selection-color: #FFFFFF; min-height: 18px;
}
QLineEdit:focus, QComboBox:focus, QDateTimeEdit:focus, QSpinBox:focus {
    border-color: #185FA5;
}
QLineEdit:disabled, QComboBox:disabled, QDateTimeEdit:disabled {
    background: #F6F8FB; color: #A8B0BC;
}
QLineEdit[readOnly="true"] { background: #F6F8FB; color: #62707F; }
QLineEdit#search {
    padding-left: 8px; background: #FFFFFF;
}
QComboBox::drop-down { border: none; width: 24px; }
QComboBox::down-arrow { image: url("%(arrow)s"); width: 12px; height: 12px; }
QComboBox::down-arrow:disabled { image: url("%(arrow_disabled)s"); }
QComboBox QAbstractItemView {
    background: #FFFFFF; border: 1px solid #E6EAF2; border-radius: 8px;
    padding: 4px; outline: none;
    selection-background-color: #E8F1FB; selection-color: #1F2430;
}
QDateTimeEdit::drop-down { border: none; width: 20px; }

/* ================= 勾选框 / 单选框 ================= */
QCheckBox, QRadioButton { spacing: 8px; }
QCheckBox:disabled, QRadioButton:disabled { color: #A8B0BC; }

QCheckBox::indicator, QRadioButton::indicator {
    width: 16px; height: 16px;
    border: 1px solid #C6CEDB; background: #FFFFFF;
}
QCheckBox::indicator { border-radius: 4px; }
QRadioButton::indicator { border-radius: 9px; }

QCheckBox::indicator:hover, QRadioButton::indicator:hover { border-color: #185FA5; }
QCheckBox::indicator:disabled, QRadioButton::indicator:disabled {
    border-color: #E3E7EE; background: #F7F8FA;
}

/* 勾选态：主色底 + 白勾（白勾是运行时生成的 png，QSS 画不出对勾） */
QCheckBox::indicator:checked {
    background: #185FA5; border-color: #185FA5;
    image: url("%(tick)s");
}
QCheckBox::indicator:checked:hover { background: #1B70C2; border-color: #1B70C2; }
QCheckBox::indicator:checked:disabled {
    background: #B7CCE2; border-color: #B7CCE2;
    image: url("%(tick_disabled)s");
}

/* 单选：白底圆框 + 主色实心圆点（圆点是运行时生成的 png，
   纯 border 技巧在不同缩放比例下容易糊），一眼能看出选中在哪 */
QRadioButton::indicator:checked {
    border: 1px solid #185FA5; background: #FFFFFF;
    image: url("%(dot)s");
}
QRadioButton::indicator:checked:disabled {
    border-color: #B7CCE2;
    image: url("%(dot_disabled)s");
}

/* 输出方式的三张「选项卡」：整行可点，选中时浅蓝底 + 主色边 */
QRadioButton[modeCard="true"] {
    background: #FBFCFE; border: 1px solid #E9EDF3; border-radius: 9px;
    padding: 9px 11px; color: #26303F;
}
QRadioButton[modeCard="true"]:hover { border-color: #C6D9EE; background: #F7FBFF; }
QRadioButton[modeCard="true"]:checked {
    border-color: #AFCCEA; background: #F1F8FE; color: #123A5E; font-weight: 600;
}

/* ================= Tab ================= */
QTabWidget::pane { border: none; background: #FFFFFF; }
QTabWidget::tab-bar { left: 4px; }
QTabBar::tab {
    background: transparent; padding: 10px 22px; margin-right: 2px;
    border: none; border-bottom: 2px solid transparent;
    color: #6B7280; font-weight: 500;
    /* 说明：内边距故意比视觉需要多留几像素 —— Windows 高分屏缩放
       （125%%/150%%）下字形实际渲染宽度会比字体度量多出 1~3px，
       余量不足时 Tab 数字（尤其最后一位）会被切掉 */
}
QTabBar::tab:hover:!selected { color: #1F2430; background: #F4F7FB; }
QTabBar::tab:selected {
    color: #185FA5; border-bottom: 2px solid #185FA5; font-weight: 600;
}

/* ================= 表格 ================= */
QTableView {
    background: #FFFFFF; border: none; alternate-background-color: #FAFBFD;
    selection-background-color: #E8F1FB; selection-color: #12324F;
    gridline-color: transparent; outline: none;
}
QTableView::item { padding: 4px 10px; border: none; }
QTableView::item:selected { background: #E8F1FB; color: #12324F; }
QHeaderView { background: transparent; }
QHeaderView::section {
    background: #F7F9FC; color: #6B7280; border: none;
    border-bottom: 1px solid #E9EDF3; padding: 9px 10px; font-weight: 600;
}
QHeaderView::section:hover { color: #1F2430; background: #F1F5FA; }
QTableCornerButton::section { background: #F7F9FC; border: none; }

/* ================= 列表 ================= */
QListWidget {
    background: #FBFCFE; border: 1px solid #E9EDF3; border-radius: 9px;
    padding: 4px; outline: none;
}
QListWidget::item {
    padding: 7px 9px; border-radius: 6px; color: #26303F;
}
QListWidget::item:hover { background: #F1F5FA; }
QListWidget::item:selected { background: #E8F1FB; color: #12324F; }

/* ================= 滚动条 ================= */
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical {
    background: #D6DCE6; border-radius: 5px; min-height: 32px;
}
QScrollBar::handle:vertical:hover { background: #BFC9D6; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 2px; }
QScrollBar::handle:horizontal {
    background: #D6DCE6; border-radius: 5px; min-width: 32px;
}
QScrollBar::handle:horizontal:hover { background: #BFC9D6; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; border: none; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }

/* ================= 日志框 / 进度条 ================= */
QPlainTextEdit {
    background: #FBFCFE; border: 1px solid #E9EDF3; border-radius: 9px;
    padding: 9px; color: #3C4655;
    font-family: Consolas, "Cascadia Mono", "Courier New", monospace; font-size: 12px;
}
QProgressBar {
    background: #EBF0F7; border: none; border-radius: 8px;
    height: 16px; text-align: center; color: #3E4A5A; font-size: 11px;
}
QProgressBar::chunk {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                                stop:0 #1B70C2, stop:1 #185FA5);
    border-radius: 8px;
}
/* 导出结果：进度条整条变成结果色（代码里用 setProperty('state', ...) 触发） */
QProgressBar[state="done"]::chunk { background: #17805B; }
QProgressBar[state="cancel"]::chunk { background: #B26A00; }
QProgressBar[state="fail"]::chunk { background: #C0392B; }

/* ================= 顶栏 / 状态栏 ================= */
QWidget#topbar { background: #FFFFFF; border-bottom: 1px solid #E9EDF3; }
QWidget#toolbar { background: #FFFFFF; border-bottom: 1px solid #EDF0F5; }
QStatusBar { background: #FFFFFF; border-top: 1px solid #E9EDF3; color: #6B7280; }
QStatusBar::item { border: none; }
QStatusBar QLabel { padding: 0 2px; }

/* ================= 文字标签 ================= */
QLabel#h1 { font-size: 18px; font-weight: 600; color: #161C28; }
QLabel#h2 { font-size: 15px; font-weight: 600; color: #1B2230; }
QLabel#sub { font-size: 12px; color: #6B7280; }
QLabel#tip { font-size: 12px; color: #7A8494; }
QLabel#tipGood { font-size: 12px; color: #17805B; }
QLabel#tipWarn { font-size: 12px; color: #B26A00; }

QLabel#badge {
    background: #EEF2F8; color: #5B6472; border-radius: 10px;
    padding: 3px 11px; font-size: 12px;
}
QLabel#badgeActive {
    background: #E8F1FB; color: #185FA5; border-radius: 10px;
    padding: 3px 11px; font-size: 12px; font-weight: 600;
}
QLabel#badgeGood {
    background: #E7F5EF; color: #17805B; border-radius: 10px;
    padding: 2px 10px; font-size: 12px;
}
QLabel#badgeBad {
    background: #FDECEA; color: #C0392B; border-radius: 10px;
    padding: 2px 10px; font-size: 12px;
}
QLabel#badgeGray {
    background: #F1F3F6; color: #6B7280; border-radius: 10px;
    padding: 2px 10px; font-size: 12px;
}

/* 空状态里的步骤序号 */
QLabel#stepNo {
    background: #E8F1FB; color: #185FA5; border-radius: 11px;
    min-width: 22px; max-width: 22px; min-height: 22px; max-height: 22px;
    font-weight: 600; font-size: 12px;
}
QLabel#stepTitle { font-size: 13px; font-weight: 600; color: #26303F; }
QLabel#stepDesc { font-size: 12px; color: #7A8494; }

/* ================= 菜单 / 提示气泡 ================= */
QMenu { background: #FFFFFF; border: 1px solid #E6EAF2; border-radius: 9px; padding: 6px; }
QMenu::item { padding: 7px 26px 7px 14px; border-radius: 6px; }
QMenu::item:selected { background: #F1F5FA; }
QMenu::separator { height: 1px; background: #EEF1F6; margin: 4px 8px; }
QToolTip {
    background: #1F2430; color: #FFFFFF; border: none;
    padding: 6px 9px; border-radius: 6px;
}

/* ================= 消息框 ================= */
QMessageBox { background: #FFFFFF; }
QMessageBox QLabel { color: #26303F; }
QMessageBox QPushButton { min-width: 76px; }
"""


def stylesheet():
    """生成最终样式表：把运行时生成的勾/箭头贴图路径填进模板

    必须在 QApplication 创建之后调用（生成贴图要用 QPixmap，需要 GUI 环境）。
    """
    a = icons.qss_assets()
    return QSS_TEMPLATE % {
        'tick': a.get('tick', ''),
        'tick_disabled': a.get('tick_disabled', ''),
        'arrow': a.get('arrow', ''),
        'arrow_disabled': a.get('arrow_disabled', ''),
        'dot': a.get('dot', ''),
        'dot_disabled': a.get('dot_disabled', ''),
    }
