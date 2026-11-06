# -*- coding: utf-8 -*-
"""core 包：与界面无关的业务层

    accounts   账号数据（account.json / keys.json / manifest.json / 各类日志路径）
    contacts   联系人、群昵称、会话（Sess）与消息数缓存
    messages   跨分库扫消息、解压正文、解析发送人、类型判定
    exporter   把消息写成 xlsx（两种输出模式，版面见 exporter 模块注释）
    runner     调起提权子进程、读子进程日志
    detect     定位微信安装目录 / 数据目录 / 进程

只依赖标准库、openpyxl 和 zstandard，不 import 任何 PyQt —— 这样这些模块
可以在命令行里单独跑（调试导出不用开界面），也是"界面卡住也不影响解密"的前提。
"""
