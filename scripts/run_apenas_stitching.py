"""
Roda SOMENTE o protocolo 3 (stitching + exportacao).

Util para remontar a placa a partir de quadrantes ja recortados - por
exemplo depois de substituir manualmente um quadrante ruim.

Uso:
    python scripts/run_apenas_stitching.py
    python scripts/run_apenas_stitching.py --entrada data/03_recortadas
    python scripts/run_apenas_stitching.py --escala 0.5   # previa rapida
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
from src.pipeline import etapa_stitching_e_exportacao, novo_lote_id  # noqa: E402
from src.utils import (  # noqa: E402
    SumarioLote,
    configurar_logging,
    garantir_pastas,
    medir_memoria_mb,
    obter_logger,
)


def construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="YellowTrap Pipeline - apenas stitching + exportacao "
                    "(protocolo 3)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--entrada", type=Path, default=settings.PASTA_RECORTADAS,
                        help="pasta com os quadrantes a1..d10")
    parser.add_argument("--saida", type=Path, default=None,
                        help="pasta de destino (default: "
                             "data/04_placas_montadas/<lote_id>)")
    parser.add_argument("--lote-id", default=None, help="identificador do lote")
    parser.add_argument("--escala", type=float, default=None,
                        help="escala de carregamento dos quadrantes "
                             "(1.0 = resolucao cheia; <1.0 gera previa rapida)")
    parser.add_argument("--verbose", action="store_true", help="log DEBUG no console")
    return parser


def main() -> int:
    args = construir_parser().parse_args()
    configurar_logging(nivel_console="DEBUG" if args.verbose else None)
    logger = obter_logger("run_apenas_stitching")

    lote_id = args.lote_id or novo_lote_id()
    pasta_saida = args.saida or (settings.PASTA_PLACAS / lote_id)
    garantir_pastas(pasta_saida, settings.PASTA_LOGS)

    if args.escala is not None and args.escala < 1.0:
        logger.warning(
            "Escala %.2f: a placa NAO sai em resolucao cheia. Use apenas para "
            "previa - nao para o arquivo cientifico.", args.escala,
        )

    inicio = time.perf_counter()
    sumario = SumarioLote(lote_id=lote_id)
    try:
        etapa_stitching_e_exportacao(sumario, args.entrada, pasta_saida,
                                     escala=args.escala)
        sumario.sucesso = True
    except Exception as exc:
        logger.exception("Stitching abortado: %s", exc)
        sumario.falhas.append({"arquivo": None, "etapa": "stitching",
                               "motivo": f"{type(exc).__name__}: {exc}"})

    sumario.duracao_seg = time.perf_counter() - inicio
    sumario.memoria_pico_mb = medir_memoria_mb()
    for linha in sumario.linhas_relatorio():
        logger.info(linha)

    if sumario.sucesso:
        sumario.salvar_json(pasta_saida / "sumario.json")
    return 0 if sumario.sucesso else 1


if __name__ == "__main__":
    raise SystemExit(main())
