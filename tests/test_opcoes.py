"""
Testes do contrato de entrada (OpcoesProcessamento).

E a porta pela qual o sistema maior conversa com o pipeline: entrada invalida
tem que estourar AQUI, com mensagem util, e nao la dentro de um worker no
meio de um lote de 2.000 fotos.
"""

from __future__ import annotations

import pytest

from config import settings
from src.exportacao import FORMATOS_VALIDOS
from src.opcoes import OpcoesProcessamento


def test_defaults_vem_de_settings():
    opcoes = OpcoesProcessamento(armadilha=14)
    assert opcoes.modo == settings.MODO_PADRAO
    assert opcoes.pasta_entrada == settings.PASTA_ENTRADA
    assert opcoes.pasta_recortadas == settings.PASTA_RECORTADAS
    assert opcoes.formato == settings.RECORTE_FORMATO_SAIDA
    assert opcoes.perfil == settings.RECORTE_PERFIL_PADRAO


def test_nenhum_modo_duplica_o_lote_por_padrao():
    """Uma foto de entrada = um arquivo de saida, em qualquer modo."""
    for modo in settings.MODOS_VALIDOS:
        opcoes = OpcoesProcessamento(modo=modo, armadilha=14)
        assert opcoes.estrategia_renomeacao == "virtual", modo
        assert opcoes.materializa_renomeadas is False, modo
        assert opcoes.duplica_em_disco is False, modo


def test_duplicacao_so_acontece_se_for_pedida():
    assert OpcoesProcessamento(estrategia_renomeacao="copiar",
                               armadilha=14).duplica_em_disco is True
    assert OpcoesProcessamento(estrategia_renomeacao="hardlink",
                               armadilha=14).duplica_em_disco is True
    # 'mover' materializa 02_renomeadas, mas realoca o arquivo em vez de copiar.
    mover = OpcoesProcessamento(estrategia_renomeacao="mover", armadilha=14)
    assert mover.materializa_renomeadas is True
    assert mover.duplica_em_disco is False


def test_origem_do_recorte_segue_a_estrategia():
    virtual = OpcoesProcessamento(modo="sequencial")
    assert virtual.pasta_origem_do_recorte == virtual.pasta_entrada

    copiando = OpcoesProcessamento(modo="sequencial",
                                   estrategia_renomeacao="copiar")
    assert copiando.pasta_origem_do_recorte == copiando.pasta_renomeadas


def test_modo_recorte_nao_renomeia():
    assert OpcoesProcessamento(modo="recorte").renomeia is False
    assert OpcoesProcessamento(modo="grid", armadilha=14).renomeia is True


@pytest.mark.parametrize("campo,valor", [
    ("modo", "montar_placa"),
    ("formato", "webp"),
    ("estrategia_renomeacao", "teletransporte"),
    ("perfil", "verde"),
    ("limite", 0),
    ("digitos", 0),
])
def test_valores_invalidos_estouram_na_construcao(campo, valor):
    with pytest.raises(ValueError):
        OpcoesProcessamento(armadilha=14, **{campo: valor})


# ---------------------------------------------------------------------------
# Numero da armadilha (VARD14A1 ... VARD14H10)
# ---------------------------------------------------------------------------


def test_grid_exige_o_numero_da_armadilha():
    """
    O nome do quadrante carrega a armadilha (VARD14G9). Sem ela o lote
    inteiro sairia sem rastreio - tem que estourar na porta de entrada, e
    nao depois de 80 recortes gravados.
    """
    with pytest.raises(ValueError, match="armadilha"):
        OpcoesProcessamento(modo="grid")


@pytest.mark.parametrize("valor", [0, -3])
def test_numero_da_armadilha_precisa_ser_positivo(valor):
    with pytest.raises(ValueError, match="armadilha"):
        OpcoesProcessamento(modo="grid", armadilha=valor)


def test_armadilha_aceita_numero_em_texto():
    """Vindo de formulario, fila ou n8n o campo chega como string."""
    assert OpcoesProcessamento(modo="grid", armadilha="14").armadilha == 14


@pytest.mark.parametrize("modo", ["sequencial", "recorte"])
def test_modos_fora_do_grid_nao_exigem_armadilha(modo):
    """So o grid nomeia por quadrante - os outros modos seguem sem o numero."""
    assert OpcoesProcessamento(modo=modo).armadilha is None


def test_resumo_do_lote_mostra_a_faixa_de_nomes_do_grid():
    """Quem le o log confere de cara qual armadilha o lote esta gravando."""
    linhas = " ".join(
        OpcoesProcessamento(modo="grid", armadilha=14).linhas_resumo())
    assert "VARD14A1" in linhas and "VARD14H10" in linhas


def test_perfil_da_armadilha_e_normalizado():
    """A cor pode chegar de um formulario ou de um JSON - aceita ' AZUL '."""
    assert OpcoesProcessamento(perfil=" AZUL ", armadilha=14).perfil == "azul"


def test_perfil_aparece_no_resumo_do_lote():
    """
    Quem le o log precisa saber com que perfil o lote rodou: e a primeira
    coisa a conferir quando um lote sai com recorte estranho.
    """
    linhas = " ".join(
        OpcoesProcessamento(perfil="azul", armadilha=14).linhas_resumo())
    assert "Armadilha" in linhas and "azul" in linhas


def test_limpar_saida_com_retomada_e_contraditorio():
    """Uma opcao apagaria exatamente o que a outra usaria."""
    with pytest.raises(ValueError, match="se anulam"):
        OpcoesProcessamento(limpar_saida=True, pular_existentes=True,
                            armadilha=14)


def test_com_clona_e_revalida():
    base = OpcoesProcessamento(modo="grid", armadilha=14)
    clone = base.com(modo="sequencial")
    assert base.modo == "grid"
    assert clone.modo == "sequencial"
    # a estrategia default acompanha o novo modo
    assert clone.estrategia_renomeacao == "virtual"


def test_com_respeita_estrategia_escolhida_a_mao():
    base = OpcoesProcessamento(modo="grid", armadilha=14,
                               estrategia_renomeacao="hardlink")
    assert base.com(modo="sequencial").estrategia_renomeacao == "hardlink"


def test_to_dict_serializa_caminhos():
    dados = OpcoesProcessamento(armadilha=14, contexto={"job": 42}).to_dict()
    assert isinstance(dados["pasta_entrada"], str)
    assert dados["contexto"] == {"job": 42}


def test_formatos_validos_batem_com_a_exportacao():
    """settings e exportacao nao podem divergir sobre os formatos aceitos."""
    assert set(settings.RECORTE_FORMATOS_VALIDOS) == set(FORMATOS_VALIDOS)
