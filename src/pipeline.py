"""
Orquestracao do pipeline YellowTrap.

Encadeia os tres protocolos validados no Colab:

    01_entrada_bruta  --renomeacao-->  02_renomeadas
    02_renomeadas     --recorte-->     03_recortadas   (paralelo)
    03_recortadas     --stitching-->   04_placas_montadas/<lote_id>/

Regras de robustez:
  * uma foto ruim nunca aborta o lote - vai para data/_falhas/ com um JSON
    explicando o motivo e o pipeline continua;
  * ao final, um sumario e escrito no log e em <saida>/sumario.json.
"""

from __future__ import annotations

import gc
import time
from datetime import datetime
from pathlib import Path

from config import settings
from src import recorte as mod_recorte
from src import renomeacao as mod_renomeacao
from src import stitching as mod_stitching
from src.exportacao import exportar_placa
from src.paralelismo import executar_em_paralelo, resolver_num_workers
from src.utils import (
    Cronometro,
    SumarioLote,
    garantir_pastas,
    limpar_pasta,
    listar_imagens,
    medir_memoria_mb,
    notificar_webhook,
    obter_logger,
)

logger = obter_logger(__name__)

__all__ = [
    "novo_lote_id",
    "etapa_renomeacao",
    "etapa_recorte",
    "etapa_stitching_e_exportacao",
    "executar_pipeline_completo",
]


def novo_lote_id() -> str:
    """Identificador do lote baseado no relogio local: 20260813_142530."""
    return datetime.now().strftime("%Y%m%d_%H%M%S")


# ---------------------------------------------------------------------------
# Etapas
# ---------------------------------------------------------------------------


def etapa_renomeacao(sumario: SumarioLote, pasta_entrada: Path,
                     pasta_destino: Path, criar_zip: bool | None = None) -> dict:
    """Protocolo 1. Atualiza o sumario e devolve o relatorio da etapa."""
    with Cronometro("ETAPA 1/3 - Renomeacao", logger):
        relatorio = mod_renomeacao.executar_renomeacao(
            pasta_origem=pasta_entrada,
            pasta_destino=pasta_destino,
            letras=settings.LETRAS_COLUNAS,
            numeros=settings.NUMEROS_LINHAS,
            lote_id=sumario.lote_id,
            criar_zip=criar_zip,
        )
    sumario.total_entrada = relatorio["total_arquivos"]
    sumario.renomeadas = len(relatorio["hashes"])
    sumario.falhas.extend(relatorio["falhas"])
    return relatorio


def etapa_recorte(sumario: SumarioLote, pasta_entrada: Path, pasta_saida: Path,
                  num_workers: int | None = None,
                  formato: str | None = None) -> list[dict]:
    """Protocolo 2, paralelizado. Atualiza o sumario e devolve os resultados."""
    formato = formato or settings.RECORTE_FORMATO_SAIDA
    arquivos = listar_imagens(pasta_entrada)
    if not arquivos:
        raise ValueError(f"Nenhuma imagem para recortar em {pasta_entrada}")

    if settings.LIMPAR_PASTAS_INTERMEDIARIAS:
        removidos = limpar_pasta(pasta_saida)
        if removidos:
            logger.info("Pasta de recortes limpa (%d arquivo(s) de lote anterior).",
                        removidos)

    workers = resolver_num_workers(num_workers)
    logger.info(
        "Recorte: %d foto(s), formato '%s', %d worker(s). Parametros: "
        "fator_deteccao=%s margem=%s modo=%s span_v_ini=%s span_min=%s dilatar=%s",
        len(arquivos), formato, workers,
        settings.RECORTE_FATOR_DETECCAO, settings.RECORTE_MARGEM,
        settings.RECORTE_MODO, settings.RECORTE_SPAN_V_INICIAL_FRAC,
        settings.RECORTE_SPAN_MINIMO_FRAC, settings.RECORTE_DILATAR,
    )

    with Cronometro("ETAPA 2/3 - Recorte", logger):
        resultados = executar_em_paralelo(
            mod_recorte.processar_foto,
            [str(a) for a in arquivos],
            num_workers=workers,
            descricao="recorte",
            pasta_saida=str(pasta_saida),
            formato=formato,
            lote_id=sumario.lote_id,
        )

    for resultado in resultados:
        if resultado.get("sucesso"):
            sumario.recortadas_ok += 1
            if not resultado.get("deteccao_ok"):
                sumario.recortadas_sem_deteccao += 1
                logger.warning(
                    "%s: quadrante salvo SEM recorte (deteccao falhou).",
                    resultado.get("arquivo"),
                )
        else:
            sumario.falhas.append({
                "arquivo": resultado.get("arquivo"),
                "etapa": "recorte",
                "motivo": resultado.get("erro"),
            })
            logger.error("Falha no recorte de %s: %s",
                         resultado.get("arquivo"), resultado.get("erro"))
    return resultados


def etapa_stitching_e_exportacao(sumario: SumarioLote, pasta_quadrantes: Path,
                                 pasta_saida: Path,
                                 escala: float | None = None) -> list[str]:
    """Protocolo 3 + exportacao. Atualiza o sumario e devolve os arquivos gerados."""
    with Cronometro("ETAPA 3/3 - Stitching + exportacao", logger):
        placa, estatisticas = mod_stitching.executar_stitching(
            pasta_imagens=pasta_quadrantes,
            ordem_letras=settings.LETRAS_COLUNAS,
            ordem_numeros=settings.NUMEROS_LINHAS,
            escala=escala,
        )
        sumario.quadrantes_no_stitching = estatisticas["quadrantes_carregados"]
        sumario.placeholders = estatisticas["placeholders"]
        sumario.dimensao_placa = tuple(estatisticas["dimensao_placa"])

        for celula in estatisticas["celulas_faltantes"]:
            sumario.falhas.append({
                "arquivo": celula,
                "etapa": "stitching",
                "motivo": "quadrante ausente - celula preenchida com placeholder",
            })

        pasta_saida.mkdir(parents=True, exist_ok=True)
        arquivos = exportar_placa(placa, pasta_saida)

        del placa
        gc.collect()

    sumario.arquivos_gerados = arquivos
    sumario.pasta_saida = str(pasta_saida)
    return arquivos


# ---------------------------------------------------------------------------
# Pipeline completo
# ---------------------------------------------------------------------------


def executar_pipeline_completo(
    pasta_entrada: Path | str | None = None,
    lote_id: str | None = None,
    num_workers: int | None = None,
    formato_recorte: str | None = None,
    criar_zip: bool | None = None,
    pular_renomeacao: bool = False,
    escala_stitching: float | None = None,
    notificar: bool | None = None,
) -> SumarioLote:
    """
    Roda os 3 protocolos em sequencia e devolve o SumarioLote.

    Levanta excecao apenas em falhas estruturais (pasta inexistente, lote
    vazio, nenhum quadrante recortado). Falhas de fotos individuais sao
    registradas no sumario e nao interrompem a execucao.
    """
    inicio = time.perf_counter()
    lote_id = lote_id or novo_lote_id()
    pasta_entrada = Path(pasta_entrada or settings.PASTA_ENTRADA)
    pasta_renomeadas = Path(settings.PASTA_RENOMEADAS)
    pasta_recortadas = Path(settings.PASTA_RECORTADAS)
    pasta_saida = Path(settings.PASTA_PLACAS) / lote_id

    garantir_pastas(*settings.PASTAS_OBRIGATORIAS)

    sumario = SumarioLote(lote_id=lote_id)
    logger.info("=" * 68)
    logger.info("INICIO DO LOTE %s | entrada: %s", lote_id, pasta_entrada)
    logger.info("=" * 68)

    try:
        # --- Etapa 1: renomeacao ---
        if pular_renomeacao:
            logger.info("ETAPA 1/3 - Renomeacao PULADA (--pular-renomeacao).")
            pasta_renomeadas = pasta_entrada
            sumario.total_entrada = len(listar_imagens(pasta_entrada))
            sumario.renomeadas = sumario.total_entrada
        else:
            etapa_renomeacao(sumario, pasta_entrada, pasta_renomeadas,
                             criar_zip=criar_zip)

        if sumario.renomeadas != settings.QUANTIDADE_ESPERADA:
            logger.warning(
                "Lote com %d foto(s) - esperado %d. Celulas sem quadrante "
                "receberao o placeholder amarelo.",
                sumario.renomeadas, settings.QUANTIDADE_ESPERADA,
            )

        # --- Etapa 2: recorte paralelo ---
        etapa_recorte(sumario, pasta_renomeadas, pasta_recortadas,
                      num_workers=num_workers, formato=formato_recorte)

        if sumario.recortadas_ok == 0:
            raise ValueError(
                "Nenhum quadrante foi recortado com sucesso - stitching abortado."
            )

        # --- Etapa 3: stitching + exportacao ---
        etapa_stitching_e_exportacao(sumario, pasta_recortadas, pasta_saida,
                                     escala=escala_stitching)
        sumario.sucesso = True
    except Exception as exc:
        logger.exception("LOTE %s ABORTADO: %s", lote_id, exc)
        sumario.falhas.append({
            "arquivo": None, "etapa": "pipeline", "motivo": f"{type(exc).__name__}: {exc}",
        })
        sumario.sucesso = False
    finally:
        sumario.duracao_seg = time.perf_counter() - inicio
        sumario.memoria_pico_mb = medir_memoria_mb()
        for linha in sumario.linhas_relatorio():
            logger.info(linha)
        try:
            destino_json = (Path(sumario.pasta_saida) if sumario.pasta_saida
                            else Path(settings.PASTA_FALHAS) / lote_id)
            caminho = sumario.salvar_json(destino_json / "sumario.json")
            logger.info("Sumario salvo em %s", caminho)
        except Exception as exc:  # pragma: no cover - defensivo
            logger.error("Nao foi possivel salvar o sumario: %s", exc)

        notificar = settings.WEBHOOK_ATIVO if notificar is None else notificar
        if notificar:
            notificar_webhook(sumario)

    return sumario
