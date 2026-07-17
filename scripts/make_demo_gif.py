"""Assemble README demo.gif from captured UI frames with captions."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
FRAMES = ROOT / "docs" / "assets" / "demo-frames"
OUT = ROOT / "docs" / "assets" / "demo.gif"
TARGET_WIDTH = 960
CAPTION_H = 44


def _font(size: int = 22) -> ImageFont.ImageFont:
    candidates = [
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\msyhbd.ttc",
        r"C:\Windows\Fonts\simhei.ttf",
        r"C:\Windows\Fonts\simsun.ttc",
        "/System/Library/Fonts/PingFang.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    ]
    for path in candidates:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size=size)
            except OSError:
                continue
    return ImageFont.load_default()


def prepare_frame(path: Path, caption: str, font: ImageFont.ImageFont) -> Image.Image:
    img = Image.open(path).convert("RGB")
    if img.width != TARGET_WIDTH:
        ratio = TARGET_WIDTH / img.width
        img = img.resize((TARGET_WIDTH, max(1, int(img.height * ratio))), Image.Resampling.LANCZOS)

    canvas = Image.new("RGB", (img.width, img.height + CAPTION_H), (15, 23, 42))
    canvas.paste(img, (0, CAPTION_H))
    draw = ImageDraw.Draw(canvas)
    draw.rectangle((0, 0, canvas.width, CAPTION_H), fill=(30, 64, 175))
    draw.text((16, 10), caption, fill=(248, 250, 252), font=font)
    # GIF palette stability
    return canvas.convert("P", palette=Image.Palette.ADAPTIVE, colors=128)


def main() -> None:
    FRAMES.mkdir(parents=True, exist_ok=True)

    # Prefer CDP captures; each caption used at most once.
    sequence = [
        ("01-welcome.png", "1/5 欢迎使用 · 简历到评价的完整流程"),
        ("03-candidate-selected.png", "2/5 选择候选人 · Agent 同步档案信息"),
        ("04-brief-cdp.png", "3/5 面试简报 · 匹配分析与风险信号"),
        ("04-brief.png", "3/5 面试简报 · 匹配分析与风险信号"),
        ("09-questions-cdp.png", "4/5 问题清单 · 面试考察题列表"),
        ("05-questions.png", "4/5 问题清单 · 面试考察题列表"),
        ("08-cdp-live.png", "5/5 实时转写 · AI 追问建议 · 评价报告"),
    ]

    font = _font(20)
    frames: list[Image.Image] = []
    used_steps: set[str] = set()
    for name, caption in sequence:
        path = FRAMES / name
        if not path.exists():
            continue
        step = caption.split(" ", 1)[0]  # e.g. 3/5
        if step in used_steps:
            continue
        frames.append(prepare_frame(path, caption, font))
        used_steps.add(step)
        print(f"frame + {name}")

    if len(frames) < 3:
        raise SystemExit(f"need >=3 frames, got {len(frames)}")

    durations = [2200] * (len(frames) - 1) + [3600]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    frames[0].save(
        OUT,
        save_all=True,
        append_images=frames[1:],
        duration=durations,
        loop=0,
        optimize=True,
    )
    size_kb = OUT.stat().st_size / 1024
    print(f"wrote {OUT} ({size_kb:.1f} KB, {len(frames)} frames)")


if __name__ == "__main__":
    main()
