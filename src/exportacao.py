"""
Gravacao dos quadrantes recortados.

Unico ponto do pipeline em que pixels vao para o disco. A funcao foi migrada
VERBATIM do Colab; os parametros de compressao (PNG=1, TIFF=5/LZW) sao
lossless e estao espelhados em config/settings.py apenas para consulta.
"""

from __future__ import annotations

import cv2

from src.utils import imwrite_fallback, obter_logger

logger = obter_logger(__name__)

__all__ = [
    "salvar_recortada",
    "extensao_do_formato",
    "FORMATOS_VALIDOS",
]

# Extensao de arquivo por formato de saida do recorte.
_EXTENSOES = {"png": ".png", "tiff": ".tiff", "jpg_max": ".jpg"}

FORMATOS_VALIDOS = tuple(_EXTENSOES)


def extensao_do_formato(formato: str) -> str:
    """Devolve a extensao de arquivo correspondente ao formato de recorte."""
    try:
        return _EXTENSOES[formato]
    except KeyError:
        raise ValueError(f"Formato invalido: {formato}") from None


# ---------------------------------------------------------------------------
# Funcao validada no Colab - NAO ALTERAR A LOGICA
# ---------------------------------------------------------------------------


def salvar_recortada(imagem, caminho_saida, formato='png'):
    """Salva no formato especificado com qualidade maxima."""
    if formato == 'png':
        params = [cv2.IMWRITE_PNG_COMPRESSION, 1]
    elif formato == 'tiff':
        params = [cv2.IMWRITE_TIFF_COMPRESSION, 5]  # LZW
    elif formato == 'jpg_max':
        params = [cv2.IMWRITE_JPEG_QUALITY, 100,
                  cv2.IMWRITE_JPEG_OPTIMIZE, 1]
    else:
        raise ValueError(f"Formato invalido: {formato}")

    gravou = cv2.imwrite(str(caminho_saida), imagem, params)
    if not gravou:
        # Resgate para caminhos com acentos no Windows (bytes identicos).
        gravou = imwrite_fallback(caminho_saida, imagem, params)
    if not gravou:
        raise IOError(f"cv2.imwrite falhou ao gravar {caminho_saida}")
    return caminho_saida
