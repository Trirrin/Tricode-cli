#!/bin/bash
# 构建项目为二进制文件

# 检查是否安装了pyinstaller
if ! command -v pyinstaller &> /dev/null
then
    echo "PyInstaller 未安装，请先运行: pip install pyinstaller"
    exit 1
fi

# 删除之前的构建
rm -rf dist build __pycache__

# 打包，去除控制台窗口，生成单文件（可调整参数）
pyinstaller --onefile --name tricode tricode.py

echo "打包完成，二进制文件位于 dist/tricode"
