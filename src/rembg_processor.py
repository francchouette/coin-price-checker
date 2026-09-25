#!/usr/bin/env python3
"""
rembg画像処理ヘルパースクリプト

別環境（~/rembg_env）で動作するため、subprocess経由で呼び出す。
主処理: 入力画像 → 背景除去 → リサイズ → pngquant圧縮 → 透過PNG出力

使い方（CLI）:
    /Users/user/rembg_env/bin/python -m src.rembg_processor INPUT_PATH OUTPUT_PATH [--size 400]
"""

import argparse
import subprocess
import sys
from pathlib import Path


PNGQUANT = '/usr/local/opt/pngquant/bin/pngquant'


def process_image(input_path: str, output_path: str, target_size: int = 400) -> bool:
    """
    画像を rembg で背景除去 → リサイズ → pngquant圧縮

    Args:
        input_path: 入力画像パス
        output_path: 出力PNG パス
        target_size: 正方形リサイズ後のサイズ（px）

    Returns:
        bool: 成功時 True
    """
    try:
        from rembg import remove, new_session
        from PIL import Image
    except ImportError as e:
        print(f"ERROR: 必要モジュールがインポートできません: {e}", file=sys.stderr)
        print("このスクリプトは ~/rembg_env/bin/python で実行する必要があります", file=sys.stderr)
        return False

    in_path = Path(input_path)
    out_path = Path(output_path)
    if not in_path.exists():
        print(f"ERROR: 入力ファイルが存在しません: {in_path}", file=sys.stderr)
        return False

    # 1. rembg背景除去
    input_bytes = in_path.read_bytes()
    session = new_session('u2net')
    output_bytes = remove(input_bytes, session=session)

    # 2. PIL でリサイズ
    from io import BytesIO
    img = Image.open(BytesIO(output_bytes))
    img.thumbnail((target_size, target_size), Image.LANCZOS)

    # 3. 一時PNG保存
    temp_png = out_path.with_suffix('.temp.png')
    img.save(temp_png, format='PNG', optimize=True)

    # 4. pngquant圧縮
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if not Path(PNGQUANT).exists():
        # pngquant が無い場合はそのまま出力（圧縮なし）
        temp_png.replace(out_path)
        print(f"WARN: pngquant未インストール、圧縮スキップ", file=sys.stderr)
    else:
        result = subprocess.run([
            PNGQUANT, '--quality=70-90', '--strip', '--force',
            '--output', str(out_path), str(temp_png),
        ], capture_output=True, text=True)
        if result.returncode != 0:
            # pngquant失敗時はリサイズ済みをそのまま使用
            temp_png.replace(out_path)
            print(f"WARN: pngquant失敗、リサイズのみ使用: {result.stderr}", file=sys.stderr)
        else:
            temp_png.unlink(missing_ok=True)

    return out_path.exists()


def main():
    parser = argparse.ArgumentParser(description="rembg画像処理（背景除去+リサイズ+圧縮）")
    parser.add_argument('input', help='入力画像パス')
    parser.add_argument('output', help='出力PNG パス')
    parser.add_argument('--size', type=int, default=400, help='リサイズ目標サイズ（px、デフォルト400）')
    args = parser.parse_args()

    success = process_image(args.input, args.output, target_size=args.size)
    sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()
