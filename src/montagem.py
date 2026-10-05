"""
Protocolo 3 - Montagem da placa (stitching).

Depois do recorte, os quadrantes sao juntados de 80 em 80 - na ordem do
lote - numa UNICA imagem com os dois lados do papel, cada um no LAYOUT
HORIZONTAL da placa:

    [A1 A2 A3 ... A10]   <- frente, faixa de cima
    [B1 B2 B3 ... B10]
    [C1 C2 C3 ... C10]
    [D1 D2 D3 ... D10]   <- frente, faixa de baixo
    ==================   <- separador entre os lados
    [E1 E2 E3 ... E10]   <- verso, faixa de cima
    ...
    [H1 H2 H3 ... H10]   <- verso, faixa de baixo

Cada faixa tem 10 celulas lado a lado; 4 faixas formam um lado. No modo grid
a armadilha inteira (VARD14A1..VARD14H10) rende UMA imagem com os 80
quadrantes.

Celulas ausentes viram um placeholder amarelo (40, 230, 250) em BGR, o que
torna visualmente obvio qual quadrante faltou. Quadrantes de tamanhos
diferentes sao normalizados para a mediana antes de encostar uns nos outros.

A logica de carregar / normalizar / montar vem do Colab. A unica diferenca e
que a placa e preenchida num array pre-alocado em vez de hstack + vstack: o
resultado e o mesmo pixel a pixel (ha teste travando isso), sem as copias
intermediarias das faixas. Como a entrega final e 1920 x 1080, cada
quadrante ja e reduzido ao ser carregado - em resolucao cheia, 80 quadrantes
de ~5000 px passariam de 7 GB.
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
    "altura_separador",
    "altura_maxima_celula",
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

    A ordem e a do lote: os 80 primeiros vao para a placa 1 (40 de frente,
    40 de verso), e assim por diante. A ultima placa pode vir incompleta - as celulas que sobram ficam
    como placeholder, para a placa manter sempre o mesmo formato.

    O nome de cada placa e "<primeiro>-<ultimo>" (VARD14A1-VARD14H10), o que
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


def carregar_quadrante(caminho: Path, altura_max: int | None = None):
    """Le um quadrante (None se nao existe ou e ilegivel), reduzido a `altura_max`."""
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
    h, w = imagem.shape[:2]
    if altura_max and h > altura_max:
        largura = max(1, round(w * altura_max / h))
        imagem = cv2.resize(imagem, (largura, altura_max),
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


def altura_separador(altura_celula: int) -> int:
    """Altura, em px, da faixa que separa um lado do papel do outro."""
    return max(1, round(altura_celula * settings.MONTAGEM_SEPARADOR_LADOS))


def montar_placa(quadrantes: dict, total_celulas: int, colunas: int | None = None,
                 cor_placeholder=None, faixas_por_lado: int | None = None) -> np.ndarray:
    """
    Layout HORIZONTAL a partir de {posicao: imagem} ja normalizado.

    A posicao i vai para a faixa i // colunas, celula i % colunas. A cada
    `faixas_por_lado` faixas comeca outro lado do papel, e entre os lados
    entra um separador. Posicoes sem imagem ficam com a cor do placeholder.
    Os quadrantes sao consumidos (removidos do dicionario) a medida que
    entram na placa, para a memoria nao carregar a placa e os quadrantes ao
    mesmo tempo.
    """
    if not quadrantes:
        raise ValueError("Nenhum quadrante carregado.")
    colunas = colunas or settings.MONTAGEM_COLUNAS
    faixas_por_lado = faixas_por_lado or settings.MONTAGEM_FAIXAS_POR_LADO
    if cor_placeholder is None:
        cor_placeholder = settings.MONTAGEM_COR_PLACEHOLDER

    altura, largura, canais = next(iter(quadrantes.values())).shape
    faixas = -(-total_celulas // colunas)  # teto
    lados = -(-faixas // faixas_por_lado)
    separador = altura_separador(altura)
    altura_lado = faixas_por_lado * altura + separador
    placa = np.empty((faixas * altura + (lados - 1) * separador,
                      colunas * largura, canais), dtype=np.uint8)
    placa[:] = cor_placeholder
    for lado in range(1, lados):
        y = lado * altura_lado - separador
        placa[y:y + separador] = settings.MONTAGEM_COR_SEPARADOR

    for posicao in sorted(quadrantes):
        linha, coluna = divmod(posicao, colunas)
        lado, faixa = divmod(linha, faixas_por_lado)
        y, x = lado * altura_lado + faixa * altura, coluna * largura
        placa[y:y + altura, x:x + largura] = quadrantes.pop(posicao)

    gc.collect()
    return placa


# ---------------------------------------------------------------------------
# Orquestracao de uma placa
# ---------------------------------------------------------------------------


def altura_maxima_celula(total_celulas: int, colunas: int | None = None) -> int:
    """
    Altura ate a qual cada quadrante e reduzido ao carregar.

    E a altura que a celula tera no arquivo final (EXPORTACAO_ALTURA dividida
    pelas faixas), vezes MONTAGEM_SUPERAMOSTRAGEM de folga para a reducao
    final continuar nitida.
    """
    colunas = colunas or settings.MONTAGEM_COLUNAS
    faixas = -(-total_celulas // colunas)
    return settings.MONTAGEM_SUPERAMOSTRAGEM * -(-settings.EXPORTACAO_ALTURA // faixas)


def montar_placa_do_plano(plano: PlanoPlaca, colunas: int | None = None,
                          altura_max: int | None = None) -> tuple[np.ndarray, dict]:
    """
    Carrega -> normaliza -> monta UMA placa.

    `altura_max` limita a altura de cada quadrante carregado (default:
    altura_maxima_celula()). Retorna (placa, estatisticas). Levanta
    ValueError se nenhuma celula da placa tem quadrante legivel.
    """
    colunas = colunas or settings.MONTAGEM_COLUNAS
    if altura_max is None:
        altura_max = altura_maxima_celula(len(plano.celulas), colunas)

    quadrantes = {}
    faltantes = []
    for posicao, caminho in enumerate(plano.celulas):
        if caminho is None:
            continue
        imagem = carregar_quadrante(caminho, altura_max)
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
