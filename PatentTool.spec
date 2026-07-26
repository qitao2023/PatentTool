# -*- mode: python ; coding: utf-8 -*-
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
        'tcl', 'tkinter', 'turtle', 'idlelib',
        'test', 'unittest', 'doctest',
        'email', 'http', 'xmlrpc', 'wsgiref',
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
    name='PatentTool',
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
)
