"""
Gera as imagens sinteticas usadas pelos testes.

As fixtures imitam o que a camera/microscopio produz numa placa YellowTrap:
fundo amarelo, linhas pretas verticais da grade e alguns pontos escuros
(pragas). As linhas dos quadrantes VIZINHOS aparecem cortadas (altura
parcial), enquanto as duas linhas do quadrante CENTRAL vao de ponta a ponta -
e exatamente esse contraste de "forca" do pico que o algoritmo usa para
escolher o par central.

Rodar manualmente:
    python tests/fixtures/gerar_fixtures.py
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

PASTA_FIXTURES = Path(__file__).resolve().parent

# Fundo amarelo da placa, em BGR (mesmo tom do placeholder do stitching).
COR_PLACA = (40, 230, 250)
COR_LINHA = (18, 18, 18)
COR_PRAGA = (30, 40, 35)

# --- foto_grade_valida.png ---------------------------------------------------
# 1200 x 800, tres quadrantes na horizontal. Linhas em x = 120, 400, 800, 1080.
# O quadrante CENTRAL fica entre x=400 e x=800.
LARGURA_VALIDA, ALTURA_VALIDA = 1200, 800
X_LINHA_ESQ_VIZINHA = 120
X_LINHA_CENTRAL_ESQ = 400
X_LINHA_CENTRAL_DIR = 800
X_LINHA_DIR_VIZINHA = 1080
ESPESSURA_LINHA = 10

# Limites esperados do recorte na resolucao cheia (usados pelos testes).
CROP_ESPERADO_X = (X_LINHA_CENTRAL_ESQ, X_LINHA_CENTRAL_DIR)


def _placa_vazia(largura: int, altura: int) -> np.ndarray:
    return np.full((altura, largura, 3), COR_PLACA, dtype=np.uint8)


def _linha_vertical(img: np.ndarray, x: int, y_inicio: int, y_fim: int,
                    espessura: int = ESPESSURA_LINHA) -> None:
    meia = espessura // 2
    img[y_inicio:y_fim, x - meia:x + espessura - meia] = COR_LINHA


def _pragas(img: np.ndarray, posicoes: list[tuple[int, int]], raio: int = 6) -> None:
    for x, y in posicoes:
        cv2.circle(img, (x, y), raio, COR_PRAGA, -1)


def gerar_foto_grade_valida() -> np.ndarray:
    """Foto tipica: 3 quadrantes, linhas centrais inteiras, vizinhas cortadas."""
    img = _placa_vazia(LARGURA_VALIDA, ALTURA_VALIDA)

    # Linhas dos quadrantes vizinhos: aparecem cortadas pelo enquadramento.
    _linha_vertical(img, X_LINHA_ESQ_VIZINHA, 0, int(ALTURA_VALIDA * 0.60))
    _linha_vertical(img, X_LINHA_DIR_VIZINHA, int(ALTURA_VALIDA * 0.40), ALTURA_VALIDA)

    # Linhas do quadrante central: de ponta a ponta (picos mais fortes).
    _linha_vertical(img, X_LINHA_CENTRAL_ESQ, 0, ALTURA_VALIDA)
    _linha_vertical(img, X_LINHA_CENTRAL_DIR, 0, ALTURA_VALIDA)

    # Pragas dentro do quadrante central + uma no vizinho (deve sumir no crop).
    _pragas(img, [(500, 200), (620, 430), (700, 650), (560, 320)])
    _pragas(img, [(250, 400)], raio=8)
    return img


def gerar_foto_sem_grade() -> np.ndarray:
    """Foto ruim: sem linhas detectaveis - a deteccao precisa FALHAR."""
    img = _placa_vazia(600, 400)
    _pragas(img, [(150, 120), (400, 260), (300, 190)], raio=5)
    return img


def gerar_quadrante_pequeno() -> np.ndarray:
    """Quadrante minusculo usado nos testes de stitching a partir de arquivo."""
    img = _placa_vazia(60, 40)
    img[0, :] = COR_LINHA
    img[-1, :] = COR_LINHA
    img[:, 0] = COR_LINHA
    img[:, -1] = COR_LINHA
    _pragas(img, [(30, 20)], raio=4)
    return img


FIXTURES = {
    "foto_grade_valida.png": gerar_foto_grade_valida,
    "foto_sem_grade.png": gerar_foto_sem_grade,
    "quadrante_pequeno.png": gerar_quadrante_pequeno,
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
