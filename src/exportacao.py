"""
Gravacao em disco: quadrantes recortados e placas montadas.

Reune os dois pontos do pipeline em que pixels vao para o disco:

  * salvar_recortada() - quadrante individual (protocolo 2)
  * exportar_placa()   - placa montada, em multiplas resolucoes (protocolo 3)

Ambas vem do Colab. Os parametros de compressao (PNG=1, TIFF=5/LZW, WEBP=95)
sao lossless ou de altissima qualidade e estao espelhados em
config/settings.py apenas para consulta.
"""

from __future__ import annotations

from pathlib import Path

import cv2

from config import settings
from src.utils import imwrite_fallback, obter_logger

logger = obter_logger(__name__)

__all__ = [
    "salvar_recortada",
    "exportar_placa",
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


def _gravar(caminho: Path, imagem, params: list) -> None:
    if not cv2.imwrite(str(caminho), imagem, params):
        # Resgate para caminhos com acentos no Windows (bytes identicos).
        if not imwrite_fallback(caminho, imagem, params):
            raise IOError(f"cv2.imwrite falhou ao gravar {caminho}")


def _redimensionar_para_largura(placa, largura: int):
    altura_placa, largura_placa = placa.shape[:2]
    altura = max(1, int(largura * altura_placa / largura_placa))
    return cv2.resize(placa, (largura, altura), interpolation=cv2.INTER_AREA)


def exportar_placa(placa, pasta_export: Path | str, nome: str,
                   resolucoes=None,
                   incluir_png_lossless: bool | None = None,
                   incluir_tiff: bool | None = None,
                   incluir_webp: bool | None = None) -> list[str]:
    """
    Exporta UMA placa em multiplas resolucoes + formatos lossless.

    Os arquivos levam o nome da placa como prefixo, entao as varias placas de
    um lote convivem na mesma pasta:

        <nome>_10k.jpg  <nome>_4k.jpg  <nome>_1200p.jpg  <nome>_720p.jpg
        <nome>_LOSSLESS.png  <nome>_CIENTIFICO.tiff  <nome>_WEBP.webp

    `resolucoes`: lista de tuplas (sufixo, largura_px, qualidade_jpg). Os
    demais parametros tem default em settings.py. Devolve os nomes gravados.
    """
    resolucoes = (settings.EXPORTACAO_RESOLUCOES if resolucoes is None
                  else resolucoes)
    if incluir_png_lossless is None:
        incluir_png_lossless = settings.EXPORTACAO_INCLUIR_PNG_LOSSLESS
    if incluir_tiff is None:
        incluir_tiff = settings.EXPORTACAO_INCLUIR_TIFF
    if incluir_webp is None:
        incluir_webp = settings.EXPORTACAO_INCLUIR_WEBP

    pasta_export = Path(pasta_export)
    pasta_export.mkdir(parents=True, exist_ok=True)
    altura_placa, largura_placa = placa.shape[:2]
    arquivos_gerados = []

    # JPEG em multiplas resolucoes
    for sufixo, largura_alvo, qualidade in resolucoes:
        if largura_alvo > largura_placa:
            logger.debug(
                "Resolucao '%s' (%d px) ignorada: maior que a placa (%d px). "
                "Nao ha upscale.", sufixo, largura_alvo, largura_placa,
            )
            continue  # nao faz upscale
        placa_redim = _redimensionar_para_largura(placa, largura_alvo)
        nome_arquivo = f"{nome}_{sufixo}.jpg"
        _gravar(pasta_export / nome_arquivo, placa_redim,
                [cv2.IMWRITE_JPEG_QUALITY, qualidade, cv2.IMWRITE_JPEG_OPTIMIZE, 1])
        del placa_redim
        arquivos_gerados.append(nome_arquivo)
        logger.info("Exportado %s", nome_arquivo)

    # PNG lossless (resolucao original)
    if incluir_png_lossless:
        nome_arquivo = f"{nome}_LOSSLESS.png"
        _gravar(pasta_export / nome_arquivo, placa, [cv2.IMWRITE_PNG_COMPRESSION, 1])
        arquivos_gerados.append(nome_arquivo)
        logger.info("Exportado %s (%d x %d px)", nome_arquivo,
                    largura_placa, altura_placa)

    # TIFF (padrao cientifico, abre no ImageJ/Fiji)
    if incluir_tiff:
        nome_arquivo = f"{nome}_CIENTIFICO.tiff"
        _gravar(pasta_export / nome_arquivo, placa, [cv2.IMWRITE_TIFF_COMPRESSION, 5])
        arquivos_gerados.append(nome_arquivo)
        logger.info("Exportado %s (TIFF LZW)", nome_arquivo)

    # WEBP (compressao moderna) - limitado pelo formato a 16383 px por lado.
    if incluir_webp:
        nome_arquivo = f"{nome}_WEBP.webp"
        lado_max = settings.EXPORTACAO_WEBP_LADO_MAXIMO
        imagem_webp = placa
        if max(altura_placa, largura_placa) > lado_max:
            largura_webp = min(lado_max,
                               int(lado_max * largura_placa / altura_placa))
            imagem_webp = _redimensionar_para_largura(placa, largura_webp)
            logger.warning(
                "%s: a placa (%d x %d px) passa do limite do WEBP (%d px por "
                "lado) - exportado reduzido para %d x %d px.", nome_arquivo,
                largura_placa, altura_placa, lado_max,
                imagem_webp.shape[1], imagem_webp.shape[0],
            )
        _gravar(pasta_export / nome_arquivo, imagem_webp,
                [cv2.IMWRITE_WEBP_QUALITY, 95])
        del imagem_webp
        arquivos_gerados.append(nome_arquivo)
        logger.info("Exportado %s", nome_arquivo)

    return arquivos_gerados
