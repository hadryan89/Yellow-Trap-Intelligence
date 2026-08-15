"""
Wrapper de multiprocessing para o pipeline.

Usa ProcessPoolExecutor (e nao ThreadPoolExecutor): o OpenCV so libera o GIL
parcialmente, entao threads nao dariam ganho real de throughput em CPU.

Cada worker processa UMA foto de forma independente: se uma falhar, as
demais continuam. Nenhuma imagem trafega entre processos - o worker grava o
resultado em disco e devolve apenas metadados (dict pequeno), evitando o
custo de pickle de arrays de centenas de MB.

ESCALA
------
Duas decisoes fazem o modulo aguentar dezenas de milhares de itens sem
inchar a memoria do processo pai:

  * submissao em JANELA: no maximo `workers x PARALELISMO_JANELA_POR_WORKER`
    tarefas ficam submetidas ao mesmo tempo. Sem isso, 2.000 fotos viram
    2.000 objetos Future vivos de uma vez;
  * agregacao INCREMENTAL: com `ao_concluir` + `reter_resultados=False` o
    chamador consome cada resultado na hora e nada e acumulado.

Logging: os workers escrevem num QueueHandler; um QueueListener no processo
pai reemite os registros nos handlers reais (arquivo rotativo + console).
Isso evita corrupcao do arquivo de log por escrita concorrente no Windows.
O nivel dentro do worker e limitado por PARALELISMO_NIVEL_LOG_WORKERS: cada
registro custa uma travessia de fila entre processos.
"""

from __future__ import annotations

import functools
import logging
import logging.handlers
import multiprocessing
import os
import time
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from contextlib import contextmanager
from typing import Any, Callable, Iterable, Sequence

from config import settings
from src.utils import formatar_duracao, obter_logger

logger = obter_logger(__name__)

__all__ = ["resolver_num_workers", "executar_em_paralelo", "descrever_item"]


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


def descrever_item(item: Any) -> str:
    """
    Nome legivel de um item da fila.

    Um item pode ser um caminho ou a tupla (caminho, nome_de_saida) usada
    quando a renomeacao e aplicada direto na saida do recorte.
    """
    if isinstance(item, (tuple, list)) and item:
        item = item[0]
    return os.path.basename(str(item))


# ---------------------------------------------------------------------------
# Ponte de logging pai <-> workers
# ---------------------------------------------------------------------------


def _inicializar_worker(fila, nivel: str = "INFO") -> None:
    """Executado uma vez em cada processo filho."""
    raiz = logging.getLogger()
    for handler in list(raiz.handlers):
        raiz.removeHandler(handler)
    raiz.setLevel(getattr(logging, str(nivel).upper(), logging.INFO))
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
    caminho = item[0] if isinstance(item, (tuple, list)) and item else item
    return {
        "arquivo": descrever_item(item),
        "caminho_entrada": str(caminho),
        "saida": None,
        "sucesso": False,
        "deteccao_ok": False,
        "pulado": False,
        "erro": f"{type(exc).__name__}: {exc}",
        "info": None,
        "duracao_seg": 0.0,
    }


class _Progresso:
    """Barra de progresso opcional (tqdm), atualizada item a item."""

    def __init__(self, total: int, descricao: str):
        self.barra = None
        if not settings.PARALELISMO_CHUNK_TQDM:
            return
        try:
            from tqdm import tqdm

            self.barra = tqdm(total=total, desc=descricao, unit="foto",
                              ncols=88, leave=True)
        except ImportError:
            self.barra = None

    def avancar(self, quantidade: int = 1) -> None:
        if self.barra is not None:
            self.barra.update(quantidade)

    def fechar(self) -> None:
        if self.barra is not None:
            self.barra.close()


def executar_em_paralelo(
    funcao: Callable[..., dict],
    itens: Sequence[Any],
    num_workers: int | None = None,
    descricao: str = "processando",
    ao_concluir: Callable[[int, dict], None] | None = None,
    reter_resultados: bool = True,
    janela: int | None = None,
    **kwargs_fixos: Any,
) -> list[dict]:
    """
    Aplica `funcao(item, **kwargs_fixos)` a cada item, em paralelo.

    `funcao` PRECISA ser uma funcao de modulo (top-level) para ser picklavel
    no Windows, onde o start method e 'spawn'.

    ao_concluir      callback (indice, resultado) chamado no processo pai a
                     cada item terminado - permite agregar sem guardar tudo.
    reter_resultados False devolve [] e mantem a memoria constante,
                     independentemente do tamanho do lote.
    janela           quantas tarefas ficam submetidas ao mesmo tempo.

    Com reter_resultados=True a lista devolvida segue a ORDEM ORIGINAL dos
    itens. Um item cujo worker levante excecao vira um resultado com
    'sucesso': False e o texto do erro em 'erro' - o lote nunca aborta por
    causa de um item.
    """
    itens = list(itens)
    if not itens:
        logger.warning("Nada a processar em '%s'.", descricao)
        return []

    workers = min(resolver_num_workers(num_workers), len(itens))
    tarefa = functools.partial(funcao, **kwargs_fixos) if kwargs_fixos else funcao
    inicio = time.perf_counter()

    resultados_por_indice: dict[int, dict] = {}
    contagem = {"ok": 0, "total": 0}
    progresso = _Progresso(len(itens), descricao)

    def _registrar(indice: int, resultado: dict) -> None:
        contagem["total"] += 1
        if resultado.get("sucesso"):
            contagem["ok"] += 1
        if reter_resultados:
            resultados_por_indice[indice] = resultado
        elif ao_concluir is None:
            resultados_por_indice[indice] = resultado  # ninguem mais consumiria
        if ao_concluir is not None:
            try:
                ao_concluir(indice, resultado)
            except Exception:  # pragma: no cover - defensivo
                logger.exception("Callback de agregacao falhou no item %d", indice)
        progresso.avancar()

    def _sequencial(indices: Iterable[int]) -> None:
        for indice in indices:
            if indice in resultados_por_indice:
                continue
            try:
                _registrar(indice, tarefa(itens[indice]))
            except Exception as exc:
                logger.exception("Falha ao processar %s", itens[indice])
                _registrar(indice, _resultado_de_erro(itens[indice], exc))

    try:
        if workers == 1:
            logger.info("Executando %d item(ns) em modo SEQUENCIAL (1 worker).",
                        len(itens))
            _sequencial(range(len(itens)))
        else:
            janela = janela or max(workers * settings.PARALELISMO_JANELA_POR_WORKER,
                                   workers)
            logger.info(
                "Executando %d item(ns) em %d worker(s) paralelo(s) "
                "(janela de %d tarefa(s)).", len(itens), workers, janela,
            )
            concluidos_indices: set[int] = set()
            try:
                extras = {}
                if settings.PARALELISMO_TAREFAS_POR_WORKER:
                    extras["max_tasks_per_child"] = int(
                        settings.PARALELISMO_TAREFAS_POR_WORKER)

                with _ponte_de_log() as fila_log:
                    with ProcessPoolExecutor(
                        max_workers=workers,
                        initializer=_inicializar_worker,
                        initargs=(fila_log, settings.PARALELISMO_NIVEL_LOG_WORKERS),
                        **extras,
                    ) as executor:
                        pendentes: dict[Any, int] = {}
                        proximo = 0

                        def _abastecer() -> None:
                            nonlocal proximo
                            while proximo < len(itens) and len(pendentes) < janela:
                                pendentes[executor.submit(tarefa, itens[proximo])] = proximo
                                proximo += 1

                        _abastecer()
                        while pendentes:
                            prontos, _ = wait(pendentes, return_when=FIRST_COMPLETED)
                            for futuro in prontos:
                                indice = pendentes.pop(futuro)
                                try:
                                    resultado = futuro.result()
                                except Exception as exc:
                                    logger.error("Worker falhou em %s: %s",
                                                 descrever_item(itens[indice]), exc)
                                    resultado = _resultado_de_erro(itens[indice], exc)
                                concluidos_indices.add(indice)
                                _registrar(indice, resultado)
                            _abastecer()
            except Exception as exc:
                # Pool quebrado (ex.: worker morto pelo SO por falta de memoria).
                # Os itens que nao voltaram sao reprocessados sequencialmente.
                logger.error("Pool de processos interrompido (%s). Reprocessando "
                             "os itens pendentes sequencialmente.", exc)
                _sequencial(i for i in range(len(itens))
                            if i not in concluidos_indices)
    finally:
        progresso.fechar()

    _log_resumo(contagem["ok"], contagem["total"], inicio, descricao)

    if not reter_resultados and ao_concluir is not None:
        return []
    return [
        resultados_por_indice.get(i)
        or _resultado_de_erro(item, RuntimeError("sem resultado"))
        for i, item in enumerate(itens)
    ]


def _log_resumo(ok: int, total: int, inicio: float, descricao: str) -> None:
    duracao = time.perf_counter() - inicio
    media = duracao / total if total else 0.0
    taxa = total / duracao if duracao else 0.0
    logger.info(
        "Etapa '%s': %d sucesso(s), %d falha(s) em %s "
        "(media %.2fs por item, %.2f item/s).",
        descricao, ok, total - ok, formatar_duracao(duracao), media, taxa,
    )
