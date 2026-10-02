"""
Protocolo 3 - Montagem da placa (stitching).

Depois do recorte, os quadrantes sao juntados de 40 em 40 - na ordem do
lote - de volta no LAYOUT HORIZONTAL da placa:

    [A1 A2 A3 ... A10]   <- faixa de cima
    [B1 B2 B3 ... B10]
    [C1 C2 C3 ... C10]
    [D1 D2 D3 ... D10]   <- faixa de baixo

Cada faixa tem 10 celulas lado a lado e as 4 faixas sao empilhadas -> placa
de 10 celulas de largura x 4 de altura. No modo grid a armadilha inteira
(VARD14A1..VARD14H10) rende duas placas: A..D e E..H, um lado do papel cada.

Celulas ausentes viram um placeholder amarelo (40, 230, 250) em BGR, o que
torna visualmente obvio qual quadrante faltou. Quadrantes de tamanhos
diferentes sao normalizados para a mediana antes de encostar uns nos outros.

A logica de carregar / normalizar / montar vem do Colab. A unica diferenca e
que a placa e preenchida num array pre-alocado em vez de hstack + vstack: o
resultado e o mesmo pixel a pixel (ha teste travando isso), sem as copias
intermediarias das faixas, e cada quadrante e liberado assim que entra na
placa - uma placa de 40 quadrantes de ~4700 px passa de 2,5 GB.
"""

from __future__ import annotations

import gc
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from config import settings
from src.utils import imread_fallback, natural_key, obter_logger

logger = obter_logger(__name__)

__all__ = [
    "PlanoPlaca",
    "planejar_placas",
    "listar_quadrantes",
    "carregar_quadrante",
    "normalizar_tamanhos",
    "montar_placa",
    "montar_placa_do_plano",
]


@dataclass(slots=True)
class PlanoPlaca:
    """
    Uma placa a montar: o nome dela e o caminho esperado de cada celula.

    `celulas[i]` ocupa a faixa i // colunas, posicao i % colunas. Um caminho
    None (ou que nao existe em disco) vira placeholder.
    """

    nome: str
    celulas: list[Path | None] = field(default_factory=list)

    @property
    def quadrantes_esperados(self) -> int:
        return sum(1 for c in self.celulas if c is not None)


def planejar_placas(caminhos, por_placa: int | None = None) -> list[PlanoPlaca]:
    """
    Fatia a lista ORDENADA de quadrantes em placas de `por_placa` celulas.

    A ordem e a do lote: os 40 primeiros vao para a placa 1, e assim por
    diante. A ultima placa pode vir incompleta - as celulas que sobram ficam
    como placeholder, para a placa manter sempre o mesmo formato.

    O nome de cada placa e "<primeiro>-<ultimo>" (VARD14A1-VARD14D10), o que
    a identifica no acervo sem precisar de numeracao propria.
    """
    por_placa = por_placa or settings.MONTAGEM_QUADRANTES_POR_PLACA
    caminhos = [Path(c) for c in caminhos]
    placas = []
    for inicio in range(0, len(caminhos), por_placa):
        fatia = caminhos[inicio:inicio + por_placa]
        celulas: list[Path | None] = list(fatia) + [None] * (por_placa - len(fatia))
        nome = f"{fatia[0].stem}-{fatia[-1].stem}"
        placas.append(PlanoPlaca(nome=nome, celulas=celulas))
    return placas


def listar_quadrantes(pasta: Path | str) -> list[Path]:
    """Quadrantes de uma pasta em ordem natural (VARD2 antes de VARD10)."""
    pasta = Path(pasta)
    return sorted(
        (p for p in pasta.iterdir()
         if p.is_file() and p.suffix.lower() in settings.EXTENSOES_IMAGEM),
        key=lambda p: natural_key(p.name),
    )


# ---------------------------------------------------------------------------
# Carregamento / normalizacao / montagem (logica do Colab)
# ---------------------------------------------------------------------------


def carregar_quadrante(caminho: Path, escala: float = 1.0):
    """Le um quadrante (None se nao existe ou e ilegivel), opcionalmente reduzido."""
    caminho = Path(caminho)
    if not caminho.is_file():
        return None
    imagem = cv2.imread(str(caminho))
    if imagem is None:
        # Resgate para caminhos com acentos no Windows.
        imagem = imread_fallback(caminho)
    if imagem is None:
        logger.error("Quadrante ilegivel, vira placeholder: %s", caminho.name)
        return None
    if escala < 1.0:
        h, w = imagem.shape[:2]
        imagem = cv2.resize(imagem, (int(w * escala), int(h * escala)),
                            interpolation=cv2.INTER_AREA)
    return imagem


def normalizar_tamanhos(quadrantes: dict) -> dict:
    """Redimensiona todos para a mediana de tamanhos (necessario para encostar)."""
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


def montar_placa(quadrantes: dict, total_celulas: int, colunas: int | None = None,
                 cor_placeholder=None) -> np.ndarray:
    """
    Layout HORIZONTAL a partir de {posicao: imagem} ja normalizado.

    A posicao i vai para a faixa i // colunas, celula i % colunas. Posicoes
    sem imagem ficam com a cor do placeholder. Os quadrantes sao consumidos
    (removidos do dicionario) a medida que entram na placa, para a memoria
    nao carregar a placa e os 40 quadrantes ao mesmo tempo.
    """
    if not quadrantes:
        raise ValueError("Nenhum quadrante carregado.")
    colunas = colunas or settings.MONTAGEM_COLUNAS
    if cor_placeholder is None:
        cor_placeholder = settings.MONTAGEM_COR_PLACEHOLDER

    altura, largura, canais = next(iter(quadrantes.values())).shape
    faixas = -(-total_celulas // colunas)  # teto
    placa = np.empty((faixas * altura, colunas * largura, canais), dtype=np.uint8)
    placa[:] = cor_placeholder

    for posicao in sorted(quadrantes):
        linha, coluna = divmod(posicao, colunas)
        y, x = linha * altura, coluna * largura
        placa[y:y + altura, x:x + largura] = quadrantes.pop(posicao)

    gc.collect()
    return placa


# ---------------------------------------------------------------------------
# Orquestracao de uma placa
# ---------------------------------------------------------------------------


def montar_placa_do_plano(plano: PlanoPlaca, escala: float | None = None,
                          colunas: int | None = None) -> tuple[np.ndarray, dict]:
    """
    Carrega -> normaliza -> monta UMA placa.

    Retorna (placa, estatisticas). Levanta ValueError se nenhuma celula da
    placa tem quadrante legivel.
    """
    escala = settings.MONTAGEM_ESCALA_CARREGAMENTO if escala is None else escala
    colunas = colunas or settings.MONTAGEM_COLUNAS

    quadrantes = {}
    faltantes = []
    for posicao, caminho in enumerate(plano.celulas):
        if caminho is None:
            continue
        imagem = carregar_quadrante(caminho, escala)
        if imagem is None:
            faltantes.append(Path(caminho).stem)
            continue
        quadrantes[posicao] = imagem

    if not quadrantes:
        raise ValueError(f"Placa {plano.nome}: nenhum quadrante legivel.")

    tamanhos_originais = {q.shape[:2] for q in quadrantes.values()}
    quadrantes = normalizar_tamanhos(quadrantes)
    tamanho_celula = next(iter(quadrantes.values())).shape[:2]
    if len(tamanhos_originais) > 1:
        logger.info(
            "Placa %s: quadrantes tinham %d tamanho(s) distinto(s); "
            "normalizados para %d x %d px (L x A) pela mediana.",
            plano.nome, len(tamanhos_originais), tamanho_celula[1], tamanho_celula[0],
        )
    if faltantes:
        logger.warning(
            "Placa %s: %d quadrante(s) ausente(s) - preenchido(s) com o "
            "placeholder amarelo: %s", plano.nome, len(faltantes),
            ", ".join(faltantes),
        )

    carregados = len(quadrantes)
    placa = montar_placa(quadrantes, total_celulas=len(plano.celulas),
                         colunas=colunas)
    logger.info("Placa %s montada: %d x %d px (L x A), %d quadrante(s).",
                plano.nome, placa.shape[1], placa.shape[0], carregados)

    estatisticas = {
        "nome": plano.nome,
        "quadrantes": carregados,
        "faltantes": faltantes,
        "placeholders": len(plano.celulas) - carregados,
        "tamanho_celula": list(tamanho_celula),
        "dimensao_placa": list(placa.shape[:2]),
    }
    return placa, estatisticas
