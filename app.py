"""
PatentTool 应用程序入口 - PyInstaller 打包用
"""
import sys
import os
from pathlib import Path


def get_base_dir() -> Path:
    """获取应用根目录（兼容 PyInstaller 打包和开发模式）"""
    if getattr(sys, 'frozen', False):
        # PyInstaller 打包后：exe 所在目录
        return Path(sys.executable).parent
    else:
        # 开发模式：脚本所在目录
        return Path(__file__).parent


def ensure_dirs(base: Path):
    """确保运行时目录存在"""
    (base / "data" / "db").mkdir(parents=True, exist_ok=True)
    (base / "data" / "output").mkdir(parents=True, exist_ok=True)
    (base / "profiles" / "patentscope_browser").mkdir(parents=True, exist_ok=True)


def main():
    base = get_base_dir()
    os.chdir(str(base))
    ensure_dirs(base)

    # 确保 src 在 path 中
    if str(base) not in sys.path:
        sys.path.insert(0, str(base))

    from src.main import main as app_main
    app_main()


if __name__ == "__main__":
    main()
