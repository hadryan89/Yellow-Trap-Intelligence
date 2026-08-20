"""
Orquestracao do pipeline YellowTrap.

O pipeline tem DUAS etapas e termina no recorte:

    01_entrada_bruta  --nomeacao-->  (plano de nomes)
                      --recorte-->   03_recortadas

UMA foto de entrada gera UM arquivo de saida. O plano de nomes so vira
arquivo em 02_renomeadas quando alguem pede explicitamente a estrategia
copiar/hardlink/mover; no caminho padrao (virtual, em todos os modos) ele e
aplicado direto no arquivo do recorte - o quadrante ja nasce com o nome
final, sem copia intermediaria. Jogar 30 fotos na entrada produz 30
quadrantes, e mais nada.

Modos (config/settings.py -> MODOS_VALIDOS):

    grid        a1..d10, ate 40 fotos por lote (comportamento historico)
    sequencial  VARD1, VARD2, VARD3, ... sem limite de quantidade
    recorte     preserva o nome de origem

Regras de robustez:
  * uma foto ruim nunca aborta o lote - vai para data/_falhas/ com um JSON
    explicando o motivo e o pipeline continua;
  * ao final, um sumario e escrito no log e em
    data/_relatorios/<lote_id>/sumario.json.
"""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

from config import settings
from src import recorte as mod_recorte
from src import renomeacao as mod_renomeacao
from src.exportacao import extensao_do_formato
from src.opcoes import OpcoesProcessamento
from src.paralelismo import descrever_item, executar_em_paralelo, resolver_num_workers
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
    "etapa_nomeacao",
    "etapa_recorte",
    "executar_processamento",
    "executar_pipeline_completo",
    "processar_pasta",
]


def novo_lote_id() -> str:
    """Identificador do lote baseado no relogio local: 20260813_142530."""
    return datetime.now().strftime("%Y%m%d_%H%M%S")


# ---------------------------------------------------------------------------
# Etapa 1 - nomeacao
# ---------------------------------------------------------------------------


def etapa_nomeacao(sumario: SumarioLote, opcoes: OpcoesProcessamento,
                   arquivos: list[Path]) -> mod_renomeacao.ResultadoNomeacao:
    """
    Monta o plano de nomes e (se a estrategia pedir) materializa em disco.

    Devolve o ResultadoNomeacao, cujo campo `itens` e a fila que a etapa de
    recorte consome: pares (caminho_a_recortar, nome_de_saida).
    """
    inicio = opcoes.indice_inicial
    if opcoes.modo == settings.MODO_SEQUENCIAL and opcoes.continuar_numeracao:
        pastas = [opcoes.pasta_recortadas]
        if opcoes.materializa_renomeadas:
            pastas.append(opcoes.pasta_renomeadas)
        inicio = mod_renomeacao.proximo_indice_sequencial(
            pastas, opcoes.prefixo, opcoes.digitos, opcoes.indice_inicial)
        logger.info("Numeracao sequencial continua a partir de %s%0*d.",
                    opcoes.prefixo, opcoes.digitos, inicio)

    plano = mod_renomeacao.planejar_nomeacao(
        arquivos,
        modo=opcoes.modo,
        prefixo=opcoes.prefixo,
        digitos=opcoes.digitos,
        inicio=inicio,
    )

    sumario.total_entrada = plano.total_arquivos
    sumario.ignoradas = len(plano.ignorados)
    if plano.ignorados:
        logger.error(
            "Modo '%s': o grid tem %d posicoes e chegaram %d foto(s). "
            "%d foto(s) FICARAM DE FORA (%s...). Use o modo 'sequencial' "
            "para lotes maiores que o grid.",
            opcoes.modo, plano.total_alvos, plano.total_arquivos,
            len(plano.ignorados), ", ".join(plano.ignorados[:3]),
        )
        for nome in plano.ignorados[:settings.SUMARIO_MAX_FALHAS_DETALHADAS]:
            sumario.adicionar_falha(nome, "nomeacao",
                                    "fora do grid (lote maior que as posicoes)")

    if not opcoes.renomeia:
        logger.info("Nomeacao: %d foto(s) mantem o nome de origem.", len(plano))
    elif opcoes.modo == settings.MODO_SEQUENCIAL and plano.itens:
        logger.info("Nomeacao sequencial: %d foto(s), de %s a %s.",
                    len(plano), plano.itens[0].nome_novo, plano.itens[-1].nome_novo)
    else:
        logger.info("Nomeacao no grid: %d foto(s) mapeada(s) em %s posicoes.",
                    len(plano), plano.total_alvos)

    if opcoes.simular:
        return mod_renomeacao.ResultadoNomeacao(
            itens=[(str(i.origem), i.stem_novo) for i in plano.itens],
            estrategia=settings.ESTRATEGIA_VIRTUAL,
        )

    if not opcoes.materializa_renomeadas:
        # Caminho padrao: o nome novo vai direto no arquivo do recorte. Uma
        # foto de entrada = um arquivo de saida, sem copia intermediaria.
        logger.info("Nomeacao aplicada direto no recorte (estrategia 'virtual'): "
                    "nenhuma copia intermediaria sera gravada em %s.",
                    opcoes.pasta_renomeadas)
    elif opcoes.duplica_em_disco:
        logger.warning(
            "Estrategia '%s': %d copia(s) do lote serao gravadas em %s ALEM "
            "dos quadrantes recortados. Use 'virtual' (padrao) para nao "
            "duplicar as fotos em disco.",
            opcoes.estrategia_renomeacao, len(plano), opcoes.pasta_renomeadas)

    with Cronometro(f"ETAPA 1/2 - Nomeacao ({opcoes.estrategia_renomeacao})", logger):
        resultado = mod_renomeacao.aplicar_plano(
            plano,
            pasta_destino=opcoes.pasta_renomeadas,
            estrategia=opcoes.estrategia_renomeacao,
            verificar_md5=opcoes.verificar_md5,
            limpar_destino=opcoes.limpar_destino_renomeadas,
            workers=opcoes.resolver_workers_io(),
            lote_id=sumario.lote_id,
        )

    sumario.renomeadas = len(resultado.itens)
    # So conta o que virou arquivo NOVO em 02_renomeadas. Em 'virtual' e
    # 'mover' nada e duplicado, entao o contador fica em zero.
    if opcoes.duplica_em_disco:
        sumario.arquivos_intermediarios = len(resultado.itens)
    sumario.estender_falhas(resultado.falhas)

    if opcoes.criar_zip and opcoes.materializa_renomeadas:
        resultado.zip, resultado.zip_ok = mod_renomeacao.gerar_zip_de_integridade(
            opcoes.pasta_renomeadas, resultado.hashes, lote_id=sumario.lote_id)

    return resultado


# ---------------------------------------------------------------------------
# Etapa 2 - recorte
# ---------------------------------------------------------------------------


def etapa_recorte(sumario: SumarioLote, itens, opcoes: OpcoesProcessamento | None = None,
                  pasta_saida: Path | None = None, num_workers: int | None = None,
                  formato: str | None = None,
                  reter_resultados: bool | None = None) -> list[dict]:
    """
    Recorta a fila `itens` em paralelo, agregando o resultado no sumario.

    `itens` aceita caminhos soltos ou pares (caminho, nome_de_saida). Com
    `opcoes` informado, o resto dos parametros vem de la.

    Compatibilidade: `etapa_recorte(sumario, pasta_entrada, pasta_saida)`
    continua funcionando - uma pasta e listada e vira a fila.
    """
    if isinstance(opcoes, (str, Path)):
        # Chamada no formato antigo: etapa_recorte(sumario, entrada, saida).
        pasta_saida, opcoes = Path(opcoes), None
    if opcoes is None:
        opcoes = OpcoesProcessamento(
            pasta_recortadas=pasta_saida, formato=formato, workers=num_workers,
            modo=settings.MODO_RECORTE,
        )
    pasta_saida = Path(pasta_saida or opcoes.pasta_recortadas)
    formato = formato or opcoes.formato

    # Modo de compatibilidade: recebeu uma pasta em vez de uma fila.
    if isinstance(itens, (str, Path)):
        itens = [str(a) for a in listar_imagens(Path(itens), limite=opcoes.limite)]

    itens = list(itens)
    if not itens:
        raise ValueError("Nenhuma imagem para recortar.")

    if opcoes.limpar_saida:
        removidos = limpar_pasta(pasta_saida)
        if removidos:
            logger.info("Pasta de recortes limpa (%d arquivo(s) de lote anterior).",
                        removidos)

    workers = resolver_num_workers(num_workers if num_workers is not None
                                   else opcoes.workers)
    if reter_resultados is None:
        reter_resultados = (opcoes.reter_resultados if opcoes.reter_resultados
                            is not None else
                            len(itens) <= settings.PARALELISMO_LIMIAR_STREAMING)

    logger.info(
        "Recorte: %d foto(s), formato '%s', %d worker(s). Parametros: "
        "perfil=%s fator_deteccao=%s margem_frac=%s espessura_max_frac=%s "
        "inclinacao_max=%s graus cortar_moldura=%s",
        len(itens), formato, workers,
        opcoes.perfil, settings.RECORTE_FATOR_DETECCAO,
        settings.RECORTE_MARGEM_FRAC, settings.RECORTE_ESPESSURA_MAX_FRAC,
        settings.RECORTE_INCLINACAO_MAX_GRAUS, settings.RECORTE_CORTAR_MOLDURA,
    )

    def agregar(indice: int, resultado: dict) -> None:
        """Agregacao incremental: nada do lote fica acumulado em memoria."""
        if resultado.get("pulado"):
            sumario.puladas += 1
            return
        if resultado.get("sucesso"):
            sumario.recortadas_ok += 1
            if not resultado.get("deteccao_ok"):
                sumario.recortadas_sem_deteccao += 1
                logger.warning("%s: quadrante salvo SEM recorte (deteccao falhou).",
                               resultado.get("arquivo"))
            return
        sumario.recortadas_falha += 1
        sumario.adicionar_falha(resultado.get("arquivo"), "recorte",
                                resultado.get("erro"))
        logger.error("Falha no recorte de %s: %s",
                     resultado.get("arquivo"), resultado.get("erro"))

    with Cronometro("ETAPA 2/2 - Recorte", logger):
        resultados = executar_em_paralelo(
            mod_recorte.processar_item,
            itens,
            num_workers=workers,
            descricao="recorte",
            ao_concluir=agregar,
            reter_resultados=reter_resultados,
            pasta_saida=str(pasta_saida),
            formato=formato,
            perfil=opcoes.perfil,
            lote_id=sumario.lote_id,
            pular_existentes=opcoes.pular_existentes,
            # So movemos para _falhas o que esta numa pasta intermediaria
            # nossa. Lendo direto da entrada do usuario, a foto problematica
            # fica onde esta - o registro em _falhas ja documenta o caso.
            mover_falhas=opcoes.materializa_renomeadas,
        )

    sumario.pasta_saida = str(pasta_saida)
    return resultados


# ---------------------------------------------------------------------------
# Execucao completa
# ---------------------------------------------------------------------------


def executar_processamento(opcoes: OpcoesProcessamento | None = None,
                           **kwargs) -> SumarioLote:
    """
    Roda nomeacao + recorte e devolve o SumarioLote.

    E o unico ponto de entrada que o sistema chamador precisa conhecer:

        executar_processamento(OpcoesProcessamento(modo="sequencial"))
        executar_processamento(modo="grid", pasta_entrada="D:/lote_07")

    Levanta excecao apenas em falhas estruturais (pasta inexistente, lote
    vazio). Falhas de fotos individuais sao registradas no sumario e nao
    interrompem a execucao.
    """
    if opcoes is None:
        opcoes = OpcoesProcessamento(**kwargs)
    elif kwargs:
        opcoes = opcoes.com(**kwargs)

    inicio = time.perf_counter()
    lote_id = opcoes.lote_id or novo_lote_id()
    sumario = SumarioLote(lote_id=lote_id, modo=opcoes.modo,
                          parametros=opcoes.to_dict())

    garantir_pastas(opcoes.pasta_recortadas, settings.PASTA_FALHAS,
                    settings.PASTA_RELATORIOS, settings.PASTA_LOGS)
    if opcoes.materializa_renomeadas:
        garantir_pastas(opcoes.pasta_renomeadas)

    logger.info("=" * 68)
    logger.info("INICIO DO LOTE %s", lote_id)
    for linha in opcoes.linhas_resumo():
        logger.info(linha)
    logger.info("=" * 68)

    try:
        if not opcoes.pasta_entrada.exists():
            raise FileNotFoundError(
                f"Pasta de entrada nao existe: {opcoes.pasta_entrada}")

        with Cronometro("Varredura da pasta de entrada", logger):
            arquivos = listar_imagens(opcoes.pasta_entrada, limite=opcoes.limite)
        if not arquivos:
            raise ValueError(f"Nenhuma imagem encontrada em {opcoes.pasta_entrada}")
        logger.info("%d imagem(ns) na entrada.", len(arquivos))

        nomeacao = etapa_nomeacao(sumario, opcoes, arquivos)
        if not nomeacao.itens:
            raise ValueError("Nenhuma foto sobreviveu a etapa de nomeacao.")

        if opcoes.modo == settings.MODO_GRID and len(nomeacao.itens) != settings.QUANTIDADE_ESPERADA:
            logger.warning("Lote com %d foto(s) - o grid tem %d posicoes.",
                           len(nomeacao.itens), settings.QUANTIDADE_ESPERADA)

        if opcoes.simular:
            extensao = extensao_do_formato(opcoes.formato)
            logger.info("SIMULACAO: %d foto(s) seriam recortadas para %s.",
                        len(nomeacao.itens), opcoes.pasta_recortadas)
            for caminho, nome in nomeacao.itens[:10]:
                logger.info("  %s -> %s%s", descrever_item(caminho), nome,
                            extensao)
            sumario.renomeadas = len(nomeacao.itens)
            sumario.sucesso = True
        else:
            etapa_recorte(sumario, nomeacao.itens, opcoes)
            # sucesso = o lote rodou ate o fim e produziu quadrantes. A
            # qualidade do lote esta em total_falhas / sem_falhas: uma foto
            # ruim entre milhares nao invalida o processamento.
            sumario.sucesso = (sumario.recortadas_ok + sumario.puladas) > 0
            if sumario.recortadas_falha:
                logger.warning("Lote concluido com %d falha(s) de %d foto(s).",
                               sumario.recortadas_falha, len(nomeacao.itens))
    except Exception as exc:
        logger.exception("LOTE %s ABORTADO: %s", lote_id, exc)
        sumario.adicionar_falha(None, "pipeline", f"{type(exc).__name__}: {exc}")
        sumario.sucesso = False
    finally:
        sumario.duracao_seg = time.perf_counter() - inicio
        sumario.memoria_pico_mb = medir_memoria_mb()
        for linha in sumario.linhas_relatorio():
            logger.info(linha)
        if not opcoes.simular:
            try:
                caminho = sumario.salvar_json(
                    Path(settings.PASTA_RELATORIOS) / lote_id / "sumario.json")
                logger.info("Sumario salvo em %s", caminho)
            except Exception as exc:  # pragma: no cover - defensivo
                logger.error("Nao foi possivel salvar o sumario: %s", exc)

        if opcoes.notificar:
            notificar_webhook(sumario)

    return sumario


def processar_pasta(pasta_entrada, modo: str | None = None, **kwargs) -> SumarioLote:
    """Atalho: `processar_pasta("D:/envio_42", modo="sequencial")`."""
    return executar_processamento(
        OpcoesProcessamento(pasta_entrada=pasta_entrada,
                            modo=modo or settings.MODO_PADRAO, **kwargs))


# ---------------------------------------------------------------------------
# Compatibilidade com a API anterior
# ---------------------------------------------------------------------------


def executar_pipeline_completo(
    pasta_entrada=None,
    lote_id: str | None = None,
    num_workers: int | None = None,
    formato_recorte: str | None = None,
    criar_zip: bool | None = None,
    pular_renomeacao: bool = False,
    notificar: bool | None = None,
    **kwargs,
) -> SumarioLote:
    """
    Assinatura antiga (modo grid). Mantida para nao quebrar integracoes.

    A etapa de stitching nao existe mais: o lote termina nos quadrantes
    recortados.
    """
    opcoes = OpcoesProcessamento(
        modo=settings.MODO_RECORTE if pular_renomeacao else settings.MODO_GRID,
        pasta_entrada=pasta_entrada,
        lote_id=lote_id,
        workers=num_workers,
        formato=formato_recorte,
        criar_zip=criar_zip,
        notificar=notificar,
        **kwargs,
    )
    return executar_processamento(opcoes)
