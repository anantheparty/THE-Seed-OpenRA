# -*- coding: utf-8 -*-
import tkinter as tk
import threading
import queue
import time
from typing import Optional

class TacticalLogWindow:
    """
    轻量级半透明日志窗口，用于显示战术核心的实时状态
    """
    def __init__(self):
        self.root: Optional[tk.Tk] = None
        self.text_area = None
        self.queue = queue.Queue()
        self.running = False
        self._thread = None

    def start(self):
        if self.running:
            return
        self.running = True
        self._thread = threading.Thread(target=self._run_ui, daemon=True)
        self._thread.start()

    def stop(self):
        self.running = False
        if self.root:
            try:
                self.root.quit()
                # 确保销毁，防止资源残留
                # self.root.destroy() 
            except Exception:
                pass

    def log(self, message: str):
        if self.running:
            try:
                timestamp = time.strftime("%H:%M:%S", time.localtime())
                self.queue.put(f"[{timestamp}] {message}")
            except Exception:
                pass

    def _run_ui(self):
        try:
            self.root = tk.Tk()
            self.root.title("Tactical Core V2 Log")
            self.root.geometry("400x300+10+10")
            self.root.attributes("-alpha", 0.8)  # 半透明
            self.root.attributes("-topmost", True) # 置顶
            
            # 拦截关闭按钮事件，改为隐藏而不是销毁
            self.root.protocol("WM_DELETE_WINDOW", self.hide)
            
            # 黑色背景，绿色字体 (Hacker style)
            self.root.configure(bg='black')
            
            self.text_area = tk.Text(self.root, bg='black', fg='#00FF00', font=('Consolas', 9))
            self.text_area.pack(expand=True, fill='both')
            
            self.root.after(100, self._update_log)
            self.root.mainloop()
        except Exception:
            pass
        finally:
            self.running = False

    def show(self):
        if self.root:
            try:
                self.root.deiconify()
            except Exception:
                pass
    
    def hide(self):
        if self.root:
            try:
                self.root.withdraw()
            except Exception:
                pass

    def _update_log(self):
        if not self.running:
            return
            
        while not self.queue.empty():
            try:
                msg = self.queue.get_nowait()
                if self.text_area:
                    self.text_area.insert(tk.END, msg + "\n")
                    self.text_area.see(tk.END)
            except queue.Empty:
                break
        
        if self.root:
            self.root.after(100, self._update_log)
