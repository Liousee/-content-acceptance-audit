#!/usr/bin/env python3
"""裁剪区域并放大后 OCR：生成裁剪图，供后续 swift ocr.swift 复核。

用法:
  python3 crop_ocr.py <img> <x0> <y0> <x1> <y1> [scale]

坐标为 Vision 归一化坐标（原点左下）；默认放大 4 倍。
输出: <tmp>/ys_bbox/_crop.png
"""
import argparse
import os
import tempfile

from PIL import Image


def main():
    ap = argparse.ArgumentParser(description='Crop and upscale a region for OCR.')
    ap.add_argument('image')
    ap.add_argument('x0', type=float)
    ap.add_argument('y0', type=float)
    ap.add_argument('x1', type=float)
    ap.add_argument('y1', type=float)
    ap.add_argument('scale', type=float, nargs='?', default=4)
    args = ap.parse_args()

    im = Image.open(args.image)
    W, H = im.size
    # Vision y from bottom → PIL y from top
    top = int((1 - args.y1) * H)
    bot = int((1 - args.y0) * H)
    left = int(args.x0 * W)
    right = int(args.x1 * W)
    crop = im.crop((max(0, left - 10), max(0, top - 10), min(W, right + 10), min(H, bot + 10)))
    crop = crop.resize((crop.width * int(args.scale), crop.height * int(args.scale)), Image.LANCZOS)
    out_dir = os.path.join(tempfile.gettempdir(), 'ys_bbox')
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, '_crop.png')
    crop.save(out)
    print('crop ->', out, crop.size)


if __name__ == '__main__':
    main()
