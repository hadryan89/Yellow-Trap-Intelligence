"""
Protocolo 3 - Stitching (montagem da placa).

Depois do recorte temos 40 quadrantes limpos, nomeados a1..d10. O stitching
monta a placa completa em LAYOUT HORIZONTAL:

    [a1 a2 a3 ... a10]   <- faixa de cima
    [b1 b2 b3 ... b10]
    [c1 c2 c3 ... c10]
    [d1 d2 d3 ... d10]   <- faixa de baixo

Ou seja: cada LETRA vira uma faixa horizontal de 10 celulas e as 4 faixas
sao empilhadas verticalmente -> placa de 10 celulas de largura x 4 de altura.

Celulas ausentes viram um placeholder amarelo (40, 230, 250) em BGR, o que
torna visualmente obvio qual quadrante faltou.

Logica migrada VERBATIM do Colab.
"""

from __future__ import annotations

import gc
import re
from pathlib import Path

import cv2
import numpy as np

from config import settings
from src.utils import imread_fallback, obter_logger

logger = obter_logger(__name__)

__all__ = [
    "carregar_quadrantes",
    "normalizar_tamanhos",
    "montar_placa",
    "executar_stitching",
]


# ---------------------------------------------------------------------------
# Funcoes validadas no Colab - NAO ALTERAR A LOGICA
# ---------------------------------------------------------------------------


def carregar_quadrantes(pasta_imagens, padrao_nome=r"([A-Za-z])(\d+)", escala=1.0):
    """
    Carrega imagens nomeadas com padrao letra+numero (ex: a1.png, b3.tiff).
    A LETRA representa a coluna visual (a, b, c, d).
    O NUMERO representa a posicao dentro da coluna (1 a 10).
    """
    quadrantes = {}
    pasta = Path(pasta_imagens)
    extensoes_validas = {".jpg", ".jpeg", ".png", ".bmp", ".tiff"}

    for arquivo in sorted(pasta.iterdir()):
        if arquivo.suffix.lower() not in extensoes_validas:
            continue
        match = re.search(padrao_nome, arquivo.stem)
        if match is None:
            continue

        letra = match.group(1).lower()
        numero = int(match.group(2))
        imagem = cv2.imread(str(arquivo))
        if imagem is None:
            # Resgate para caminhos com acentos no Windows.
            imagem = imread_fallback(arquivo)
        if imagem is None:
            logger.error("Quadrante ilegivel, sera ignorado: %s", arquivo.name)
            continue

        if escala < 1.0:
            h, w = imagem.shape[:2]
            imagem = cv2.resize(imagem, (int(w * escala), int(h * escala)),
                                interpolation=cv2.INTER_AREA)

        quadrantes[(letra, numero)] = imagem

    gc.collect()
    return quadrantes


def normalizar_tamanhos(quadrantes):
    """Redimensiona todos para a mediana de tamanhos (necessario para hstack/vstack)."""
    if not quadrantes:
        return quadrantes

    alturas = [img.shape[0] for img in quadrantes.values()]
    larguras = [img.shape[1] for img in quadrantes.values()]
    altura_padrao = int(np.median(alturas))
    largura_padrao = int(np.median(larguras))

    normalizados = {}
    for chave, img in quadrantes.items():
        if img.shape[:2] != (altura_padrao, largura_padrao):
            img = cv2.resize(img, (largura_padrao, altura_padrao),
                             interpolation=cv2.INTER_AREA)
        normalizados[chave] = img
    return normalizados


def montar_placa(quadrantes, ordem_letras=None, ordem_numeros=None,
                 cor_placeholder=(40, 230, 250)):
    """
    Layout HORIZONTAL:
    - Cada LETRA (a, b, c, d) vira uma faixa horizontal (10 celulas lado a lado)
    - As 4 faixas sao empilhadas verticalmente

    Resultado:
      [a1 a2 a3 ... a10]   <- linha de cima
      [b1 b2 b3 ... b10]
      [c1 c2 c3 ... c10]
      [d1 d2 d3 ... d10]   <- linha de baixo
    """
    if not quadrantes:
        raise ValueError("Nenhum quadrante carregado.")

    if ordem_letras is None:
        ordem_letras = sorted({k[0] for k in quadrantes})
    if ordem_numeros is None:
        ordem_numeros = sorted({k[1] for k in quadrantes})

    shape_padrao = next(iter(quadrantes.values())).shape

    linhas_montadas = []
    for letra in ordem_letras:
        cells_da_linha = []
        for numero in ordem_numeros:
            chave = (letra, numero)
            if chave in quadrantes:
                cells_da_linha.append(quadrantes[chave])
            else:
                placeholder = np.full(shape_padrao, cor_placeholder, dtype=np.uint8)
                cells_da_linha.append(placeholder)
        linha = np.hstack(cells_da_linha)
        linhas_montadas.append(linha)

    placa = np.vstack(linhas_montadas)
    gc.collect()
    return placa


# ---------------------------------------------------------------------------
# Orquestracao da etapa (camada nova - nao altera a logica acima)
# ---------------------------------------------------------------------------


def celulas_faltantes(quadrantes, ordem_letras=None, ordem_numeros=None) -> list[str]:
    """Lista as celulas do grid que nao tem quadrante carregado (viram placeholder)."""
    ordem_letras = ordem_letras or settings.LETRAS_COLUNAS
    ordem_numeros = ordem_numeros or settings.NUMEROS_LINHAS
    return [
        f"{letra}{numero}"
        for letra in ordem_letras
        for numero in ordem_numeros
        if (letra, numero) not in quadrantes
    ]


def executar_stitching(
    pasta_imagens: Path | str | None = None,
    ordem_letras: list[str] | None = None,
    ordem_numeros: list[int] | None = None,
    escala: float | None = None,
) -> tuple[np.ndarray, dict]:
    """
    Executa o protocolo 3 completo: carrega -> normaliza -> monta.

    Retorna (placa, estatisticas).
    """
    pasta_imagens = Path(pasta_imagens or settings.PASTA_RECORTADAS)
    ordem_letras = ordem_letras or settings.LETRAS_COLUNAS
    ordem_numeros = ordem_numeros or settings.NUMEROS_LINHAS
    escala = settings.STITCHING_ESCALA_CARREGAMENTO if escala is None else escala

    if not pasta_imagens.exists():
        raise FileNotFoundError(f"Pasta de quadrantes nao existe: {pasta_imagens}")

    quadrantes = carregar_quadrantes(
        pasta_imagens,
        padrao_nome=settings.STITCHING_PADRAO_NOME,
        escala=escala,
    )
    logger.info("Stitching: %d quadrante(s) carregado(s) de %s",
                len(quadrantes), pasta_imagens)

    if not quadrantes:
        raise ValueError(f"Nenhum quadrante valido encontrado em {pasta_imagens}")

    # Quadrantes fora do grid configurado (ex.: e1, a11) sao ignorados na
    # montagem, mas influenciariam a mediana de tamanhos - por isso o aviso.
    fora_do_grid = [
        f"{letra}{numero}" for (letra, numero) in quadrantes
        if letra not in ordem_letras or numero not in ordem_numeros
    ]
    if fora_do_grid:
        logger.warning("Quadrante(s) fora do grid configurado (ignorado(s) na "
                       "montagem): %s", ", ".join(sorted(fora_do_grid)))

    tamanhos_originais = {q.shape[:2] for q in quadrantes.values()}
    quadrantes = normalizar_tamanhos(quadrantes)
    tamanho_celula = next(iter(quadrantes.values())).shape[:2]
    if len(tamanhos_originais) > 1:
        logger.info(
            "Quadrantes tinham %d tamanho(s) distinto(s); normalizados para "
            "%d x %d px (L x A) pela mediana.",
            len(tamanhos_originais), tamanho_celula[1], tamanho_celula[0],
        )

    faltantes = celulas_faltantes(quadrantes, ordem_letras, ordem_numeros)
    if faltantes:
        logger.warning(
            "%d celula(s) sem quadrante - serao preenchidas com o placeholder "
            "amarelo: %s", len(faltantes), ", ".join(faltantes),
        )

    placa = montar_placa(
        quadrantes,
        ordem_letras=ordem_letras,
        ordem_numeros=ordem_numeros,
        cor_placeholder=settings.STITCHING_COR_PLACEHOLDER,
    )
    logger.info("Placa montada: %d x %d px (L x A), %d linha(s) x %d coluna(s).",
                placa.shape[1], placa.shape[0], len(ordem_letras), len(ordem_numeros))

    estatisticas = {
        "quadrantes_carregados": len(quadrantes),
        "celulas_faltantes": faltantes,
        "placeholders": len(faltantes),
        "fora_do_grid": sorted(fora_do_grid),
        "tamanho_celula": tamanho_celula,
        "dimensao_placa": placa.shape[:2],
    }
    return placa, estatisticas
