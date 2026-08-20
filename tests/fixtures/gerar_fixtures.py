"""
Gera as imagens sinteticas usadas pelos testes.

As fixtures imitam o que a camera/microscopio produz sobre uma armadilha
adesiva: fundo de cor forte (AMARELO ou AZUL), linhas escuras da grade e
alguns pontos escuros (pragas).

O que cada detalhe cobre no detector:

  * A celula da grade e MENOR que a foto nos dois eixos. E o caso real
    (9600x5400 de foto para celula de ~4400) e e o que obriga o recorte a
    acontecer nos quatro lados: sem isso a linha horizontal e as tiras dos
    quadrantes de cima e de baixo entram na entrega.
  * As linhas dos quadrantes VIZINHOS aparecem cortadas (altura parcial) e
    a moldura preta encosta na borda de cima. Nenhuma das duas pode ser
    confundida com borda do quadrante central.
  * A versao AZUL tem exatamente a mesma geometria da AMARELA. O detector
    olha geometria, nao cor: as duas tem que sair com o MESMO crop box.
  * A versao INCLINADA gira a grade em 1.5 grau - o suficiente para
    destruir a projecao por coluna se a correcao de inclinacao sumir.

Rodar manualmente:
    python tests/fixtures/gerar_fixtures.py
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

PASTA_FIXTURES = Path(__file__).resolve().parent

# Cores de fundo, em BGR.
COR_AMARELA = (40, 230, 250)
COR_AZUL = (215, 150, 40)
COR_LINHA = (18, 18, 18)
COR_PRAGA = (30, 40, 35)
COR_MOLDURA = (12, 12, 12)

# --- Geometria comum das fixtures com grade ---------------------------------
# 1200 x 800. Linhas verticais em x = 150, 650, 1150 e horizontais em
# y = 40, 540. O centro da foto (600, 400) cai na celula (150..650, 40..540),
# que e o quadrante que o recorte tem que entregar.
LARGURA_VALIDA, ALTURA_VALIDA = 1200, 800
X_LINHAS = (150, 650, 1150)
Y_LINHAS = (40, 540)
X_LINHA_CENTRAL_ESQ, X_LINHA_CENTRAL_DIR = 150, 650
Y_LINHA_CENTRAL_TOPO, Y_LINHA_CENTRAL_BASE = 40, 540
ESPESSURA_LINHA = 10
ALTURA_MOLDURA = 18

# Limites esperados do recorte na resolucao cheia (usados pelos testes).
CROP_ESPERADO_X = (X_LINHA_CENTRAL_ESQ, X_LINHA_CENTRAL_DIR)
CROP_ESPERADO_Y = (Y_LINHA_CENTRAL_TOPO, Y_LINHA_CENTRAL_BASE)

INCLINACAO_GRAUS = 1.5


def _placa_vazia(cor, largura: int, altura: int) -> np.ndarray:
    return np.full((altura, largura, 3), cor, dtype=np.uint8)


def _linha_vertical(img, x, y_inicio, y_fim, espessura=ESPESSURA_LINHA) -> None:
    meia = espessura // 2
    img[y_inicio:y_fim, x - meia:x + espessura - meia] = COR_LINHA


def _linha_horizontal(img, y, x_inicio, x_fim, espessura=ESPESSURA_LINHA) -> None:
    meia = espessura // 2
    img[y - meia:y + espessura - meia, x_inicio:x_fim] = COR_LINHA


def _pragas(img, posicoes, raio: int = 6) -> None:
    for x, y in posicoes:
        cv2.circle(img, (x, y), raio, COR_PRAGA, -1)


def gerar_grade(cor, inclinacao_graus: float = 0.0) -> np.ndarray:
    """Foto tipica: grade completa, moldura no topo, pragas espalhadas."""
    img = _placa_vazia(cor, LARGURA_VALIDA, ALTURA_VALIDA)

    for x in X_LINHAS:
        _linha_vertical(img, x, 0, ALTURA_VALIDA)
    for y in Y_LINHAS:
        _linha_horizontal(img, y, 0, LARGURA_VALIDA)

    # Linha de um quadrante vizinho, cortada pelo enquadramento: nao pode
    # virar borda do quadrante central.
    _linha_vertical(img, 900, 0, int(ALTURA_VALIDA * 0.35))

    # Pragas dentro do quadrante central + uma em cada vizinho (que devem
    # sumir no recorte).
    _pragas(img, [(300, 150), (420, 330), (520, 470), (250, 430)])
    _pragas(img, [(800, 300)], raio=8)
    _pragas(img, [(400, 700)], raio=8)

    if inclinacao_graus:
        centro = (LARGURA_VALIDA / 2.0, ALTURA_VALIDA / 2.0)
        matriz = cv2.getRotationMatrix2D(centro, inclinacao_graus, 1.0)
        img = cv2.warpAffine(img, matriz, (LARGURA_VALIDA, ALTURA_VALIDA),
                             flags=cv2.INTER_NEAREST,
                             borderMode=cv2.BORDER_REPLICATE)

    # A moldura entra DEPOIS da rotacao: ela e da foto, nao da armadilha.
    img[:ALTURA_MOLDURA, :] = COR_MOLDURA
    return img


def gerar_foto_grade_valida() -> np.ndarray:
    return gerar_grade(COR_AMARELA)


def gerar_foto_grade_azul() -> np.ndarray:
    return gerar_grade(COR_AZUL)


def gerar_foto_grade_inclinada() -> np.ndarray:
    return gerar_grade(COR_AZUL, INCLINACAO_GRAUS)


def gerar_foto_sem_grade() -> np.ndarray:
    """Foto ruim: sem linhas detectaveis - a deteccao precisa FALHAR."""
    img = _placa_vazia(COR_AMARELA, 600, 400)
    _pragas(img, [(150, 120), (400, 260), (300, 190)], raio=5)
    return img


FIXTURES = {
    "foto_grade_valida.png": gerar_foto_grade_valida,
    "foto_grade_azul.png": gerar_foto_grade_azul,
    "foto_grade_inclinada.png": gerar_foto_grade_inclinada,
    "foto_sem_grade.png": gerar_foto_sem_grade,
}


def gerar_todas(pasta: Path | None = None, forcar: bool = False) -> list[Path]:
    """Gera as fixtures que ainda nao existem. Retorna os caminhos gerados."""
    pasta = Path(pasta or PASTA_FIXTURES)
    pasta.mkdir(parents=True, exist_ok=True)
    geradas = []
    for nome, funcao in FIXTURES.items():
        caminho = pasta / nome
        if caminho.exists() and not forcar:
            continue
        cv2.imwrite(str(caminho), funcao(), [cv2.IMWRITE_PNG_COMPRESSION, 1])
        geradas.append(caminho)
    return geradas


if __name__ == "__main__":
    geradas = gerar_todas(forcar=True)
    for caminho in geradas:
        print(f"gerada: {caminho}")
