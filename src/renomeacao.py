"""
Protocolo 1 - Renomeacao de imagens com verificacao de integridade.

As fotos chegam da camera/microscopio com nomes arbitrarios (DSC0001.JPG,
IMG_042.jpg, ...) e sao renomeadas para o padrao do grid da placa:
a1, a2, ..., a10, b1, ..., d10 - seguindo a ORDEM NATURAL dos arquivos.

A garantia de ordem vem do operador: as fotos sao tiradas em sequencia
a1 -> d10.

Logica migrada VERBATIM do Colab. Copia byte-a-byte (shutil.copy) com
conferencia MD5 origem<->destino e, opcionalmente, ZIP em modo ZIP_STORED
(sem recompressao) tambem verificado por MD5.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import zipfile
from pathlib import Path

from config import settings
from src.utils import natural_key, obter_logger

logger = obter_logger(__name__)

__all__ = [
    "natural_key",
    "gerar_mapeamento",
    "calcular_hash_md5",
    "renomear_com_verificacao",
    "criar_zip_sem_compressao",
    "verificar_integridade_zip",
    "executar_renomeacao",
]


# ---------------------------------------------------------------------------
# Funcoes validadas no Colab - NAO ALTERAR A LOGICA
# ---------------------------------------------------------------------------


def gerar_mapeamento(pasta_origem, letras, numeros):
    """Gera lista [(nome_original, nome_novo), ...] baseado na ordem natural."""
    extensoes = {'.jpg', '.jpeg', '.png', '.bmp', '.tiff'}
    arquivos = [arq.name for arq in Path(pasta_origem).iterdir()
                if arq.suffix.lower() in extensoes]
    arquivos = sorted(arquivos, key=natural_key)

    nomes_alvo = [f"{letra}{numero}" for letra in letras for numero in numeros]

    mapeamento = []
    total = min(len(arquivos), len(nomes_alvo))
    for i in range(total):
        extensao = Path(arquivos[i]).suffix.lower()
        mapeamento.append((arquivos[i], f"{nomes_alvo[i]}{extensao}"))

    return mapeamento, len(arquivos), len(nomes_alvo)


def calcular_hash_md5(caminho_arquivo):
    """Calcula MD5 do arquivo (chunks para arquivos grandes)."""
    hash_md5 = hashlib.md5()
    with open(caminho_arquivo, 'rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            hash_md5.update(chunk)
    return hash_md5.hexdigest()


def renomear_com_verificacao(pasta_origem, pasta_destino, mapeamento,
                             limpar_destino=True):
    """Copia byte-a-byte com verificacao MD5. Retorna dict {novo: hash}."""
    os.makedirs(pasta_destino, exist_ok=True)
    if limpar_destino:
        for arq in Path(pasta_destino).iterdir():
            if arq.is_file():
                arq.unlink()

    hashes = {}
    falhas = []
    for orig, novo in mapeamento:
        caminho_orig = os.path.join(pasta_origem, orig)
        caminho_novo = os.path.join(pasta_destino, novo)

        hash_orig = calcular_hash_md5(caminho_orig)
        shutil.copy(caminho_orig, caminho_novo)
        hash_copia = calcular_hash_md5(caminho_novo)

        if hash_orig == hash_copia:
            hashes[novo] = hash_orig
            logger.debug("OK %s -> %s (md5 %s)", orig, novo, hash_orig[:8])
        else:
            falhas.append((orig, novo))
            logger.error("MD5 DIVERGENTE: %s -> %s", orig, novo)

    return hashes, falhas


def criar_zip_sem_compressao(pasta_origem, caminho_zip):
    """Cria ZIP em modo STORED (sem compressao, bytes preservados)."""
    if os.path.exists(caminho_zip):
        os.remove(caminho_zip)
    with zipfile.ZipFile(caminho_zip, 'w', zipfile.ZIP_STORED) as zf:
        for nome in sorted(os.listdir(pasta_origem)):
            caminho = os.path.join(pasta_origem, nome)
            zf.write(caminho, arcname=nome)


def verificar_integridade_zip(caminho_zip, hashes_esperados):
    """Confirma que os bytes dentro do zip batem com os hashes originais."""
    todos_ok = True
    resultados = {}
    with zipfile.ZipFile(caminho_zip, 'r') as zf:
        for novo in sorted(hashes_esperados.keys()):
            with zf.open(novo) as f:
                hash_md5 = hashlib.md5()
                for chunk in iter(lambda: f.read(8192), b''):
                    hash_md5.update(chunk)
                hash_zip = hash_md5.hexdigest()
            ok = hash_zip == hashes_esperados[novo]
            resultados[novo] = ok
            if not ok:
                todos_ok = False
    return todos_ok, resultados


# ---------------------------------------------------------------------------
# Orquestracao da etapa (camada nova - nao altera a logica acima)
# ---------------------------------------------------------------------------


def executar_renomeacao(
    pasta_origem: Path | str | None = None,
    pasta_destino: Path | str | None = None,
    letras: list[str] | None = None,
    numeros: list[int] | None = None,
    lote_id: str | None = None,
    criar_zip: bool | None = None,
) -> dict:
    """
    Executa o protocolo 1 completo e devolve um relatorio da etapa.

    Retorno:
        {
          'mapeamento': [(orig, novo), ...],
          'hashes': {novo: md5},
          'falhas': [ {arquivo, etapa, motivo}, ... ],
          'total_arquivos': int, 'total_alvos': int,
          'zip': str | None, 'zip_ok': bool | None,
        }
    """
    pasta_origem = Path(pasta_origem or settings.PASTA_ENTRADA)
    pasta_destino = Path(pasta_destino or settings.PASTA_RENOMEADAS)
    letras = letras or settings.LETRAS_COLUNAS
    numeros = numeros or settings.NUMEROS_LINHAS
    criar_zip = settings.RENOMEACAO_CRIAR_ZIP if criar_zip is None else criar_zip

    if not pasta_origem.exists():
        raise FileNotFoundError(f"Pasta de entrada nao existe: {pasta_origem}")

    mapeamento, total_arquivos, total_alvos = gerar_mapeamento(
        pasta_origem, letras, numeros
    )
    logger.info(
        "Renomeacao: %d arquivo(s) encontrado(s), %d nome(s) alvo, %d mapeado(s).",
        total_arquivos, total_alvos, len(mapeamento),
    )

    if total_arquivos == 0:
        raise ValueError(f"Nenhuma imagem encontrada em {pasta_origem}")
    if total_arquivos != total_alvos:
        logger.warning(
            "Quantidade divergente: %d foto(s) para %d posicao(oes) do grid. "
            "Serao processadas %d.",
            total_arquivos, total_alvos, len(mapeamento),
        )

    hashes, falhas_md5 = renomear_com_verificacao(
        pasta_origem, pasta_destino, mapeamento,
        limpar_destino=settings.RENOMEACAO_LIMPAR_DESTINO,
    )
    logger.info("Renomeacao: %d arquivo(s) com MD5 conferido.", len(hashes))

    falhas = []
    for orig, novo in falhas_md5:
        motivo = "MD5 da copia diferente do original (copia corrompida)"
        falhas.append({"arquivo": orig, "etapa": "renomeacao", "motivo": motivo})
        from src.utils import registrar_falha  # import tardio evita ciclo

        registrar_falha(
            pasta_origem / orig, "renomeacao", motivo,
            {"nome_destino": novo}, lote_id=lote_id, mover_arquivo=True,
        )

    caminho_zip = None
    zip_ok = None
    if criar_zip and hashes:
        sufixo = f"_{lote_id}" if lote_id else ""
        settings.PASTA_ZIPS.mkdir(parents=True, exist_ok=True)
        caminho_zip = settings.PASTA_ZIPS / f"renomeadas{sufixo}.zip"
        criar_zip_sem_compressao(pasta_destino, str(caminho_zip))
        logger.info("ZIP (ZIP_STORED) criado: %s", caminho_zip)
        if settings.RENOMEACAO_VERIFICAR_ZIP:
            zip_ok, resultados = verificar_integridade_zip(str(caminho_zip), hashes)
            divergentes = [n for n, ok in resultados.items() if not ok]
            if zip_ok:
                logger.info("Integridade do ZIP confirmada (%d arquivos).", len(resultados))
            else:
                logger.error("ZIP com %d arquivo(s) divergente(s): %s",
                             len(divergentes), divergentes)

    return {
        "mapeamento": mapeamento,
        "hashes": hashes,
        "falhas": falhas,
        "total_arquivos": total_arquivos,
        "total_alvos": total_alvos,
        "zip": str(caminho_zip) if caminho_zip else None,
        "zip_ok": zip_ok,
    }
