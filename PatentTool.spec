# -*- mode: python ; coding: utf-8 -*-
# ---- 构建时间戳：每次打包生成带时间的文件名，并写入 Windows 版本信息 ----
import datetime as _dt
from PyInstaller.utils.win32.versioninfo import (
    VSVersionInfo, FixedFileInfo, StringFileInfo,
    StringTable, StringStruct, VarFileInfo, VarStruct,
)

_now = _dt.datetime.now()
_BUILD_TS = _now.strftime("%Y%m%d_%H%M%S")                # 20260803_134521
_VERSION = "1.0." + _now.strftime("%Y%m%d%H%M%S")         # 1.0.20260803134521

_version_info = VSVersionInfo(
    ffi=FixedFileInfo(
        filevers=(1, 0, 0, 1),   # WORD 上限 65535，具体版本放在下方字符串里
        prodvers=(1, 0, 0, 1),
        mask=0x3F,
        flags=0x0,
        OS=0x40004,
        fileType=0x1,
        subtype=0x0,
        date=(0, 0),
    ),
    kids=[
        StringFileInfo([
            StringTable(
                "080404b0",   # 中文简体 + Unicode
                [
                    StringStruct("CompanyName", "PatentTool"),
                    StringStruct("FileDescription", "专利检索分析工具"),
                    StringStruct("FileVersion", _VERSION),
                    StringStruct("InternalName", "PatentTool"),
                    StringStruct("OriginalFilename",
                                 "PatentTool_v1.0_%s.exe" % _BUILD_TS),
                    StringStruct("ProductName", "专利检索分析工具"),
                    StringStruct("ProductVersion", _VERSION),
                    StringStruct("Comments",
                                 "Build time: %s" % _now.strftime("%Y-%m-%d %H:%M:%S")),
                ],
            )
        ]),
        VarFileInfo([VarStruct("Translation", [2052, 1200])]),
    ],
)

a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=[],
    datas=[('config', 'config')],
    hiddenimports=[
        'src', 'src.ui', 'src.web_automation', 'src.utils',
        'src.query_generator', 'src.analysis', 'src.result_collector',
        'src.pdf_extractor',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # 排除 PySide6 中不需要的 Qt 模块（裁掉几百MB）
    excludes=[
        # PySide6 不用的子模块
        'PySide6.QtQml', 'PySide6.QtQuick', 'PySide6.QtQuickWidgets',
        'PySide6.Qt3DCore', 'PySide6.Qt3DRender', 'PySide6.Qt3DInput',
        'PySide6.Qt3DLogic', 'PySide6.Qt3DAnimation', 'PySide6.Qt3DExtras',
        'PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets',
        'PySide6.QtWebChannel', 'PySide6.QtWebSockets',
        'PySide6.QtMultimedia', 'PySide6.QtMultimediaWidgets',
        'PySide6.QtBluetooth', 'PySide6.QtNfc',
        'PySide6.QtSensors', 'PySide6.QtSerialPort',
        'PySide6.QtPositioning', 'PySide6.QtLocation',
        'PySide6.QtCharts', 'PySide6.QtDataVisualization',
        'PySide6.QtSql', 'PySide6.QtTest', 'PySide6.QtXml',
        'PySide6.QtHelp', 'PySide6.QtPrintSupport',
        'PySide6.QtSvgWidgets',
        'PySide6.QtOpenGL', 'PySide6.QtOpenGLWidgets',
        'PySide6.QtStateMachine', 'PySide6.QtTextToSpeech',
        'PySide6.QtPdf', 'PySide6.QtPdfWidgets',
        # Playwright 浏览器驱动（系统Edge足够）
        'playwright.driver',
        # 不用的标准库
        # 注意：不能排除 email / http —— httpx(DeepSeek/Kimi/Google Patents)
        #       底层依赖 http.client 与 email，排除后打包版所有网络请求都会崩
        'tcl', 'tkinter', 'turtle', 'idlelib',
        'test', 'unittest', 'doctest',
        'xmlrpc', 'wsgiref',
        'curses', 'ensurepip', 'venv',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=None,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=None)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='PatentTool_v1.0_%s' % _BUILD_TS,
    debug=False,
    bootloader_ignore_signals=False,
    strip=True,
    upx=True,           # UPX 压缩
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,       # 无控制台窗口
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='assets/icon.ico',   # exe 图标
    version=_version_info,    # Windows 文件属性里的版本 + 构建时间
)
