"""
Roda SOMENTE o protocolo 2 (recorte do quadrante central), em paralelo.

Util para reprocessar recortes sem refazer a renomeacao, ou para testar o
efeito de um formato de saida diferente.

Uso:
    python scripts/run_apenas_recorte.py
    python scripts/run_apenas_recorte.py --entrada data/02_renomeadas --workers 6
    python scripts/run_apenas_recorte.py --formato tiff
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from config import settings  # noqa: E402
from src.pipeline import etapa_recorte, novo_lote_id  # noqa: E402
from src.utils import (  # noqa: E402
    SumarioLote,
    configurar_logging,
    garantir_pastas,
    medir_memoria_mb,
    obter_logger,
)


def construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="YellowTrap Pipeline - apenas o recorte (protocolo 2)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--entrada", type=Path, default=settings.PASTA_RENOMEADAS,
                        help="pasta com as fotos a recortar")
    parser.add_argument("--saida", type=Path, default=settings.PASTA_RECORTADAS,
                        help="pasta de destino dos quadrantes")
    parser.add_argument("--workers", type=int, default=None,
                        help="numero de processos paralelos (default: CPUs - 1)")
    parser.add_argument("--formato", choices=["png", "tiff", "jpg_max"],
                        default=settings.RECORTE_FORMATO_SAIDA,
                        help="formato dos quadrantes recortados")
    parser.add_argument("--lote-id", default=None, help="identificador do lote")
    parser.add_argument("--verbose", action="store_true", help="log DEBUG no console")
    return parser


def main() -> int:
    args = construir_parser().parse_args()
    configurar_logging(nivel_console="DEBUG" if args.verbose else None)
    logger = obter_logger("run_apenas_recorte")

    garantir_pastas(args.saida, settings.PASTA_FALHAS, settings.PASTA_LOGS)

    inicio = time.perf_counter()
    sumario = SumarioLote(lote_id=args.lote_id or novo_lote_id())
    try:
        etapa_recorte(sumario, args.entrada, args.saida,
                      num_workers=args.workers, formato=args.formato)
        sumario.sucesso = sumario.recortadas_ok > 0
    except Exception as exc:
        logger.exception("Recorte abortado: %s", exc)
        sumario.falhas.append({"arquivo": None, "etapa": "recorte",
                               "motivo": f"{type(exc).__name__}: {exc}"})

    sumario.duracao_seg = time.perf_counter() - inicio
    sumario.memoria_pico_mb = medir_memoria_mb()
    for linha in sumario.linhas_relatorio():
        logger.info(linha)
    return 0 if sumario.sucesso else 1


if __name__ == "__main__":
    raise SystemExit(main())
