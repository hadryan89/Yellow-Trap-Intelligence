"""
Roda SOMENTE o recorte, preservando os nomes dos arquivos de entrada.

Atalho para `run_pipeline.py --modo recorte` com a entrada apontando para
data/02_renomeadas/. Util para reprocessar recortes sem refazer a nomeacao,
ou para testar o efeito de um formato de saida diferente.

Uso:
    python scripts/run_apenas_recorte.py
    python scripts/run_apenas_recorte.py --entrada data/02_renomeadas --workers 6
    python scripts/run_apenas_recorte.py --formato tiff --retomar
    python scripts/run_apenas_recorte.py --entrada data/azuis --perfil azul
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from config import settings  # noqa: E402
from src.exportacao import FORMATOS_VALIDOS  # noqa: E402
from src.opcoes import OpcoesProcessamento  # noqa: E402
from src.pipeline import executar_processamento  # noqa: E402
from src.utils import configurar_logging  # noqa: E402


def construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="YellowTrap Pipeline - apenas o recorte (sem renomear)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--entrada", type=Path, default=settings.PASTA_RENOMEADAS,
                        help="pasta com as fotos a recortar")
    parser.add_argument("--saida", type=Path, default=settings.PASTA_RECORTADAS,
                        help="pasta de destino dos quadrantes")
    parser.add_argument("--workers", type=int, default=None,
                        help="numero de processos paralelos (default: CPUs - 1)")
    parser.add_argument("--formato", choices=list(FORMATOS_VALIDOS), default=None,
                        help="formato dos quadrantes recortados")
    parser.add_argument("--perfil", choices=list(settings.RECORTE_PERFIS),
                        default=None,
                        help="cor da armadilha: auto atende amarela e azul "
                             "(padrao); amarela/azul travam a faixa esperada "
                             "do quadrante num lote dificil")
    parser.add_argument("--borda", choices=list(settings.RECORTE_BORDAS_VALIDAS),
                        default=None,
                        help="onde o corte cai em relacao a linha da grade: "
                             "linha = o traco inteiro fica no quadrante "
                             "(padrao) | dentro = quadrante sem traco | "
                             "meia_linha = metade do traco de cada lado")
    parser.add_argument("--limite", type=int, default=None,
                        help="processa apenas as N primeiras fotos")
    parser.add_argument("--retomar", dest="pular_existentes", action="store_true",
                        default=None,
                        help="pula fotos cujo quadrante ja existe na saida")
    parser.add_argument("--limpar-saida", dest="limpar_saida", action="store_true",
                        default=None, help="esvazia a pasta de recortes antes")
    parser.add_argument("--lote-id", default=None, help="identificador do lote")
    parser.add_argument("--verbose", action="store_true", help="log DEBUG no console")
    return parser


def main() -> int:
    args = construir_parser().parse_args()
    configurar_logging(nivel_console="DEBUG" if args.verbose else None)

    sumario = executar_processamento(OpcoesProcessamento(
        modo=settings.MODO_RECORTE,
        pasta_entrada=args.entrada,
        pasta_recortadas=args.saida,
        lote_id=args.lote_id,
        workers=args.workers,
        formato=args.formato,
        perfil=args.perfil,
        borda=args.borda,
        limite=args.limite,
        pular_existentes=args.pular_existentes,
        limpar_saida=args.limpar_saida,
    ))
    if not sumario.sucesso:
        return 1
    return 0 if sumario.total_falhas == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
