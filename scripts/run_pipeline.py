"""
Modo BATCH - nomeia e recorta um lote, uma vez.

Dois modos de nomeacao (e um terceiro que nao renomeia):

    --modo grid         a1..d10, ate 40 fotos por lote (padrao historico)
    --modo sequencial   VARD1, VARD2, VARD3, ... sem limite
    --modo recorte      preserva o nome de origem

Uso:
    python scripts/run_pipeline.py
    python scripts/run_pipeline.py --modo sequencial --entrada "D:\\envio_42"
    python scripts/run_pipeline.py --modo sequencial --continuar --workers 12
    python scripts/run_pipeline.py --modo grid --materializar copiar --zip
    python scripts/run_pipeline.py --modo sequencial --simular
    python scripts/run_pipeline.py --modo sequencial --perfil azul

O recorte e o MESMO para armadilha amarela e azul - o detector olha a
geometria da grade, nao a cor do papel. --perfil so aperta a faixa de largura
aceita para o quadrante, como trava extra num lote dificil.

Codigo de saida:
    0  lote concluido sem nenhuma falha
    1  falha estrutural (pasta inexistente, lote vazio, nada processado)
    2  lote concluido, mas com falhas individuais - confira data/_falhas/
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
        description="YellowTrap Pipeline - nomeacao + recorte dos quadrantes",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--modo", choices=list(settings.MODOS_VALIDOS),
                        default=settings.MODO_PADRAO,
                        help="grid = a1..d10 | sequencial = VARD1 | "
                             "recorte = mantem o nome de origem")
    parser.add_argument("--entrada", type=Path, default=None,
                        help="pasta com as fotos brutas")
    parser.add_argument("--saida", type=Path, default=None,
                        help="pasta de destino dos quadrantes recortados")
    parser.add_argument("--lote-id", default=None,
                        help="identificador do lote (default: timestamp)")
    parser.add_argument("--workers", type=int, default=None,
                        help="processos paralelos no recorte (default: CPUs - 1)")
    parser.add_argument("--formato", choices=list(FORMATOS_VALIDOS), default=None,
                        help="formato dos quadrantes recortados")
    parser.add_argument("--perfil", choices=list(settings.RECORTE_PERFIS),
                        default=None,
                        help="cor da armadilha: auto atende amarela e azul "
                             "(padrao); amarela/azul travam a faixa esperada "
                             "do quadrante num lote dificil")
    parser.add_argument("--limite", type=int, default=None,
                        help="processa apenas as N primeiras fotos (teste/amostra)")

    grupo_nome = parser.add_argument_group("nomeacao sequencial")
    grupo_nome.add_argument("--prefixo", default=None,
                            help="prefixo do modo sequencial")
    grupo_nome.add_argument("--digitos", type=int, default=None,
                            help="largura minima do contador: 1 = VARD1, "
                                 "VARD2... | 7 = VARD0000001, VARD0000002...")
    grupo_nome.add_argument("--inicio", type=int, default=None,
                            help="primeiro numero da sequencia")
    grupo_nome.add_argument("--continuar", dest="continuar", action="store_true",
                            default=None,
                            help="continua a numeracao a partir do maior nome "
                                 "ja existente na saida")

    grupo_io = parser.add_argument_group("materializacao das renomeadas")
    grupo_io.add_argument("--materializar", dest="estrategia",
                          choices=list(settings.ESTRATEGIAS_VALIDAS), default=None,
                          help="virtual = nao grava 02_renomeadas (mais rapido) | "
                               "hardlink | copiar | mover")
    grupo_io.add_argument("--sem-md5", dest="verificar_md5", action="store_false",
                          default=None,
                          help="desliga a conferencia MD5 da copia (lotes grandes)")
    grupo_io.add_argument("--zip", dest="zip", action="store_true", default=None,
                          help="cria o ZIP (ZIP_STORED) das fotos renomeadas")
    grupo_io.add_argument("--sem-zip", dest="zip", action="store_false",
                          help="nao cria o ZIP das fotos renomeadas")

    grupo_exec = parser.add_argument_group("execucao")
    grupo_exec.add_argument("--retomar", dest="pular_existentes",
                            action="store_true", default=None,
                            help="pula fotos cujo quadrante ja existe na saida")
    grupo_exec.add_argument("--limpar-saida", dest="limpar_saida",
                            action="store_true", default=None,
                            help="esvazia a pasta de recortes antes de comecar")
    grupo_exec.add_argument("--simular", action="store_true",
                            help="mostra o plano de nomes e nao grava nada")
    grupo_exec.add_argument("--verbose", action="store_true",
                            help="log DEBUG no console")
    return parser


def main() -> int:
    args = construir_parser().parse_args()
    configurar_logging(nivel_console="DEBUG" if args.verbose else None)

    opcoes = OpcoesProcessamento(
        modo=args.modo,
        pasta_entrada=args.entrada,
        pasta_recortadas=args.saida,
        lote_id=args.lote_id,
        workers=args.workers,
        formato=args.formato,
        perfil=args.perfil,
        limite=args.limite,
        prefixo=args.prefixo,
        digitos=args.digitos,
        indice_inicial=args.inicio,
        continuar_numeracao=args.continuar,
        estrategia_renomeacao=args.estrategia,
        verificar_md5=args.verificar_md5,
        criar_zip=args.zip,
        pular_existentes=args.pular_existentes,
        limpar_saida=args.limpar_saida,
        simular=args.simular,
    )

    sumario = executar_processamento(opcoes)
    if not sumario.sucesso:
        return 1
    return 0 if sumario.total_falhas == 0 else 2


if __name__ == "__main__":
    # Guarda obrigatoria no Windows: o ProcessPoolExecutor usa 'spawn' e
    # reimporta este modulo em cada worker.
    raise SystemExit(main())
