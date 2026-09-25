"""
新カラーミー商品管理シートの商品説明を500文字程度に短縮するスクリプト

対象:
- シート: 新カラーミー商品管理
- 列: BA列（商品説明）- 500文字を超える行のみ処理

使用方法:
    python -m src.cm_shorten_descriptions [--dry-run] [--limit N] [-v]

オプション:
    --dry-run: 実際の更新を行わない（確認用）
    --limit N: 処理件数を制限
    -v, --verbose: 詳細ログを出力
"""

import argparse
import copy
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config import Config
from src.spreadsheet import SpreadsheetClient
from src.cm_sheet_columns import Col, get_cell, cell_ref

logger = logging.getLogger(__name__)

MAX_LENGTH = 500

SHORTEN_PROMPT = """あなたは貴金属コイン・地金のECサイトの商品説明の編集者です。

以下の商品説明を{max_length}文字前後（450〜{max_length}文字）の日本語に編集してください。

## 要件
- {max_length}文字以内に収めること（厳守）
- できるだけ{max_length}文字に近づけること（短くしすぎない）
- 重要な商品特徴・スペック・魅力をできる限り保持すること
- 【品位】【重量】【直径】【発行年】などの【】囲みのスペック表記は必ず残すこと
- 自然で読みやすい日本語であること
- HTMLタグがあれば除去すること
- 編集後の文章のみを出力すること（前置き・説明は不要）

## 出力フォーマット
以下の順番で出力すること：
1. 【品位】【重量】【直径】などの【】囲みスペック行（元の順番・表記を維持）
2. 空行
3. 商品説明文（要約・編集した本文）

## 元の商品説明
{description}
"""


def init_gemini():
    """Vertex AI Gemini 2.5 Pro を初期化して返す"""
    try:
        import vertexai
        from vertexai.generative_models import GenerativeModel
        vertexai.init(project="coin-price-tracker-479614", location="us-central1")
        model = GenerativeModel("gemini-2.5-pro")
        logger.info("Vertex AI Gemini 2.5 Pro 初期化完了")
        return model
    except Exception as e:
        logger.error(f"Vertex AI 初期化エラー: {e}")
        return None


def shorten_description(model, description: str, max_length: int = MAX_LENGTH) -> str:
    """
    Gemini を使って商品説明を指定文字数以内に短縮する

    Returns:
        str: 短縮後の説明（失敗時は空文字列）
    """
    prompt = SHORTEN_PROMPT.format(
        max_length=max_length,
        description=description
    )
    try:
        response = model.generate_content(prompt)
        result = response.text.strip()
        # AIが文字数制限を超えた場合、最後の句点で切る
        if len(result) > max_length:
            truncated = result[:max_length]
            last_period = truncated.rfind("。")
            result = truncated[:last_period + 1] if last_period != -1 else truncated
        return result
    except Exception as e:
        logger.error(f"  Gemini 呼び出しエラー: {e}")
        return ""


def _has_issue(description: str) -> bool:
    """問題のある説明かどうか判定する"""
    if '<br>' in description or '<strong>' in description or '<span' in description:
        return True  # HTMLタグあり
    if '【' not in description:
        return True  # スペック行なし
    if '【' in description and not description.startswith('【'):
        return True  # スペック行が先頭でない
    if not description.endswith(('。', '！', '？', '♪', 'い', 'す', 'か', '）', '」')):
        return True  # 末尾が途中で切れている
    return False


def shorten_descriptions(
    dry_run: bool = False,
    limit: int = None,
    force: bool = False,
    fix_issues: bool = False,
    start_row: int = None,
    end_row: int = None,
) -> dict:
    """
    新カラーミー商品管理シートのBA列（商品説明）を500文字以内に短縮する

    Args:
        dry_run: Trueの場合、実際の更新を行わない
        limit: 処理件数制限（Noneで全件）
        force: Trueの場合、500文字以下の行も再処理する
        fix_issues: Trueの場合、問題のある行（HTML・スペック行位置・末尾切れ）のみ再処理する
        start_row: 処理対象の開始行（含む、Noneで先頭）
        end_row: 処理対象の終了行（含む、Noneで最後）

    Returns:
        dict: 処理結果の統計
    """
    client = SpreadsheetClient()
    if not client.connect():
        logger.error("スプレッドシートへの接続に失敗しました")
        return {"error": "connection_failed"}

    model = None
    if not dry_run:
        model = init_gemini()
        if not model:
            return {"error": "gemini_init_failed"}

    try:
        sheet = client._spreadsheet.worksheet(Config.SHEET_COLORME_V2)
        all_data = sheet.get_all_values()

        if len(all_data) <= 1:
            logger.info("データがありません")
            return {"processed": 0}

        logger.info(f"総行数: {len(all_data) - 1}件")

        # 対象行を抽出
        target_rows = []
        for row_idx, row in enumerate(all_data[1:], start=2):
            # 行範囲フィルタ
            if start_row is not None and row_idx < start_row:
                continue
            if end_row is not None and row_idx > end_row:
                continue
            description = get_cell(row, Col.EXPL)
            if not description:
                continue
            if fix_issues:
                if _has_issue(description):
                    target_rows.append({"row_idx": row_idx, "product_name": get_cell(row, Col.NAME), "description": description})
            elif force or len(description) > MAX_LENGTH:
                target_rows.append({"row_idx": row_idx, "product_name": get_cell(row, Col.NAME), "description": description})

        if fix_issues:
            label = "問題あり行（--fix-issues）"
        elif force:
            label = "全件（--force）"
        else:
            label = f"{MAX_LENGTH}文字超"
        if start_row is not None or end_row is not None:
            range_label = f"行{start_row or '先頭'}〜{end_row or '最後'}"
            label = f"{label} / {range_label}"
        logger.info(f"短縮対象（{label}）: {len(target_rows)}件")

        if limit:
            target_rows = target_rows[:limit]
            logger.info(f"件数制限適用: {len(target_rows)}件")

        if not target_rows:
            logger.info("短縮対象の商品がありません")
            return {"processed": 0, "shortened": 0, "failed": 0}

        if dry_run:
            logger.info("\n[ドライラン] 対象商品一覧:")
            for item in target_rows:
                logger.info(
                    f"  行{item['row_idx']}: {item['product_name'][:40]} "
                    f"({len(item['description'])}文字)"
                )
            return {"processed": len(target_rows), "shortened": 0, "failed": 0, "dry_run": True}

        # バッチ更新用リスト
        update_cells = []
        stats = {"processed": 0, "shortened": 0, "failed": 0}

        for item in target_rows:
            row_idx = item["row_idx"]
            product_name = item["product_name"]
            description = item["description"]

            logger.info(
                f"\n[{stats['processed'] + 1}/{len(target_rows)}] "
                f"行{row_idx}: {product_name[:40]} ({len(description)}文字)"
            )

            shortened = shorten_description(model, description)

            if shortened:
                update_cells.append({
                    'range': cell_ref(Col.EXPL, row_idx),
                    'values': [[shortened]]
                })
                stats["shortened"] += 1
                logger.info(f"  → {len(shortened)}文字に短縮")
            else:
                stats["failed"] += 1
                logger.warning("  短縮に失敗（スキップ）")

            stats["processed"] += 1

            # 10件ごとに中間保存
            if stats["processed"] % 10 == 0 and update_cells:
                logger.info(f"\n[中間保存] {stats['processed']}件処理完了 - スプレッドシートに保存中...")
                for retry in range(3):
                    try:
                        sheet.batch_update(copy.deepcopy(update_cells), value_input_option='RAW')
                        logger.info(f"[中間保存] {len(update_cells)}セルを保存しました")
                        update_cells = []
                        break
                    except Exception as e:
                        if retry < 2:
                            logger.warning(f"[中間保存] 保存エラー（リトライ {retry + 1}/3）: {e}")
                            time.sleep(5)
                        else:
                            logger.error(f"[中間保存] 保存エラー（最終）: {e}")
                            update_cells = []
                time.sleep(1)

            # API制限対策
            time.sleep(0.5)

        # 最終保存
        if update_cells:
            logger.info(f"\n[最終保存] 残り{len(update_cells)}セルをスプレッドシートに保存中...")
            for retry in range(3):
                try:
                    sheet.batch_update(copy.deepcopy(update_cells), value_input_option='RAW')
                    logger.info(f"[最終保存] 完了: {len(update_cells)}セル")
                    break
                except Exception as e:
                    if retry < 2:
                        logger.warning(f"[最終保存] 保存エラー（リトライ {retry + 1}/3）: {e}")
                        time.sleep(5)
                    else:
                        logger.error(f"[最終保存] 保存エラー（最終）: {e}")

        return stats

    except Exception as e:
        logger.error(f"エラーが発生しました: {e}")
        import traceback
        traceback.print_exc()
        return {"error": str(e)}


def main():
    parser = argparse.ArgumentParser(
        description='新カラーミー商品管理シートの商品説明を500文字以内に短縮'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='ドライラン（実際の更新なし）'
    )
    parser.add_argument(
        '--limit',
        type=int,
        default=None,
        help='処理件数制限'
    )
    parser.add_argument(
        '--force',
        action='store_true',
        help='500文字以下の行も強制再処理する'
    )
    parser.add_argument(
        '--fix-issues',
        action='store_true',
        help='HTMLあり・スペック行位置ずれ・末尾切れの行のみ再処理する'
    )
    parser.add_argument(
        '--start',
        type=int,
        default=None,
        help='処理開始行（含む）'
    )
    parser.add_argument(
        '--end',
        type=int,
        default=None,
        help='処理終了行（含む）'
    )
    parser.add_argument(
        '-v', '--verbose',
        action='store_true',
        help='詳細ログを出力'
    )

    args = parser.parse_args()

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )

    logger.info("=" * 60)
    logger.info("商品説明短縮スクリプト（新カラーミー商品管理 BA列）")
    logger.info("=" * 60)
    logger.info(f"モード: {'ドライラン' if args.dry_run else '本番'}")
    logger.info(f"最大文字数: {MAX_LENGTH}文字")
    if args.force:
        logger.info("強制再処理: ON（500文字以下も対象）")
    if args.fix_issues:
        logger.info("問題修正モード: ON（HTML・スペック位置・末尾切れのみ）")
    if args.limit:
        logger.info(f"件数制限: {args.limit}件")
    if args.start is not None or args.end is not None:
        logger.info(f"行範囲: {args.start or '先頭'}〜{args.end or '最後'}")
    logger.info("=" * 60)

    start_time = time.time()
    result = shorten_descriptions(
        dry_run=args.dry_run,
        limit=args.limit,
        force=args.force,
        fix_issues=args.fix_issues,
        start_row=args.start,
        end_row=args.end,
    )
    elapsed = time.time() - start_time

    logger.info("\n" + "=" * 60)
    logger.info("処理完了")
    if "error" not in result:
        logger.info(f"  処理件数: {result.get('processed', 0)}件")
        if not result.get("dry_run"):
            logger.info(f"  短縮成功: {result.get('shortened', 0)}件")
            logger.info(f"  失敗: {result.get('failed', 0)}件")
    else:
        logger.error(f"  エラー: {result['error']}")
    logger.info(f"  所要時間: {elapsed:.1f}秒")
    logger.info("=" * 60)

    return 0 if "error" not in result else 1


if __name__ == "__main__":
    sys.exit(main())
