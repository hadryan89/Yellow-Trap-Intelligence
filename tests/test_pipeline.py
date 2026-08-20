"""
Testes de integracao: nomeacao + recorte, paralelismo, modos e falhas.

Nota sobre paralelismo nos testes: no Windows o ProcessPoolExecutor usa
'spawn', e os workers reimportam config.settings do zero - por isso o
monkeypatch das pastas nao chega neles. Os testes que dependem de pastas
isoladas rodam com workers=1 (caminho sequencial, mesmo codigo); ha testes
dedicados exercitando o caminho multiprocesso de verdade.
"""

from __future__ import annotations

import json
import os

import pytest

from config import settings
from src.opcoes import OpcoesProcessamento
from src.paralelismo import executar_em_paralelo, resolver_num_workers
from src.pipeline import executar_pipeline_completo, executar_processamento
from src.recorte import processar_foto, processar_item


def _povoar_entrada(pasta, foto_modelo, quantidade=40, prefixo="IMG"):
    """Copia a mesma foto sintetica N vezes com nomes sequenciais."""
    pasta.mkdir(parents=True, exist_ok=True)
    dados = foto_modelo.read_bytes()
    for i in range(1, quantidade + 1):
        (pasta / f"{prefixo}_{i:03d}.png").write_bytes(dados)


def _opcoes(pastas, **kwargs) -> OpcoesProcessamento:
    """Opcoes apontando para as pastas isoladas, sempre com 1 worker."""
    kwargs.setdefault("workers", 1)
    kwargs.setdefault("pasta_entrada", pastas["PASTA_ENTRADA"])
    kwargs.setdefault("pasta_recortadas", pastas["PASTA_RECORTADAS"])
    kwargs.setdefault("pasta_renomeadas", pastas["PASTA_RENOMEADAS"])
    return OpcoesProcessamento(**kwargs)


# ---------------------------------------------------------------------------
# Paralelismo
# ---------------------------------------------------------------------------


def test_resolver_num_workers_deixa_um_core_livre(monkeypatch):
    monkeypatch.setattr(settings, "NUM_WORKERS", None)
    monkeypatch.setattr("os.cpu_count", lambda: 8)
    assert resolver_num_workers() == 7
    assert resolver_num_workers(3) == 3
    assert resolver_num_workers(0) == 1  # nunca zero


def test_paralelismo_preserva_a_ordem_dos_resultados(tmp_path, foto_valida):
    """A lista de saida tem que seguir a ordem da lista de entrada."""
    _povoar_entrada(tmp_path, foto_valida, quantidade=6, prefixo="ORD")
    entradas = sorted(str(p) for p in tmp_path.glob("ORD_*.png"))
    saida = tmp_path / "recortes"

    resultados = executar_em_paralelo(
        processar_foto, entradas, num_workers=2, descricao="teste",
        pasta_saida=str(saida), formato="png",
    )

    assert [r["arquivo"] for r in resultados] == [os.path.basename(p) for p in entradas]
    assert all(r["sucesso"] for r in resultados)
    assert len(list(saida.glob("*.png"))) == 6


def test_uma_foto_ruim_nao_derruba_o_lote(tmp_path, foto_valida):
    """Requisito: o pipeline nunca aborta por causa de 1 foto."""
    _povoar_entrada(tmp_path, foto_valida, quantidade=3, prefixo="OK")
    ruim = tmp_path / "OK_004.png"
    ruim.write_bytes(b"nao sou uma imagem")
    entradas = sorted(str(p) for p in tmp_path.glob("OK_*.png"))

    resultados = executar_em_paralelo(
        processar_foto, entradas, num_workers=2, descricao="teste",
        pasta_saida=str(tmp_path / "recortes"), formato="png",
    )

    assert len(resultados) == 4
    assert sum(1 for r in resultados if r["sucesso"]) == 3
    falha = next(r for r in resultados if not r["sucesso"])
    assert falha["arquivo"] == "OK_004.png"
    assert falha["erro"]


def test_lista_vazia_nao_quebra():
    assert executar_em_paralelo(processar_foto, [], num_workers=2) == []


def test_streaming_nao_acumula_resultados(tmp_path, foto_valida):
    """
    Com reter_resultados=False a memoria fica constante: nada volta na lista,
    o chamador consome cada item pelo callback.
    """
    _povoar_entrada(tmp_path, foto_valida, quantidade=5, prefixo="STR")
    entradas = sorted(str(p) for p in tmp_path.glob("STR_*.png"))
    vistos = []

    resultados = executar_em_paralelo(
        processar_item, entradas, num_workers=2, descricao="teste",
        ao_concluir=lambda i, r: vistos.append(i),
        reter_resultados=False,
        pasta_saida=str(tmp_path / "recortes"), formato="png",
    )

    assert resultados == []
    assert sorted(vistos) == list(range(5))


def test_janela_de_submissao_processa_tudo(tmp_path, foto_valida):
    """A janela limita as tarefas em voo - nenhum item pode ficar de fora."""
    _povoar_entrada(tmp_path, foto_valida, quantidade=9, prefixo="JAN")
    entradas = sorted(str(p) for p in tmp_path.glob("JAN_*.png"))

    resultados = executar_em_paralelo(
        processar_item, entradas, num_workers=2, descricao="teste", janela=2,
        pasta_saida=str(tmp_path / "recortes"), formato="png",
    )

    assert len(resultados) == 9
    assert all(r["sucesso"] for r in resultados)


def test_processar_item_aceita_par_caminho_nome(tmp_path, foto_valida):
    """A fila do pipeline carrega (caminho, nome_de_saida)."""
    _povoar_entrada(tmp_path, foto_valida, quantidade=2, prefixo="PAR")
    saida = tmp_path / "recortes"
    itens = [(str(p), f"VARD{i:07d}")
             for i, p in enumerate(sorted(tmp_path.glob("PAR_*.png")), start=1)]

    resultados = executar_em_paralelo(
        processar_item, itens, num_workers=2, descricao="teste",
        pasta_saida=str(saida), formato="png",
    )

    assert all(r["sucesso"] for r in resultados)
    assert (saida / "VARD0000001.png").exists()
    assert (saida / "VARD0000002.png").exists()


# ---------------------------------------------------------------------------
# Nao duplicar o lote em disco
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("modo", ["grid", "sequencial", "recorte"])
def test_n_fotos_entram_n_arquivos_saem(pastas_isoladas, foto_valida, modo):
    """
    Regressao: o lote nao pode ser replicado pelas pastas do pipeline.

    30 fotos na entrada tem que produzir 30 quadrantes em 03_recortadas e
    NADA em 02_renomeadas - em qualquer um dos tres modos. Antes, o modo
    grid materializava uma copia byte-a-byte por foto e o mesmo lote passava
    a ocupar disco duas vezes.
    """
    entrada = pastas_isoladas["PASTA_ENTRADA"]
    _povoar_entrada(entrada, foto_valida, quantidade=30)

    sumario = executar_processamento(
        _opcoes(pastas_isoladas, modo=modo, lote_id=f"LOTE_1X_{modo}"))

    assert sumario.sucesso is True
    assert sumario.recortadas_ok == 30
    assert sumario.arquivos_intermediarios == 0

    # Saida: exatamente uma imagem por foto de entrada.
    saida = list(pastas_isoladas["PASTA_RECORTADAS"].glob("*.png"))
    assert len(saida) == 30
    assert len({p.name for p in saida}) == 30

    # Nenhuma copia intermediaria e a entrada intacta.
    assert list(pastas_isoladas["PASTA_RENOMEADAS"].iterdir()) == []
    assert len(list(entrada.glob("*.png"))) == 30


# ---------------------------------------------------------------------------
# Modo grid (comportamento historico)
# ---------------------------------------------------------------------------


@pytest.mark.lento
def test_modo_grid_ponta_a_ponta(pastas_isoladas, foto_valida):
    """40 fotos brutas entram e saem 40 quadrantes a1..d10 recortados."""
    _povoar_entrada(pastas_isoladas["PASTA_ENTRADA"], foto_valida, quantidade=40)

    sumario = executar_processamento(_opcoes(pastas_isoladas,
                                             modo="grid", lote_id="LOTE_TESTE"))

    assert sumario.sucesso is True
    assert sumario.total_entrada == 40
    assert sumario.renomeadas == 40
    assert sumario.recortadas_ok == 40
    assert sumario.recortadas_sem_deteccao == 0
    assert sumario.total_falhas == 0

    recortadas = pastas_isoladas["PASTA_RECORTADAS"]
    assert (recortadas / "a1.png").exists() and (recortadas / "d10.png").exists()
    assert len(list(recortadas.glob("*.png"))) == 40

    # Estrategia virtual (padrao): 02_renomeadas nao e escrita - as 40 fotos
    # de entrada geram 40 arquivos novos, e nao 80.
    assert list(pastas_isoladas["PASTA_RENOMEADAS"].iterdir()) == []
    assert sumario.arquivos_intermediarios == 0

    relatorio = pastas_isoladas["PASTA_RELATORIOS"] / "LOTE_TESTE" / "sumario.json"
    assert relatorio.exists()
    assert json.loads(relatorio.read_text(encoding="utf-8"))["modo"] == "grid"


def test_modo_grid_com_lote_incompleto(pastas_isoladas, foto_valida):
    """Menos fotos que posicoes: processa o que veio, sem nada ignorado."""
    _povoar_entrada(pastas_isoladas["PASTA_ENTRADA"], foto_valida, quantidade=3)
    sumario = executar_processamento(_opcoes(pastas_isoladas, modo="grid",
                                             lote_id="LOTE_PARCIAL"))

    assert sumario.ignoradas == 0
    assert sumario.recortadas_ok == 3
    nomes = sorted(p.name for p in pastas_isoladas["PASTA_RECORTADAS"].glob("*.png"))
    assert nomes == ["a1.png", "a2.png", "a3.png"]


@pytest.mark.lento
def test_modo_grid_com_mais_fotos_que_posicoes(pastas_isoladas, foto_valida,
                                               monkeypatch):
    """Sobra de fotos vira falha registrada, nunca descarte silencioso."""
    monkeypatch.setattr(settings, "LETRAS_COLUNAS", ["a"])
    monkeypatch.setattr(settings, "NUMEROS_LINHAS", [1, 2])
    _povoar_entrada(pastas_isoladas["PASTA_ENTRADA"], foto_valida, quantidade=5)

    sumario = executar_processamento(_opcoes(pastas_isoladas, modo="grid",
                                             lote_id="LOTE_SOBRA"))

    assert sumario.total_entrada == 5
    assert sumario.renomeadas == 2
    assert sumario.ignoradas == 3
    assert any(f["etapa"] == "nomeacao" for f in sumario.falhas)


# ---------------------------------------------------------------------------
# Modo sequencial (VARD1, VARD2, ...)
# ---------------------------------------------------------------------------


def test_modo_sequencial_renomeia_e_recorta(pastas_isoladas, foto_valida):
    """Renomeia para VARD1, VARD2... e recorta, sem materializar 02_renomeadas."""
    _povoar_entrada(pastas_isoladas["PASTA_ENTRADA"], foto_valida,
                    quantidade=12, prefixo="DSC")

    sumario = executar_processamento(_opcoes(pastas_isoladas, modo="sequencial",
                                             lote_id="LOTE_SEQ"))

    assert sumario.sucesso is True
    assert sumario.recortadas_ok == 12

    recortadas = pastas_isoladas["PASTA_RECORTADAS"]
    # A numeracao comeca em 1 e nao leva zeros a esquerda: VARD1 .. VARD12.
    nomes = {p.name for p in recortadas.glob("*.png")}
    assert nomes == {f"VARD{i}.png" for i in range(1, 13)}
    assert "VARD0.png" not in nomes

    # Estrategia virtual: nada foi copiado para 02_renomeadas...
    assert list(pastas_isoladas["PASTA_RENOMEADAS"].iterdir()) == []
    # ...e a entrada continua intacta.
    assert len(list(pastas_isoladas["PASTA_ENTRADA"].glob("*.png"))) == 12


def test_modo_sequencial_respeita_a_ordem_natural(pastas_isoladas, foto_valida):
    """DSC_2 tem que virar VARD2, e nao o ultimo numero do lote."""
    entrada = pastas_isoladas["PASTA_ENTRADA"]
    dados = foto_valida.read_bytes()
    for nome in ["DSC_10.png", "DSC_2.png", "DSC_1.png"]:
        (entrada / nome).write_bytes(dados)

    executar_processamento(_opcoes(pastas_isoladas, modo="sequencial",
                                   estrategia_renomeacao="copiar",
                                   lote_id="LOTE_ORDEM"))

    renomeadas = pastas_isoladas["PASTA_RENOMEADAS"]
    assert (renomeadas / "VARD1.png").exists()
    assert (renomeadas / "VARD2.png").exists()
    assert (renomeadas / "VARD3.png").exists()
    # A ordem natural garante o de-para; conferido pelos bytes de origem.
    assert (renomeadas / "VARD3.png").read_bytes() == \
           (entrada / "DSC_10.png").read_bytes()


def test_modo_sequencial_continua_a_numeracao(pastas_isoladas, foto_valida):
    """Segundo envio nao pode sobrescrever o acervo do primeiro."""
    entrada = pastas_isoladas["PASTA_ENTRADA"]
    _povoar_entrada(entrada, foto_valida, quantidade=3, prefixo="A")
    executar_processamento(_opcoes(pastas_isoladas, modo="sequencial",
                                   lote_id="ENVIO_1"))

    for arquivo in entrada.glob("*.png"):
        arquivo.unlink()
    _povoar_entrada(entrada, foto_valida, quantidade=2, prefixo="B")
    executar_processamento(_opcoes(pastas_isoladas, modo="sequencial",
                                   continuar_numeracao=True, lote_id="ENVIO_2"))

    nomes = {p.name for p in pastas_isoladas["PASTA_RECORTADAS"].glob("*.png")}
    assert nomes == {f"VARD{i}.png" for i in range(1, 6)}


def test_modo_sequencial_com_limite(pastas_isoladas, foto_valida):
    _povoar_entrada(pastas_isoladas["PASTA_ENTRADA"], foto_valida, quantidade=10)
    sumario = executar_processamento(_opcoes(pastas_isoladas, modo="sequencial",
                                             limite=4, lote_id="LOTE_LIMITE"))
    assert sumario.total_entrada == 4
    assert sumario.recortadas_ok == 4


# ---------------------------------------------------------------------------
# Modo recorte (sem renomear)
# ---------------------------------------------------------------------------


def test_modo_recorte_preserva_o_nome_de_origem(pastas_isoladas, foto_valida):
    _povoar_entrada(pastas_isoladas["PASTA_ENTRADA"], foto_valida,
                    quantidade=3, prefixo="ORIG")
    sumario = executar_processamento(_opcoes(pastas_isoladas, modo="recorte",
                                             lote_id="LOTE_NOMES"))

    assert sumario.recortadas_ok == 3
    nomes = sorted(p.name for p in pastas_isoladas["PASTA_RECORTADAS"].glob("*.png"))
    assert nomes == ["ORIG_001.png", "ORIG_002.png", "ORIG_003.png"]


# ---------------------------------------------------------------------------
# O RECORTE E O MESMO EM QUALQUER MODO
# ---------------------------------------------------------------------------


def test_recorte_identico_em_todos_os_modos(pastas_isoladas, foto_valida,
                                            tmp_path):
    """
    Blindagem do requisito central: mudar o modo muda o NOME do arquivo e
    nada mais. Os bytes do quadrante tem que ser identicos nos tres modos e
    iguais ao do recorte avulso.
    """
    entrada = pastas_isoladas["PASTA_ENTRADA"]
    _povoar_entrada(entrada, foto_valida, quantidade=1, prefixo="UNICA")

    saidas = {}
    for modo, nome in (("grid", "a1.png"), ("sequencial", "VARD1.png"),
                       ("recorte", "UNICA_001.png")):
        pasta = tmp_path / f"saida_{modo}"
        executar_processamento(_opcoes(pastas_isoladas, modo=modo,
                                       pasta_recortadas=pasta,
                                       lote_id=f"LOTE_{modo}"))
        saidas[modo] = (pasta / nome).read_bytes()

    avulso = tmp_path / "avulso"
    processar_foto(str(entrada / "UNICA_001.png"), pasta_saida=avulso,
                   formato="png")
    referencia = (avulso / "UNICA_001.png").read_bytes()

    assert saidas["grid"] == referencia
    assert saidas["sequencial"] == referencia
    assert saidas["recorte"] == referencia


# ---------------------------------------------------------------------------
# Robustez e escala
# ---------------------------------------------------------------------------


def test_retomada_pula_o_que_ja_existe(pastas_isoladas, foto_valida):
    """Reprocessar um lote interrompido custa so o que faltava."""
    _povoar_entrada(pastas_isoladas["PASTA_ENTRADA"], foto_valida, quantidade=4,
                    prefixo="RET")

    primeiro = executar_processamento(_opcoes(pastas_isoladas, modo="sequencial",
                                              lote_id="RET_1"))
    assert primeiro.recortadas_ok == 4

    segundo = executar_processamento(_opcoes(pastas_isoladas, modo="sequencial",
                                             pular_existentes=True,
                                             lote_id="RET_2"))
    assert segundo.puladas == 4
    assert segundo.recortadas_ok == 0
    assert segundo.sucesso is True


def test_foto_corrompida_no_meio_do_lote(pastas_isoladas, foto_valida):
    """Uma foto ruim entra no sumario e o resto do lote termina."""
    entrada = pastas_isoladas["PASTA_ENTRADA"]
    _povoar_entrada(entrada, foto_valida, quantidade=3, prefixo="MIX")
    (entrada / "MIX_004.png").write_bytes(b"nao sou uma imagem")

    sumario = executar_processamento(_opcoes(pastas_isoladas, modo="sequencial",
                                             lote_id="LOTE_MIX"))

    assert sumario.recortadas_ok == 3
    assert sumario.recortadas_falha == 1
    assert sumario.total_falhas == 1
    assert sumario.sucesso is True      # o lote rodou ate o fim...
    assert sumario.sem_falhas is False  # ...mas exige conferencia

    # Lendo direto da entrada (estrategia virtual), o arquivo do usuario nao
    # e movido: fica onde estava, e o motivo vai para _falhas em JSON.
    assert (entrada / "MIX_004.png").exists()
    jsons = list((pastas_isoladas["PASTA_FALHAS"] / "LOTE_MIX").glob("*.json"))
    assert len(jsons) == 1


def test_falha_no_grid_move_a_copia_e_preserva_o_original(pastas_isoladas,
                                                          foto_valida):
    """
    Com materializacao, quem vai para _falhas e a copia - nao o original.

    Materializar deixou de ser o default (duplicava o lote em disco), entao
    aqui a estrategia 'copiar' e pedida explicitamente.
    """
    entrada = pastas_isoladas["PASTA_ENTRADA"]
    _povoar_entrada(entrada, foto_valida, quantidade=1, prefixo="BOA")
    (entrada / "RUIM_001.png").write_bytes(b"nao sou uma imagem")

    sumario = executar_processamento(_opcoes(pastas_isoladas, modo="grid",
                                             estrategia_renomeacao="copiar",
                                             lote_id="LOTE_GRID_FALHA"))

    assert sumario.recortadas_falha == 1
    assert (entrada / "RUIM_001.png").exists(), "o original nao pode sumir"
    assert (pastas_isoladas["PASTA_FALHAS"] / "LOTE_GRID_FALHA" / "a2.png").exists()


def test_detalhe_das_falhas_e_limitado(pastas_isoladas, foto_valida, monkeypatch):
    """Um lote inteiro ruim nao pode gerar um sumario gigante."""
    monkeypatch.setattr(settings, "SUMARIO_MAX_FALHAS_DETALHADAS", 3)
    entrada = pastas_isoladas["PASTA_ENTRADA"]
    entrada.mkdir(parents=True, exist_ok=True)
    for i in range(8):
        (entrada / f"RUIM_{i:03d}.png").write_bytes(b"lixo")

    sumario = executar_processamento(_opcoes(pastas_isoladas, modo="sequencial",
                                             lote_id="LOTE_RUIM"))

    assert sumario.total_falhas == 8
    assert len(sumario.falhas) == 3
    assert sumario.to_dict()["falhas_omitidas"] == 5


def test_simular_nao_grava_nada(pastas_isoladas, foto_valida):
    _povoar_entrada(pastas_isoladas["PASTA_ENTRADA"], foto_valida, quantidade=3)
    sumario = executar_processamento(_opcoes(pastas_isoladas, modo="sequencial",
                                             simular=True, lote_id="LOTE_SIM"))

    assert sumario.sucesso is True
    assert list(pastas_isoladas["PASTA_RECORTADAS"].iterdir()) == []
    assert list(pastas_isoladas["PASTA_RENOMEADAS"].iterdir()) == []


def test_pipeline_sem_fotos_reporta_falha_sem_explodir(pastas_isoladas):
    """Pasta vazia: o lote falha de forma controlada, com sumario."""
    sumario = executar_processamento(_opcoes(pastas_isoladas,
                                             lote_id="LOTE_VAZIO"))
    assert sumario.sucesso is False
    assert sumario.total_falhas >= 1
    assert sumario.falhas[-1]["etapa"] == "pipeline"


def test_pasta_de_entrada_inexistente(pastas_isoladas, tmp_path):
    sumario = executar_processamento(_opcoes(
        pastas_isoladas, pasta_entrada=tmp_path / "nao_existe",
        lote_id="LOTE_404"))
    assert sumario.sucesso is False
    assert "FileNotFoundError" in sumario.falhas[-1]["motivo"]


def test_sumario_serializa_para_json(pastas_isoladas, foto_valida, tmp_path):
    _povoar_entrada(pastas_isoladas["PASTA_ENTRADA"], foto_valida, quantidade=2)
    sumario = executar_processamento(_opcoes(pastas_isoladas,
                                             lote_id="LOTE_JSON"))

    caminho = sumario.salvar_json(tmp_path / "s.json")
    dados = json.loads(caminho.read_text(encoding="utf-8"))
    assert dados["lote_id"] == "LOTE_JSON"
    assert {"duracao_seg", "falhas", "modo", "fotos_por_segundo"} <= set(dados)


# ---------------------------------------------------------------------------
# Compatibilidade da API antiga
# ---------------------------------------------------------------------------


def test_executar_pipeline_completo_ainda_funciona(pastas_isoladas, foto_valida):
    """A assinatura antiga continua valida - agora sem a etapa de stitching."""
    _povoar_entrada(pastas_isoladas["PASTA_ENTRADA"], foto_valida, quantidade=3)

    sumario = executar_pipeline_completo(lote_id="LOTE_COMPAT", num_workers=1)

    assert sumario.sucesso is True
    assert sumario.recortadas_ok == 3
    assert (pastas_isoladas["PASTA_RECORTADAS"] / "a1.png").exists()
