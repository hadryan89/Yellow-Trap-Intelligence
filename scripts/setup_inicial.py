"""
Cria a estrutura de pastas do projeto e confere as dependencias.

Uso:
    python scripts/setup_inicial.py
"""

from __future__ import annotations

import argparse
import importlib
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from config import settings  # noqa: E402

# (modulo importavel, nome no pip, obrigatorio?)
DEPENDENCIAS = [
    ("cv2", "opencv-python", True),
    ("numpy", "numpy", True),
    ("tqdm", "tqdm", True),
    ("watchdog", "watchdog", False),
    ("colorlog", "colorlog", False),
    ("pytest", "pytest", False),
]

VERDE, AMARELO, VERMELHO, RESET = "\033[92m", "\033[93m", "\033[91m", "\033[0m"


def criar_estrutura(com_gitkeep: bool = True) -> list[Path]:
    """Cria todas as pastas de settings.PASTAS_OBRIGATORIAS."""
    criadas = []
    for pasta in settings.PASTAS_OBRIGATORIAS:
        if not pasta.exists():
            pasta.mkdir(parents=True, exist_ok=True)
            criadas.append(pasta)
        if com_gitkeep:
            gitkeep = pasta / ".gitkeep"
            if not gitkeep.exists():
                gitkeep.touch()
    return criadas


def conferir_dependencias() -> tuple[list[str], list[str]]:
    """Retorna (faltando_obrigatorias, faltando_opcionais)."""
    faltando_obrigatorias, faltando_opcionais = [], []
    for modulo, pacote, obrigatorio in DEPENDENCIAS:
        try:
            importlib.import_module(modulo)
            versao = getattr(importlib.import_module(modulo), "__version__", "?")
            print(f"  {VERDE}OK{RESET}    {pacote:<18} {versao}")
        except ImportError:
            marcador = f"{VERMELHO}FALTA{RESET}" if obrigatorio else f"{AMARELO}opc.{RESET} "
            print(f"  {marcador} {pacote:<18} nao instalado")
            (faltando_obrigatorias if obrigatorio else faltando_opcionais).append(pacote)
    return faltando_obrigatorias, faltando_opcionais


def main() -> int:
    parser = argparse.ArgumentParser(description="Setup inicial do YellowTrap Pipeline")
    parser.add_argument("--sem-gitkeep", action="store_true",
                        help="nao cria arquivos .gitkeep nas pastas de dados")
    args = parser.parse_args()

    print("=" * 68)
    print("YellowTrap Pipeline - setup inicial")
    print("=" * 68)
    print(f"\nRaiz do projeto: {settings.BASE_DIR}")
    print(f"Pasta de dados : {settings.PASTA_DADOS}")
    print(f"Python         : {sys.version.split()[0]} ({sys.executable})")

    print("\n[1/3] Criando estrutura de pastas...")
    criadas = criar_estrutura(com_gitkeep=not args.sem_gitkeep)
    for pasta in settings.PASTAS_OBRIGATORIAS:
        marcador = "criada" if pasta in criadas else "ja existia"
        print(f"  - {pasta}  ({marcador})")

    print("\n[2/3] Conferindo dependencias...")
    faltando, opcionais = conferir_dependencias()

    print("\n[3/3] Conferindo a calibracao do recorte...")
    esperados = {
        "RECORTE_FATOR_DETECCAO": 0.125,
        "RECORTE_MARGEM_FRAC": 0.004,
        "RECORTE_BORDA": "linha",
        "RECORTE_RECUPERACAO_FRAC": 0.25,
        "RECORTE_LINHA_INFLADA": 2.0,
        "RECORTE_PERFIL_PADRAO": "auto",
        "RECORTE_ESPESSURA_MAX_FRAC": 0.04,
        "RECORTE_INCLINACAO_MAX_GRAUS": 3.0,
        "RECORTE_PONTE_FRAC": 0.04,
        "RECORTE_DISTANCIA_MIN_FRAC": 0.02,
        "RECORTE_FORCA_RELATIVA": 0.55,
        "RECORTE_FORCA_MINIMA": 0.35,
        "RECORTE_CORTAR_MOLDURA": True,
        "QUANTIDADE_ESPERADA": 40,
    }
    divergentes = []
    for nome, esperado in esperados.items():
        atual = getattr(settings, nome)
        if atual != esperado:
            divergentes.append(f"{nome}: {atual!r} (esperado {esperado!r})")
    if divergentes:
        print(f"  {VERMELHO}ATENCAO - parametros alterados em relacao a "
              f"calibracao medida no acervo:{RESET}")
        for linha in divergentes:
            print(f"    - {linha}")
        print("    Rode `pytest tests/test_recorte_acervo.py -m acervo` antes "
              "de usar em producao.")
    else:
        print(f"  {VERDE}OK{RESET}    todos os parametros conferem com a calibracao.")

    print("\n" + "=" * 68)
    if faltando:
        print(f"{VERMELHO}Instale as dependencias obrigatorias antes de rodar:{RESET}")
        print("    pip install -r requirements.txt")
        return 1

    if opcionais:
        print(f"{AMARELO}Opcionais faltando ({', '.join(opcionais)}) - "
              f"watcher/cores/testes podem nao funcionar.{RESET}")

    print(f"{VERDE}Ambiente pronto.{RESET}\n")
    print("Proximos passos:")
    print(f"  1. Coloque as fotos em {settings.PASTA_ENTRADA}")
    print(f"  2. Lote de {settings.QUANTIDADE_ESPERADA} fotos no grid da placa:")
    print("       python scripts/run_pipeline.py --modo grid")
    print("     Muitas fotos, so renomear e recortar:")
    print("       python scripts/run_pipeline.py --modo sequencial")
    print("  3. Ou deixe o watcher ligado:  python scripts/watcher.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
