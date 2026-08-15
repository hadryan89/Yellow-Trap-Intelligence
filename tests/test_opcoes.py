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
    opcoes = OpcoesProcessamento()
    assert opcoes.modo == settings.MODO_PADRAO
    assert opcoes.pasta_entrada == settings.PASTA_ENTRADA
    assert opcoes.pasta_recortadas == settings.PASTA_RECORTADAS
    assert opcoes.formato == settings.RECORTE_FORMATO_SAIDA


def test_estrategia_default_depende_do_modo():
    """Grid mantem a copia com MD5; sequencial nao materializa nada."""
    assert OpcoesProcessamento(modo="grid").estrategia_renomeacao == "copiar"
    assert OpcoesProcessamento(modo="sequencial").estrategia_renomeacao == "virtual"
    assert OpcoesProcessamento(modo="sequencial").materializa_renomeadas is False
    assert OpcoesProcessamento(modo="grid").materializa_renomeadas is True


def test_origem_do_recorte_segue_a_estrategia():
    virtual = OpcoesProcessamento(modo="sequencial")
    assert virtual.pasta_origem_do_recorte == virtual.pasta_entrada

    copiando = OpcoesProcessamento(modo="sequencial",
                                   estrategia_renomeacao="copiar")
    assert copiando.pasta_origem_do_recorte == copiando.pasta_renomeadas


def test_modo_recorte_nao_renomeia():
    assert OpcoesProcessamento(modo="recorte").renomeia is False
    assert OpcoesProcessamento(modo="grid").renomeia is True


@pytest.mark.parametrize("campo,valor", [
    ("modo", "montar_placa"),
    ("formato", "webp"),
    ("estrategia_renomeacao", "teletransporte"),
    ("limite", 0),
    ("digitos", 0),
])
def test_valores_invalidos_estouram_na_construcao(campo, valor):
    with pytest.raises(ValueError):
        OpcoesProcessamento(**{campo: valor})


def test_limpar_saida_com_retomada_e_contraditorio():
    """Uma opcao apagaria exatamente o que a outra usaria."""
    with pytest.raises(ValueError, match="se anulam"):
        OpcoesProcessamento(limpar_saida=True, pular_existentes=True)


def test_com_clona_e_revalida():
    base = OpcoesProcessamento(modo="grid")
    clone = base.com(modo="sequencial")
    assert base.modo == "grid"
    assert clone.modo == "sequencial"
    # a estrategia default acompanha o novo modo
    assert clone.estrategia_renomeacao == "virtual"


def test_com_respeita_estrategia_escolhida_a_mao():
    base = OpcoesProcessamento(modo="grid", estrategia_renomeacao="hardlink")
    assert base.com(modo="sequencial").estrategia_renomeacao == "hardlink"


def test_to_dict_serializa_caminhos():
    dados = OpcoesProcessamento(contexto={"job": 42}).to_dict()
    assert isinstance(dados["pasta_entrada"], str)
    assert dados["contexto"] == {"job": 42}


def test_formatos_validos_batem_com_a_exportacao():
    """settings e exportacao nao podem divergir sobre os formatos aceitos."""
    assert set(settings.RECORTE_FORMATOS_VALIDOS) == set(FORMATOS_VALIDOS)
