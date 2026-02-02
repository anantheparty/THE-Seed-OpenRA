# -*- coding: utf-8 -*-
"""
Tactical Core V2 独立启动器
用于脱离主程序单独运行战术核心模块，进行测试或独立部署。
"""
import time
import sys
import os
import argparse
import importlib

# 动态配置路径以支持独立运行
current_dir = os.path.dirname(os.path.abspath(__file__))
# 获取父目录
parent_dir = os.path.dirname(current_dir)

# 将父目录加入 sys.path，以便能以包的形式导入当前模块
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

# 获取包名 (通常为 tactical_core，但支持重命名)
package_name = os.path.basename(current_dir)

try:
    # 动态导入模块
    enhancer_module = importlib.import_module(f"{package_name}.enhancer")
    ui_module = importlib.import_module(f"{package_name}.ui")
    
    BiodsEnhancer = enhancer_module.BiodsEnhancer
    TacticalLogWindow = ui_module.TacticalLogWindow
except ImportError as e:
    print(f"Critical Error: Failed to import tactical core modules: {e}")
    print(f"Ensure that '{package_name}' is treated as a package.")
    sys.exit(1)

def run_standalone():
    """启动完整战术核心（含UI）"""
    print("Initializing Tactical Core V2 (Standalone)...")
    
    # 启用调试日志
    os.environ["LLM_DEBUG"] = "1"
    
    # 创建核心实例
    core = BiodsEnhancer()
    
    try:
        # 启动模块
        # 独立启动时默认显示日志窗口
        core.start(api_client_placeholder=None, show_log_window=True)
        
        print("Tactical Core V2 is running. Press Ctrl+C to stop.")
        
        # 模拟主循环保持程序运行
        while True:
            time.sleep(1.0)
            
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        core.stop()
        print("Shutdown complete.")

def run_ui():
    """仅启动UI窗口"""
    print("Initializing Tactical Core V2 UI (Standalone)...")
    
    window = TacticalLogWindow()
    window.start()
    
    print("Tactical Core UI is running. Press Ctrl+C to stop.")
    
    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        print("\nStopping UI...")
    finally:
        window.stop()
        print("UI Shutdown complete.")

def main():
    parser = argparse.ArgumentParser(description="Tactical Core V2 Launcher")
    parser.add_argument("--ui", action="store_true", help="Launch UI only (follows main program)")
    args = parser.parse_args()

    if args.ui:
        run_ui()
    else:
        run_standalone()

if __name__ == "__main__":
    main()
