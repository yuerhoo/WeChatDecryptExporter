# -*- coding: utf-8 -*-
"""ui 包：所有 PyQt5 界面代码

    main_window      主窗口（Tab 列表、搜索、勾选、状态栏）与两个后台线程
    settings_dialog  设置窗（账号 / 密钥 / 解密 / 数据目录）
    export_dialog    导出参数弹窗（时间、类型、输出方式、保存位置）
    export_runner    导出进度窗 + 后台导出线程
    table_model      QTableView 的数据模型
    delegates        表格勾选列的自绘 delegate
    theme            全局 QSS（配色、控件外观）
    icons            运行时用 QPainter 画的线条图标 + 勾选/单选贴图

约定：ui 层只做"取数据、显示、转发用户意图"，不直接读写 sqlite；
所有耗时操作都丢给 QThread，通过 pyqtSignal 回主线程更新界面。
"""
