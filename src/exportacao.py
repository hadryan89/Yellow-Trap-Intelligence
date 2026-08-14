"""
Salvamento e exportacao em multiplos formatos.

Reune os dois pontos do pipeline em que pixels vao para o disco:

  * salvar_recortada()              - quadrante individual (protocolo 2)
  * exportar_multiplas_resolucoes() - placa final (protocolo 3)

Ambas as funcoes foram migradas VERBATIM do Colab. Os parametros de
compressao (PNG=1, TIFF=5/LZW, WEBP=95) sao lossless ou de altissima
qualidade e estao espelhados em config/settings.py apenas para consulta.
"""

from __future__ import annotations

import os
from pathlib import Path

import cv2

from config import settings
from src.utils import imwrite_fallback, obter_logger

logger = obter_logger(__name__)

__all__ = [
    "salvar_recortada",
    "exportar_multiplas_resolucoes",
    "extensao_do_formato",
]

# Extensao de arquivo por formato de saida do recorte.
_EXTENSOES = {"png": ".png", "tiff": ".tiff", "jpg_max": ".jpg"}


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


def exportar_multiplas_resolucoes(placa, pasta_export, resolucoes,
                                  incluir_png_lossless=True,
                                  incluir_tiff=True,
                                  incluir_webp=True):
    """
    Exporta a placa em multiplas resolucoes + formatos lossless.

    resolucoes: lista de tuplas (nome, largura_px, qualidade_jpg)
    """
    os.makedirs(pasta_export, exist_ok=True)

    for arq in os.listdir(pasta_export):
        caminho_arq = os.path.join(pasta_export, arq)
        if os.path.isfile(caminho_arq):
            os.remove(caminho_arq)

    altura_placa, largura_placa = placa.shape[:2]
    proporcao = altura_placa / largura_placa
    arquivos_gerados = []

    # JPEG em multiplas resolucoes
    for nome, largura_alvo, qualidade in resolucoes:
        if largura_alvo > largura_placa:
            logger.debug(
                "Resolucao '%s' (%d px) ignorada: maior que a placa (%d px). "
                "Nao ha upscale.", nome, largura_alvo, largura_placa,
            )
            continue  # nao faz upscale

        altura_alvo = int(largura_alvo * proporcao)
        placa_redim = cv2.resize(placa, (largura_alvo, altura_alvo),
                                 interpolation=cv2.INTER_AREA)

        nome_arquivo = f'placa_{nome}.jpg'
        caminho = os.path.join(pasta_export, nome_arquivo)
        params = [
            cv2.IMWRITE_JPEG_QUALITY, qualidade,
            cv2.IMWRITE_JPEG_OPTIMIZE, 1,
        ]
        if not cv2.imwrite(caminho, placa_redim, params):
            imwrite_fallback(caminho, placa_redim, params)
        arquivos_gerados.append(nome_arquivo)
        logger.info("Exportado %s (%d x %d px)", nome_arquivo, largura_alvo, altura_alvo)

    # PNG lossless (resolucao original)
    if incluir_png_lossless:
        caminho = os.path.join(pasta_export, 'placa_LOSSLESS.png')
        params = [cv2.IMWRITE_PNG_COMPRESSION, 1]
        if not cv2.imwrite(caminho, placa, params):
            imwrite_fallback(caminho, placa, params)
        arquivos_gerados.append('placa_LOSSLESS.png')
        logger.info("Exportado placa_LOSSLESS.png (%d x %d px)",
                    largura_placa, altura_placa)

    # TIFF (padrao cientifico, abre no ImageJ/Fiji)
    if incluir_tiff:
        caminho = os.path.join(pasta_export, 'placa_CIENTIFICO.tiff')
        params = [cv2.IMWRITE_TIFF_COMPRESSION, 5]
        if not cv2.imwrite(caminho, placa, params):
            imwrite_fallback(caminho, placa, params)
        arquivos_gerados.append('placa_CIENTIFICO.tiff')
        logger.info("Exportado placa_CIENTIFICO.tiff (TIFF LZW)")

    # WEBP (compressao moderna)
    if incluir_webp:
        caminho = os.path.join(pasta_export, 'placa_WEBP.webp')
        params = [cv2.IMWRITE_WEBP_QUALITY, 95]
        if not cv2.imwrite(caminho, placa, params):
            imwrite_fallback(caminho, placa, params)
        arquivos_gerados.append('placa_WEBP.webp')
        logger.info("Exportado placa_WEBP.webp")

    return arquivos_gerados


# ---------------------------------------------------------------------------
# Camada de conveniencia (nao altera a logica acima)
# ---------------------------------------------------------------------------


def exportar_placa(placa, pasta_export: Path | str, resolucoes=None,
                   incluir_png_lossless: bool | None = None,
                   incluir_tiff: bool | None = None,
                   incluir_webp: bool | None = None) -> list[str]:
    """exportar_multiplas_resolucoes() com os defaults de settings.py."""
    return exportar_multiplas_resolucoes(
        placa,
        str(pasta_export),
        resolucoes if resolucoes is not None else settings.EXPORTACAO_RESOLUCOES,
        incluir_png_lossless=(
            settings.EXPORTACAO_INCLUIR_PNG_LOSSLESS
            if incluir_png_lossless is None else incluir_png_lossless
        ),
        incluir_tiff=(
            settings.EXPORTACAO_INCLUIR_TIFF if incluir_tiff is None else incluir_tiff
        ),
        incluir_webp=(
            settings.EXPORTACAO_INCLUIR_WEBP if incluir_webp is None else incluir_webp
        ),
    )
