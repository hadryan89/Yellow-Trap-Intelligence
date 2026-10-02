"""
Roda SOMENTE a montagem das placas (protocolo 3) sobre quadrantes ja recortados.

Util para remontar uma placa depois de substituir a mao um quadrante ruim,
ou para gerar uma previa leve sem refazer o recorte. Os quadrantes da pasta
de entrada sao tomados em ordem natural (VARD14A1, VARD14A2, ..., VARD14A10,
VARD14B1, ...) e juntados de 40 em 40 no formato da placa (4 faixas de 10).

Use --filtro para escolher UMA armadilha numa pasta que tem varias:

    python scripts/run_apenas_montagem.py --filtro "VARD14*"
    python scripts/run_apenas_montagem.py --entrada data/03_recortadas
    python scripts/run_apenas_montagem.py --filtro "VARD14*" --escala 0.25   # previa

Codigo de saida:
    0  todas as placas montadas
    1  nada para montar (pasta vazia / inexistente)
    2  alguma placa falhou - veja o log
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
from src import montagem as mod_montagem  # noqa: E402
from src.exportacao import exportar_placa  # noqa: E402
from src.pipeline import novo_lote_id  # noqa: E402
from src.utils import (  # noqa: E402
    SumarioLote,
    configurar_logging,
    formatar_duracao,
    medir_memoria_mb,
    obter_logger,
)


def construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="YellowTrap Pipeline - apenas a montagem das placas "
                    "(protocolo 3)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--entrada", type=Path, default=settings.PASTA_RECORTADAS,
                        help="pasta com os quadrantes recortados")
    parser.add_argument("--filtro", default="*",
                        help="padrao (glob) dos quadrantes a usar, ex.: 'VARD14*'")
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
    logger = obter_logger("run_apenas_montagem")

    lote_id = args.lote_id or novo_lote_id()
    pasta_saida = args.saida or (settings.PASTA_PLACAS / lote_id)
    if args.escala is not None and not 0 < args.escala <= 1:
        print("ERRO: --escala precisa estar em (0, 1]", file=sys.stderr)
        return 1
    if args.escala is not None and args.escala < 1.0:
        logger.warning(
            "Escala %.2f: a placa NAO sai em resolucao cheia. Use apenas para "
            "previa - nao para o arquivo cientifico.", args.escala,
        )

    if not args.entrada.is_dir():
        logger.error("Pasta de quadrantes nao existe: %s", args.entrada)
        return 1
    quadrantes = [p for p in mod_montagem.listar_quadrantes(args.entrada)
                  if p.match(args.filtro)]
    if not quadrantes:
        logger.error("Nenhum quadrante em %s (filtro %r).", args.entrada, args.filtro)
        return 1

    inicio = time.perf_counter()
    sumario = SumarioLote(lote_id=lote_id, modo="montagem")
    sumario.total_entrada = len(quadrantes)
    for plano in mod_montagem.planejar_placas(quadrantes):
        try:
            placa, estatisticas = mod_montagem.montar_placa_do_plano(
                plano, escala=args.escala)
            estatisticas["arquivos"] = exportar_placa(placa, pasta_saida, plano.nome)
            del placa
            sumario.placas.append(estatisticas)
        except Exception as exc:
            logger.exception("Placa %s abortada: %s", plano.nome, exc)
            sumario.adicionar_falha(plano.nome, "montagem",
                                    f"{type(exc).__name__}: {exc}")

    sumario.sucesso = bool(sumario.placas)
    sumario.pasta_placas = str(pasta_saida)
    sumario.duracao_seg = time.perf_counter() - inicio
    sumario.memoria_pico_mb = medir_memoria_mb()
    logger.info("=" * 68)
    logger.info("MONTAGEM %s: %d quadrante(s) -> %d placa(s), %d falha(s), %s",
                lote_id, len(quadrantes), len(sumario.placas),
                sumario.total_falhas, formatar_duracao(sumario.duracao_seg))
    for placa in sumario.placas:
        logger.info("  %s  (%d quadrante(s), %d placeholder(s))", placa["nome"],
                    placa["quadrantes"], placa["placeholders"])
    logger.info("  Pasta das placas: %s", pasta_saida)
    logger.info("=" * 68)
    if sumario.placas:
        sumario.salvar_json(pasta_saida / "sumario_montagem.json")

    if not sumario.sucesso:
        return 1
    return 0 if sumario.total_falhas == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
