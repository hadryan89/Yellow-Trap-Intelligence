"""
Gravacao em disco: quadrantes recortados e placas montadas.

Reune os dois pontos do pipeline em que pixels vao para o disco:

  * salvar_recortada() - quadrante individual (protocolo 2)
  * exportar_placa()   - placa montada, frente e verso num unico PNG de
                         1920 x 1080 (protocolo 3)

salvar_recortada() vem do Colab: PNG=1 e TIFF=5/LZW sao lossless e JPEG sai
com qualidade 100.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from config import settings
from src.utils import imwrite_fallback, obter_logger

logger = obter_logger(__name__)

__all__ = [
    "salvar_recortada",
    "exportar_placa",
    "compor_quadro",
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


def compor_quadro(placa, largura: int | None = None, altura: int | None = None,
                  rotulos=None):
    """
    Encaixa a placa num quadro de exatamente `largura` x `altura` px.

    A placa e reduzida sem distorcer e centralizada; a sobra vira fundo. Os
    `rotulos` (um por lado do papel, de cima para baixo) vao na margem
    esquerda, na altura do lado correspondente - se a margem for estreita
    demais para o texto, o rotulo e omitido.
    """
    largura = largura or settings.EXPORTACAO_LARGURA
    altura = altura or settings.EXPORTACAO_ALTURA
    rotulos = settings.MONTAGEM_ROTULOS_LADOS if rotulos is None else rotulos

    altura_placa, largura_placa = placa.shape[:2]
    fator = min(largura / largura_placa, altura / altura_placa)
    nova_largura = max(1, round(largura_placa * fator))
    nova_altura = max(1, round(altura_placa * fator))
    interpolacao = cv2.INTER_AREA if fator < 1 else cv2.INTER_LINEAR
    reduzida = cv2.resize(placa, (nova_largura, nova_altura),
                          interpolation=interpolacao)

    quadro = np.empty((altura, largura, 3), dtype=np.uint8)
    quadro[:] = settings.EXPORTACAO_COR_FUNDO
    x0 = (largura - nova_largura) // 2
    y0 = (altura - nova_altura) // 2
    quadro[y0:y0 + nova_altura, x0:x0 + nova_largura] = reduzida

    if rotulos:
        _escrever_rotulos(quadro, rotulos, margem=x0, y0=y0, altura_placa=nova_altura)
    return quadro


def _escrever_rotulos(quadro, rotulos, margem: int, y0: int, altura_placa: int) -> None:
    fonte = cv2.FONT_HERSHEY_SIMPLEX
    folga = max(4, margem // 10)
    espessura = 2
    maior, _ = cv2.getTextSize(max(rotulos, key=len), fonte, 1.0, espessura)
    escala = min(1.2, (margem - 2 * folga) / maior[0]) if maior[0] else 0
    if escala < 0.4:
        logger.debug("Margem de %d px estreita demais para os rotulos dos lados.",
                     margem)
        return
    altura_lado = altura_placa / len(rotulos)
    for i, rotulo in enumerate(rotulos):
        (w, h), _ = cv2.getTextSize(rotulo, fonte, escala, espessura)
        x = (margem - w) // 2
        y = round(y0 + (i + 0.5) * altura_lado + h / 2)
        cv2.putText(quadro, rotulo, (x, y), fonte, escala,
                    settings.EXPORTACAO_COR_ROTULO, espessura, cv2.LINE_AA)


def exportar_placa(placa, pasta_export: Path | str, nome: str) -> list[str]:
    """
    Exporta UMA placa como <nome>.png, em EXPORTACAO_LARGURA x EXPORTACAO_ALTURA.

    O nome da placa e o nome do arquivo, entao as varias placas de um lote
    convivem na mesma pasta. Devolve os nomes gravados.
    """
    pasta_export = Path(pasta_export)
    pasta_export.mkdir(parents=True, exist_ok=True)
    quadro = compor_quadro(placa)
    nome_arquivo = f"{nome}.png"
    _gravar(pasta_export / nome_arquivo, quadro, [cv2.IMWRITE_PNG_COMPRESSION, 3])
    logger.info("Exportado %s (%d x %d px)", nome_arquivo,
                quadro.shape[1], quadro.shape[0])
    return [nome_arquivo]
