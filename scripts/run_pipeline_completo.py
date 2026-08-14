"""
Modo BATCH - roda os 3 protocolos em sequencia, uma vez.

Uso:
    python scripts/run_pipeline_completo.py
    python scripts/run_pipeline_completo.py --entrada "D:\\fotos\\lote_07" --workers 4
    python scripts/run_pipeline_completo.py --formato tiff --zip
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from config import settings  # noqa: E402
from src.pipeline import executar_pipeline_completo  # noqa: E402
from src.utils import configurar_logging  # noqa: E402


def construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="YellowTrap Pipeline - execucao completa (renomeacao -> "
                    "recorte -> stitching -> exportacao)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--entrada", type=Path, default=settings.PASTA_ENTRADA,
                        help="pasta com as fotos brutas")
    parser.add_argument("--lote-id", default=None,
                        help="identificador do lote (default: timestamp)")
    parser.add_argument("--workers", type=int, default=None,
                        help="numero de processos paralelos (default: CPUs - 1)")
    parser.add_argument("--formato", choices=["png", "tiff", "jpg_max"],
                        default=settings.RECORTE_FORMATO_SAIDA,
                        help="formato dos quadrantes recortados")
    parser.add_argument("--zip", dest="zip", action="store_true", default=None,
                        help="cria o ZIP (ZIP_STORED) das fotos renomeadas")
    parser.add_argument("--sem-zip", dest="zip", action="store_false",
                        help="nao cria o ZIP das fotos renomeadas")
    parser.add_argument("--pular-renomeacao", action="store_true",
                        help="assume que as fotos ja estao nomeadas a1..d10 e "
                             "recorta direto da pasta de entrada")
    parser.add_argument("--verbose", action="store_true",
                        help="log DEBUG no console")
    return parser


def main() -> int:
    args = construir_parser().parse_args()
    configurar_logging(nivel_console="DEBUG" if args.verbose else None)

    sumario = executar_pipeline_completo(
        pasta_entrada=args.entrada,
        lote_id=args.lote_id,
        num_workers=args.workers,
        formato_recorte=args.formato,
        criar_zip=args.zip,
        pular_renomeacao=args.pular_renomeacao,
    )
    return 0 if sumario.sucesso else 1


if __name__ == "__main__":
    # Guarda obrigatoria no Windows: o ProcessPoolExecutor usa 'spawn' e
    # reimporta este modulo em cada worker.
    raise SystemExit(main())
