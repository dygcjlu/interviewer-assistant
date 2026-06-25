"""
视频帧查看器
用法: python scripts/video_frame_viewer.py <视频路径>

键盘操作:
  →  / D    前进一帧
  ←  / A    后退一帧
  空格       播放/暂停
  G         跳转到指定帧（弹出输入框）
  S         跳过指定帧数（弹出输入框）
  Q / ESC   退出
"""

import sys
import tkinter as tk
from tkinter import simpledialog

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

# Windows 系统字体路径（按优先级尝试）
_FONT_CANDIDATES = [
    "C:/Windows/Fonts/msyh.ttc",    # 微软雅黑
    "C:/Windows/Fonts/simhei.ttf",  # 黑体
    "C:/Windows/Fonts/simsun.ttc",  # 宋体
]

def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def put_cn_text(
    img: np.ndarray,
    text: str,
    pos: tuple[int, int],
    size: int = 18,
    color: tuple[int, int, int] = (0, 255, 0),
) -> np.ndarray:
    """在 BGR 图像上渲染中文文字，返回新图像。"""
    pil_img = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
    draw = ImageDraw.Draw(pil_img)
    font = _load_font(size)
    # PIL 使用 RGB
    draw.text(pos, text, font=font, fill=(color[2], color[1], color[0]))
    return cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)


def draw_overlay(frame: np.ndarray, current: int, total: int, paused: bool) -> np.ndarray:
    """绘制顶部状态栏和底部操作提示。"""
    h, w = frame.shape[:2]
    out = frame.copy()

    # 半透明背景条
    overlay = out.copy()
    cv2.rectangle(overlay, (0, 0), (w, 38), (0, 0, 0), -1)
    cv2.rectangle(overlay, (0, h - 32), (w, h), (0, 0, 0), -1)
    cv2.addWeighted(overlay, 0.55, out, 0.45, 0, out)

    status = f"帧: {current + 1} / {total}  {'[暂停]' if paused else '[播放]'}"
    hint = "→/D:下一帧  ←/A:上一帧  空格:播放暂停  G:跳转到帧  S:跳过帧数  Q/ESC:退出"

    out = put_cn_text(out, status, (8, 6),  size=20, color=(0, 255, 0))
    out = put_cn_text(out, hint,   (8, h - 30), size=15, color=(200, 200, 200))
    return out


def ask_int(title: str, prompt: str, min_val: int, max_val: int) -> int | None:
    """用 tkinter 弹框向用户获取整数，避免终端输入焦点问题。"""
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    val = simpledialog.askinteger(
        title,
        f"{prompt}\n范围：{min_val} ~ {max_val}",
        minvalue=min_val,
        maxvalue=max_val,
        parent=root,
    )
    root.destroy()
    return val


def main(video_path: str) -> None:
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        print(f"无法打开视频: {video_path}")
        sys.exit(1)

    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    print(f"\n视频信息:")
    print(f"  路径     : {video_path}")
    print(f"  分辨率   : {w} x {h}")
    print(f"  帧率     : {fps:.2f} fps")
    print(f"  总帧数   : {total_frames}")
    print(f"  时长     : {total_frames / fps:.2f} 秒\n")

    cv2.namedWindow("视频帧查看器", cv2.WINDOW_NORMAL)
    cv2.resizeWindow("视频帧查看器", min(w, 1280), min(h, 720))

    current_frame = 0
    paused = True

    def seek(target: int) -> None:
        nonlocal current_frame
        target = max(0, min(target, total_frames - 1))
        cap.set(cv2.CAP_PROP_POS_FRAMES, target)
        current_frame = target

    while True:
        if not paused:
            ret, frame = cap.read()
            if not ret:
                paused = True
                seek(total_frames - 1)
                continue
            current_frame = int(cap.get(cv2.CAP_PROP_POS_FRAMES)) - 1
        else:
            cap.set(cv2.CAP_PROP_POS_FRAMES, current_frame)
            ret, frame = cap.read()
            if not ret:
                print("读取帧失败，退出。")
                break

        display = draw_overlay(frame, current_frame, total_frames, paused)
        cv2.imshow("视频帧查看器", display)

        delay = 1 if not paused else 0
        key = cv2.waitKey(delay) & 0xFF

        if key in (ord('q'), ord('Q'), 27):        # Q / ESC：退出
            break
        elif key == 32:                             # 空格：播放/暂停
            paused = not paused
        elif key in (83, ord('d'), ord('D')):       # → / D：下一帧
            paused = True
            seek(current_frame + 1)
        elif key in (81, ord('a'), ord('A')):       # ← / A：上一帧
            paused = True
            seek(current_frame - 1)
        elif key in (ord('g'), ord('G')):           # G：跳转到指定帧
            paused = True
            val = ask_int("跳转到帧", f"请输入目标帧编号（1-based）", 1, total_frames)
            if val is not None:
                seek(val - 1)
                print(f"  已跳转到第 {val} 帧。")
        elif key in (ord('s'), ord('S')):           # S：跳过指定帧数
            paused = True
            remaining = total_frames - 1 - current_frame
            if remaining <= 0:
                print("  已在最后一帧，无法继续跳过。")
            else:
                val = ask_int("跳过帧数", f"请输入要跳过的帧数", 1, remaining)
                if val is not None:
                    seek(current_frame + val)
                    print(f"  已跳过 {val} 帧，当前第 {current_frame + 1} 帧。")

    cap.release()
    cv2.destroyAllWindows()
    print("已退出。")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python scripts/video_frame_viewer.py <视频路径>")
        sys.exit(1)
    main(sys.argv[1])
