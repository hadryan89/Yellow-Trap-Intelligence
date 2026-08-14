"""
Wrapper de multiprocessing para o pipeline.

Usa ProcessPoolExecutor (e nao ThreadPoolExecutor): o OpenCV so libera o GIL
parcialmente, entao threads nao dariam ganho real de throughput em CPU.

Cada worker processa UMA foto de forma independente: se uma falhar, as
demais continuam. Nenhuma imagem trafega entre processos - o worker grava o
resultado em disco e devolve apenas metadados (dict pequeno), evitando o
custo de pickle de arrays de centenas de MB.

Logging: os workers escrevem num QueueHandler; um QueueListener no processo
pai reemite os registros nos handlers reais (arquivo rotativo + console).
Isso evita corrupcao do arquivo de log por escrita concorrente no Windows.
"""

from __future__ import annotations

import functools
import logging
import logging.handlers
import multiprocessing
import os
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from contextlib import contextmanager
from typing import Any, Callable, Iterable, Sequence

from config import settings
from src.utils import formatar_duracao, obter_logger

logger = obter_logger(__name__)

__all__ = ["resolver_num_workers", "executar_em_paralelo"]


def resolver_num_workers(num_workers: int | None = None) -> int:
    """
    Resolve a quantidade de workers.

    None -> os.cpu_count() - 1 (deixa 1 core livre para o sistema).
    Nunca retorna menos que 1.
    """
    if num_workers is None:
        num_workers = settings.NUM_WORKERS
    if num_workers is None:
        num_workers = (os.cpu_count() or 2) - 1
    return max(1, int(num_workers))


# ---------------------------------------------------------------------------
# Ponte de logging pai <-> workers
# ---------------------------------------------------------------------------


def _inicializar_worker(fila) -> None:
    """Executado uma vez em cada processo filho."""
    raiz = logging.getLogger()
    for handler in list(raiz.handlers):
        raiz.removeHandler(handler)
    raiz.setLevel(logging.DEBUG)
    if fila is not None:
        raiz.addHandler(logging.handlers.QueueHandler(fila))

    try:
        import cv2

        cv2.setNumThreads(settings.PARALELISMO_CV2_THREADS)
    except Exception:  # pragma: no cover - defensivo
        pass


@contextmanager
def _ponte_de_log():
    """
    Cria a fila de log compartilhada e o listener no processo pai.

    Se o logging ainda nao estiver configurado, nao ha para onde reemitir:
    devolve None e os workers ficam sem handler (silenciosos).
    """
    handlers = list(logging.getLogger().handlers)
    if not handlers:
        yield None
        return

    gerente = multiprocessing.Manager()
    fila = gerente.Queue(-1)
    ouvinte = logging.handlers.QueueListener(fila, *handlers, respect_handler_level=True)
    ouvinte.start()
    try:
        yield fila
    finally:
        ouvinte.stop()
        gerente.shutdown()


# ---------------------------------------------------------------------------
# Execucao
# ---------------------------------------------------------------------------


def _resultado_de_erro(item: Any, exc: BaseException) -> dict:
    """Resultado sintetico para um item cujo worker morreu."""
    return {
        "arquivo": os.path.basename(str(item)),
        "caminho_entrada": str(item),
        "saida": None,
        "sucesso": False,
        "deteccao_ok": False,
        "erro": f"{type(exc).__name__}: {exc}",
        "info": None,
        "duracao_seg": 0.0,
    }


def _barra(iteravel, total: int, descricao: str):
    """tqdm quando disponivel/ligado; iteracao normal caso contrario."""
    if not settings.PARALELISMO_CHUNK_TQDM:
        return iteravel
    try:
        from tqdm import tqdm

        return tqdm(iteravel, total=total, desc=descricao, unit="foto",
                    ncols=88, leave=True)
    except ImportError:
        return iteravel


def executar_em_paralelo(
    funcao: Callable[..., dict],
    itens: Sequence[Any],
    num_workers: int | None = None,
    descricao: str = "processando",
    **kwargs_fixos: Any,
) -> list[dict]:
    """
    Aplica `funcao(item, **kwargs_fixos)` a cada item, em paralelo.

    `funcao` PRECISA ser uma funcao de modulo (top-level) para ser picklavel
    no Windows, onde o start method e 'spawn'.

    Retorna a lista de resultados NA ORDEM ORIGINAL dos itens. Um item cujo
    worker levante excecao vira um resultado com 'sucesso': False e o texto
    do erro em 'erro' - o lote nunca aborta por causa de um item.
    """
    itens = list(itens)
    if not itens:
        logger.warning("Nada a processar em '%s'.", descricao)
        return []

    workers = min(resolver_num_workers(num_workers), len(itens))
    tarefa = functools.partial(funcao, **kwargs_fixos) if kwargs_fixos else funcao
    inicio = time.perf_counter()

    if workers == 1:
        logger.info("Executando %d item(ns) em modo SEQUENCIAL (1 worker).", len(itens))
        resultados = []
        for item in _barra(itens, len(itens), descricao):
            try:
                resultados.append(tarefa(item))
            except Exception as exc:
                logger.exception("Falha ao processar %s", item)
                resultados.append(_resultado_de_erro(item, exc))
        _log_resumo(resultados, inicio, descricao)
        return resultados

    logger.info("Executando %d item(ns) em %d worker(s) paralelo(s).",
                len(itens), workers)

    resultados_por_indice: dict[int, dict] = {}
    with _ponte_de_log() as fila_log:
        try:
            with ProcessPoolExecutor(
                max_workers=workers,
                initializer=_inicializar_worker,
                initargs=(fila_log,),
            ) as executor:
                futuros = {
                    executor.submit(tarefa, item): indice
                    for indice, item in enumerate(itens)
                }
                for futuro in _barra(as_completed(futuros), len(futuros), descricao):
                    indice = futuros[futuro]
                    try:
                        resultados_por_indice[indice] = futuro.result()
                    except Exception as exc:
                        logger.error("Worker falhou em %s: %s", itens[indice], exc)
                        resultados_por_indice[indice] = _resultado_de_erro(itens[indice], exc)
        except Exception as exc:
            # Pool quebrado (ex.: worker morto pelo SO por falta de memoria).
            # Os itens que nao voltaram sao reprocessados sequencialmente.
            logger.error("Pool de processos interrompido (%s). Reprocessando os "
                         "itens pendentes sequencialmente.", exc)
            for indice, item in enumerate(itens):
                if indice in resultados_por_indice:
                    continue
                try:
                    resultados_por_indice[indice] = tarefa(item)
                except Exception as exc_item:
                    resultados_por_indice[indice] = _resultado_de_erro(item, exc_item)

    resultados = [
        resultados_por_indice.get(i) or _resultado_de_erro(item, RuntimeError("sem resultado"))
        for i, item in enumerate(itens)
    ]
    _log_resumo(resultados, inicio, descricao)
    return resultados


def _log_resumo(resultados: Iterable[dict], inicio: float, descricao: str) -> None:
    resultados = list(resultados)
    ok = sum(1 for r in resultados if r.get("sucesso"))
    duracao = time.perf_counter() - inicio
    media = duracao / len(resultados) if resultados else 0.0
    logger.info(
        "Etapa '%s': %d sucesso(s), %d falha(s) em %s (media %.2fs por item).",
        descricao, ok, len(resultados) - ok, formatar_duracao(duracao), media,
    )
