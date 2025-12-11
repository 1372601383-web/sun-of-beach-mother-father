# -*- coding: utf-8 -*-
"""
PyQt6 GitHub 图片上传器（美化版）
- 拖拽图片文件夹（仅文件夹，且文件夹内必须包含图片）
- 复制到仓库根目录（脚本中设置 repo_root 或自动使用脚本所在目录）
- git add/commit/pull/rebase/push（可选用内嵌 token）
- 实时显示上传进度与网速（解析 git stderr）
- 生成 Excel（列：文件名(去扩展名) | 凑数(空) | jsDelivr URL）
- Git 用户名与 Token 写在脚本顶部（不在界面显示）
"""
import os
import sys
import shutil
import subprocess
import time
from urllib.parse import quote
from datetime import datetime

from PyQt6 import QtCore, QtWidgets
import openpyxl

# ---------------- CONFIG ----------------
GITHUB_USERNAME = "1372601383-web"
GITHUB_TOKEN = ""  # 可选
GITHUB_REPO = "sun-of-beach-mother-father"
GITHUB_BRANCH = "main"
REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".svg", ".bmp"}
EXCEL_SUFFIX = "_urls.xlsx"
# ----------------------------------------

def ts():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def windows_long_path(path):
    if os.name == "nt":
        if path.startswith("\\\\?\\"):
            return path
        return "\\\\?\\" + os.path.abspath(path)
    return path

def copy_tree_with_retry(src, dst, logger):
    fail_list = []
    success_count = 0
    for root, dirs, files in os.walk(src):
        rel = os.path.relpath(root, src)
        target_dir = os.path.join(dst, rel) if rel != "." else dst
        try:
            os.makedirs(target_dir, exist_ok=True)
        except Exception as e:
            logger(f"创建目标目录失败: {target_dir} -> {e}")
            fail_list.append((root, target_dir, str(e)))
            continue
        for f in files:
            src_path = os.path.join(root, f)
            dst_path = os.path.join(target_dir, f)
            try:
                shutil.copy2(src_path, dst_path)
                success_count += 1
            except Exception:
                try:
                    sp = windows_long_path(src_path)
                    dp = windows_long_path(dst_path)
                    os.makedirs(os.path.dirname(dst_path), exist_ok=True)
                    shutil.copy2(sp, dp)
                    success_count += 1
                except Exception as e2:
                    logger(f"复制文件失败: '{src_path}' -> '{dst_path}' 错误: {e2}")
                    fail_list.append((src_path, dst_path, str(e2)))
    return success_count, fail_list

class WorkerSignals(QtCore.QObject):
    log = QtCore.pyqtSignal(str)
    progress = QtCore.pyqtSignal(int, int)
    speed = QtCore.pyqtSignal(str)
    finished = QtCore.pyqtSignal(bool, str)

class UploadWorker(QtCore.QRunnable):
    def __init__(self, src_folder, repo_root, username, token, repo, branch):
        super().__init__()
        self.src_folder = src_folder
        self.repo_root = repo_root
        self.username = username
        self.token = token
        self.repo = repo
        self.branch = branch
        self.signals = WorkerSignals()

    def log(self, msg):
        self.signals.log.emit(f"[{ts()}] {msg}")

    def run(self):
        try:
            folder_name = os.path.basename(self.src_folder.rstrip(os.sep))
            dest = os.path.join(self.repo_root, folder_name)
            if os.path.exists(dest):
                self.log(f"目标仓库根目录已存在同名文件夹：{folder_name}，上传被阻止。")
                self.signals.finished.emit(False, "目标文件夹已存在于仓库根目录，已阻止上传。")
                return

            image_paths = []
            for root, _, files in os.walk(self.src_folder):
                for f in files:
                    if os.path.splitext(f)[1].lower() in IMAGE_EXT:
                        image_paths.append(os.path.join(root, f))
            total_images = len(image_paths)
            if total_images == 0:
                self.log("拖入文件夹不包含任何图片，已取消。")
                self.signals.finished.emit(False, "所选文件夹内无图片。")
                return
            self.log(f"拖入文件夹：{self.src_folder}，图片数量：{total_images}")
            self.signals.progress.emit(0, total_images)

            self.log("开始复制文件到仓库根目录...")
            start_copy = time.time()
            success_count, fail_list = copy_tree_with_retry(self.src_folder, dest, self.log)
            elapsed = time.time() - start_copy
            self.log(f"复制完成：成功 {success_count}，失败 {len(fail_list)}，耗时 {elapsed:.1f}s")
            if fail_list:
                for a,b,err in fail_list[:10]:
                    self.log(f"复制失败样本：{a} -> {b}  错误: {err}")
                if len(fail_list) > 10:
                    self.log(f"... 还有 {len(fail_list)-10} 条复制失败记录（已略）")

            self.log("开始 git add/commit/pull(rebase)/push ...")
            r = subprocess.run(["git", "add", folder_name], cwd=self.repo_root,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if r.returncode != 0:
                self.log(f"git add 返回错误：{r.stderr.strip()}")
                self.signals.finished.emit(False, f"git add 失败: {r.stderr.strip()}")
                return

            commit_msg = f"Add {folder_name}"
            r = subprocess.run(["git", "commit", "-m", commit_msg], cwd=self.repo_root,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            self.log(r.stdout.strip() + ("\n" + r.stderr.strip() if r.stderr.strip() else ""))

            self.log(f"git pull --rebase origin {self.branch}")
            r = subprocess.run(["git", "pull", "origin", self.branch, "--rebase"],
                               cwd=self.repo_root, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            self.log(r.stdout.strip() + ("\n" + r.stderr.strip() if r.stderr.strip() else ""))

            self.log("开始 git push（解析 stderr 以提取上传速度）")
            proc = subprocess.Popen(["git", "push", "origin", self.branch],
                                    cwd=self.repo_root, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    text=True, bufsize=1)
            last_speed = "-"
            while True:
                line = proc.stderr.readline()
                if line == "" and proc.poll() is not None:
                    break
                if line:
                    line = line.strip()
                    self.log(line)
                    if '/s' in line or 'KiB/s' in line or 'MiB/s' in line or 'B/s' in line:
                        tokens = [tok for tok in line.split() if tok.endswith("/s")]
                        if tokens:
                            last_speed = tokens[-1]
                            self.signals.speed.emit(last_speed)
                    if "Writing objects:" in line and "(" in line and "/" in line:
                        try:
                            part = line.split("(",1)[1].split(")")[0]
                            if "/" in part:
                                done_s, total_s = part.split("/",1)
                                done = int(done_s.strip())
                                total = int(total_s.strip())
                                prog = min(total_images, max(0, done * total_images // max(1, total)))
                                self.signals.progress.emit(prog, total_images)
                        except Exception:
                            pass
            rc = proc.wait()
            if rc != 0:
                stderr = proc.stderr.read() if proc.stderr else ""
                self.log(f"git push 返回码 {rc}，stderr 部分：{stderr}")
                self.signals.finished.emit(False, f"git push 失败，返回码 {rc}")
                return

            self.log("git push 成功，开始生成 Excel ...")
            wb = openpyxl.Workbook()
            ws = wb.active
            ws.append(["文件名", "凑数", "图片URL"])
            rows = []
            for root, _, files in os.walk(dest):
                for fname in files:
                    if os.path.splitext(fname)[1].lower() in IMAGE_EXT:
                        name_no_ext = os.path.splitext(fname)[0]
                        rel_path = os.path.relpath(os.path.join(root, fname), start=self.repo_root)
                        rel_posix = rel_path.replace(os.sep, "/")
                        parts = rel_posix.split("/")
                        encoded = "/".join(quote(p) for p in parts)
                        cdn_url = f"https://cdn.jsdelivr.net/gh/{self.username}/{self.repo}/{encoded}"
                        rows.append((name_no_ext, "", cdn_url))
                        self.signals.progress.emit(len(rows), total_images)
            for r in rows:
                ws.append(r)
            excel_name = f"{folder_name}{EXCEL_SUFFIX}"
            excel_path = os.path.join(self.repo_root, excel_name)
            try:
                wb.save(excel_path)
                self.log(f"Excel 已保存：{excel_path}")
            except Exception as e:
                self.log(f"保存 Excel 失败：{e}")

            self.log("任务完成。")
            self.signals.finished.emit(True, f"完成：{excel_path}")

        except Exception as e:
            self.log(f"任务异常终止：{e}")
            self.signals.finished.emit(False, f"任务异常：{e}")

class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("GitHub 图片上传器 （PyQt6）")
        self.resize(860, 640)
        self.setAcceptDrops(True)

        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        layout = QtWidgets.QVBoxLayout(central)
        layout.setContentsMargins(12,12,12,12)
        layout.setSpacing(8)

        info = QtWidgets.QLabel(f"仓库根目录： {REPO_ROOT}    （Git 用户名与 Token 已写在脚本内）")
        layout.addWidget(info)

        self.drag_area = QtWidgets.QLabel("把含图片的文件夹拖到这里\n（仅支持文件夹；文件夹内必须包含图片）")
        self.drag_area.setFixedHeight(150)
        self.drag_area.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.drag_area.setStyleSheet("""
            QLabel {
                background: qlineargradient(spread:pad, x1:0, y1:0, x2:1, y2:1, stop:0 #162b2b, stop:1 #1f2f2f);
                color: #d8f3e3;
                border: 2px dashed #2e8b57;
                border-radius: 8px;
                font-size: 16px;
            }
        """)
        layout.addWidget(self.drag_area)

        row = QtWidgets.QHBoxLayout()
        self.count_label = QtWidgets.QLabel("已选：无")
        self.count_label.setStyleSheet("font-weight:bold")
        row.addWidget(self.count_label)
        self.start_btn = QtWidgets.QPushButton("开始上传并生成 Excel")
        self.start_btn.clicked.connect(self.on_start)
        row.addWidget(self.start_btn)
        self.start_btn.setEnabled(False)
        layout.addLayout(row)

        self.progress_bar = QtWidgets.QProgressBar()
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setStyleSheet("""
            QProgressBar {
                border: 1px solid #444;
                border-radius: 8px;
                text-align: center;
                background: #222;
                color: #fff;
            }
            QProgressBar::chunk {
                background-color: #39d353;
                border-radius: 8px;
            }
        """)
        layout.addWidget(self.progress_bar)

        self.speed_label = QtWidgets.QLabel("上传速度：-")
        layout.addWidget(self.speed_label)

        self.log_view = QtWidgets.QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setStyleSheet("background:#0f1010;color:#cfead1;font-family:Consolas;")
        layout.addWidget(self.log_view, stretch=1)

        self.status = QtWidgets.QStatusBar()
        self.setStatusBar(self.status)
        self.pool = QtCore.QThreadPool.globalInstance()

        self.selected_folder = None
        self.total_images = 0

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        if not urls:
            return
        local_path = urls[0].toLocalFile()
        if not os.path.isdir(local_path):
            self.append_log(f"请拖入文件夹（不是文件）：{local_path}")
            return
        count = 0
        for root, _, files in os.walk(local_path):
            for f in files:
                if os.path.splitext(f)[1].lower() in IMAGE_EXT:
                    count += 1
        if count == 0:
            self.append_log(f"文件夹不包含图片：{local_path}")
            return
        folder_name = os.path.basename(local_path.rstrip(os.sep))
        dest = os.path.join(REPO_ROOT, folder_name)
        if os.path.exists(dest):
            self.append_log(f"仓库根目录已存在同名文件夹：{folder_name}。已阻止，请先重命名本地文件夹。")
            self.selected_folder = None
            self.count_label.setText("已选：无（目标已存在同名文件夹）")
            self.start_btn.setEnabled(False)
            return

        self.selected_folder = local_path
        self.total_images = count
        self.count_label.setText(f"已选：{local_path}，图片数量：{count}")
        self.append_log(f"拖拽选中文件夹：{local_path}，图片数量：{count}")
        self.start_btn.setEnabled(True)

    def append_log(self, text):
        self.log_view.appendPlainText(f"[{ts()}] {text}")
        self.log_view.verticalScrollBar().setValue(self.log_view.verticalScrollBar().maximum())

    def on_start(self):
        if not self.selected_folder:
            self.append_log("未选择任何文件夹。")
            return
        self.start_btn.setEnabled(False)
        worker = UploadWorker(self.selected_folder, REPO_ROOT, GITHUB_USERNAME, GITHUB_TOKEN, GITHUB_REPO, GITHUB_BRANCH)
        worker.signals.log.connect(self.append_log)
        worker.signals.progress.connect(self.on_progress)
        worker.signals.speed.connect(self.on_speed)
        worker.signals.finished.connect(self.on_finished)
        self.pool.start(worker)
        self.append_log("任务已提交后台线程执行。")
        self.progress_bar.setRange(0, max(1, self.total_images))
        self.progress_bar.setValue(0)

    def on_progress(self, done, total):
        self.progress_bar.setMaximum(max(1, total))
        self.progress_bar.setValue(min(done, total))
        self.status.showMessage(f"已处理 {done}/{total}")

    def on_speed(self, speed_text):
        self.speed_label.setText(f"上传速度：{speed_text}")

    def on_finished(self, success, message):
        self.append_log(f"任务结束：{message}")
        self.start_btn.setEnabled(True)
        if success:
            QtWidgets.QMessageBox.information(self, "完成", message)
        else:
            QtWidgets.QMessageBox.warning(self, "失败", message)

def main():
    app = QtWidgets.QApplication(sys.argv)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
